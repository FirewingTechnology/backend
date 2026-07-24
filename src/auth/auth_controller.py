import time
import re
import os
import jwt
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, current_app
from src.auth.twilio_verify_service import TwilioVerifyService

auth_api = Blueprint('auth_api', __name__)
verify_service = TwilioVerifyService()
JWT_SECRET = os.environ.get('JWT_SECRET', 'powersupply_jwt_secret_key_2026')

def normalize_e164(phone: str) -> str:
    """Normalizes any phone string into strict E.164 format (+[1-9]\\d{1,14})."""
    if not phone:
        return ''
    clean = re.sub(r'[^\d+]', '', str(phone).strip())
    if not clean:
        return ''
    if clean.startswith('+'):
        return clean
    if len(clean) == 10:
        return '+91' + clean
    if clean.startswith('91') and len(clean) == 12:
        return '+' + clean
    if clean.startswith('0') and len(clean) == 11:
        return '+91' + clean[1:]
    return '+' + clean

def is_valid_e164(phone: str) -> bool:
    return re.match(r'^\+[1-9]\d{1,14}$', phone) is not None

def _get_redis():
    return current_app.config.get('REDIS_CLIENT')

def _get_firestore():
    return current_app.config.get('FIRESTORE_DB')

def generate_jwt(user_id: str, phone: str, role: str = 'user') -> str:
    payload = {
        'sub': user_id,
        'phone': phone,
        'role': role,
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


@auth_api.route('/send-otp', methods=['POST'])
def send_otp():
    start_time = time.time()
    try:
        data = request.get_json() or {}
        raw_phone = data.get('phone') or data.get('phoneNumber') or ''
        phone = normalize_e164(raw_phone)

        if not is_valid_e164(phone):
            return jsonify({"success": False, "message": "Invalid phone number format. Must be E.164 (e.g. +919876543210)"}), 400

        redis_client = _get_redis()
        if redis_client:
            try:
                # 1. Check 30-second cooldown
                cooldown_key = f"cooldown:{phone}"
                if redis_client.get(cooldown_key):
                    return jsonify({"success": False, "message": "Please wait 30 seconds before requesting another OTP."}), 429

                # 2. Check 3 OTP requests per 10 minutes (600s)
                rate_key = f"ratelimit:send:{phone}"
                send_count = redis_client.incr(rate_key)
                if send_count == 1:
                    redis_client.expire(rate_key, 600)
                if send_count > 3:
                    return jsonify({"success": False, "message": "Too many OTP requests. Maximum 3 requests allowed per 10 minutes."}), 429

                # Set 30s cooldown
                redis_client.setex(cooldown_key, 30, "1")
            except Exception as re_err:
                print(f"[AuthController] Redis rate limiting error: {re_err}")

        # Send OTP via Twilio / Mock service
        result = verify_service.send_otp(phone)
        latency_ms = int((time.time() - start_time) * 1000)
        print(f"[AuthController] Send OTP to {phone[:6]}*** | Status: {result.get('success')} | Latency: {latency_ms}ms")

        if result.get("success"):
            return jsonify({"success": True, "message": "OTP Sent", "phoneNumber": phone}), 200
        else:
            return jsonify({"success": False, "message": result.get("message", "Failed to send OTP")}), 400

    except Exception as e:
        print(f"[AuthController] send_otp server error: {e}")
        return jsonify({"success": False, "message": "Server error processing OTP request."}), 500


@auth_api.route('/verify-otp', methods=['POST'])
def verify_otp():
    start_time = time.time()
    try:
        data = request.get_json() or {}
        raw_phone = data.get('phone') or data.get('phoneNumber') or ''
        otp_code = str(data.get('otp') or '').strip()
        phone = normalize_e164(raw_phone)

        if not is_valid_e164(phone) or not otp_code:
            return jsonify({"success": False, "verified": False, "message": "Phone number and OTP code are required."}), 400

        redis_client = _get_redis()
        if redis_client:
            try:
                # Check maximum 5 verification attempts per phone
                attempt_key = f"ratelimit:verify:{phone}"
                attempts = redis_client.incr(attempt_key)
                if attempts == 1:
                    redis_client.expire(attempt_key, 600)
                if attempts > 5:
                    return jsonify({"success": False, "verified": False, "message": "Maximum verification attempts exceeded. Please request a new OTP."}), 429
            except Exception as re_err:
                print(f"[AuthController] Redis verify attempt error: {re_err}")

        # Verify OTP
        res = verify_service.verify_otp(phone, otp_code)
        latency_ms = int((time.time() - start_time) * 1000)
        print(f"[AuthController] Verify OTP for {phone[:6]}*** | Verified: {res.get('verified')} | Latency: {latency_ms}ms")

        if not res.get("verified"):
            return jsonify({"success": False, "verified": False, "message": res.get("message", "Invalid OTP")}), 400

        # Success - Clear verify attempt rate limit
        if redis_client:
            try:
                redis_client.delete(f"ratelimit:verify:{phone}")
            except Exception:
                pass

        # Fetch or create User Profile in Firestore
        db = _get_firestore()
        user_id = phone.replace('+', '')
        user_data = {"uid": user_id, "phone": phone, "role": "user", "updatedAt": datetime.now(timezone.utc).isoformat()}
        
        if db:
            try:
                users_ref = db.collection('users').document(user_id)
                user_doc = users_ref.get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                else:
                    users_ref.set(user_data)
            except Exception as fs_err:
                print(f"[AuthController] Firestore user sync error: {fs_err}")

        token = generate_jwt(user_id, phone, role=user_data.get('role', 'user'))

        return jsonify({
            "success": True,
            "verified": True,
            "jwt": token,
            "user": user_data,
            "phoneNumber": phone
        }), 200

    except Exception as e:
        print(f"[AuthController] verify_otp server error: {e}")
        return jsonify({"success": False, "verified": False, "message": "Server error processing verification."}), 500
