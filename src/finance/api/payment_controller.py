from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError

payment_api = Blueprint('payment_api', __name__)

@payment_api.route('/api/v2/finance/webhook', methods=['POST'])
def webhook_receiver():
    payload = request.json
    
    # Example basic Razorpay webhook shape parsing
    try:
        payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
        payment_id = payment_entity.get('id')
        notes = payment_entity.get('notes', {})
        uid = notes.get('uid')
        # Razorpay sends amounts in paise by default as int/string
        amount_paise = int(payment_entity.get('amount', 0))
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Invalid payload structure"}), 400

    if not payment_id or not uid or amount_paise <= 0:
        return jsonify({"error": "Missing required fields"}), 400

    payment_service = current_app.config.get('PAYMENT_SERVICE')
    if not payment_service:
        return jsonify({"error": "Payment service not configured"}), 500

    # Signature verification
    signature = request.headers.get('x-razorpay-signature')
    from src.infrastructure.security.webhook_verifier import WebhookVerifier
    if not signature or not WebhookVerifier.verify_razorpay_signature(request.data, signature):
        return jsonify({"error": "Unauthorized: Invalid Signature"}), 401
    
    try:
        status = payment_service.process_webhook(payment_id, uid, amount_paise)
        return jsonify({"status": status}), 200
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent processing"}), 409
    except Exception as e:
        # Log exception in production
        print(f"Webhook error: {e}")
        return jsonify({"error": "Internal failure"}), 500
