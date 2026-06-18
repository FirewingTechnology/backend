from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError

payout_webhook_api = Blueprint('payout_webhook_api', __name__)

@payout_webhook_api.route('/api/v2/finance/webhook/razorpayx', methods=['POST'])
def razorpayx_webhook_receiver():
    signature = request.headers.get('x-razorpay-signature')
    
    # 1. Cryptographically Verify Webhook Origin
    from src.infrastructure.security.webhook_verifier import WebhookVerifier
    if not WebhookVerifier.verify_razorpay_signature(request.data, signature):
        return jsonify({"error": "Unauthorized: Invalid Webhook Signature"}), 401

    payload = request.json
    
    try:
        event = payload.get('event')
        payout = payload.get('payload', {}).get('payout', {}).get('entity', {})
        payout_id = payout.get('id')
        idempotency_key = payout.get('reference_id')
        # Amount in paise
        amount_paise = int(payout.get('amount', 0))
        # Custom logic to derive UID - usually stored in notes or DB lookup via idempotency_key
        # For spec completion, assuming idempotency_key maps to DB processing
    except Exception:
        return jsonify({"error": "Invalid payload structure"}), 400

    withdrawal_service = current_app.config.get('WITHDRAWAL_SERVICE')
    db = current_app.config.get('FIRESTORE_DB')
    
    if not withdrawal_service or not db:
        return jsonify({"error": "Service not configured."}), 500

    # Fetch UID from DB using idempotency_key
    try:
        doc = db.collection('processed_withdrawals').document(idempotency_key).get()
        if not doc.exists:
            return jsonify({"error": "Withdrawal record not found"}), 404
        uid = doc.get('uid')
    except Exception:
        return jsonify({"error": "DB lookup failed"}), 500

    try:
        if event == 'payout.processed':
            withdrawal_service.process_payout_success(payout_id, idempotency_key, uid, amount_paise)
        elif event in ['payout.failed', 'payout.rejected', 'payout.reversed']:
            reason = payout.get('status_details', {}).get('reason', 'Webhook Failure Event')
            withdrawal_service.process_payout_failure(payout_id, idempotency_key, uid, amount_paise, reason)
        return jsonify({"status": "acknowledged"}), 200
        
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent processing"}), 409
    except Exception as e:
        print(f"Payout Webhook error: {e}")
        return jsonify({"error": "Internal failure"}), 500
