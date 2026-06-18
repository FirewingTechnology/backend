from flask import Blueprint, request, jsonify
from firebase_admin import firestore

subscription_api = Blueprint('subscription_controller', __name__)
db = firestore.client()

@subscription_api.route('/api/v2/finance/webhook/razorpay_subscriptions', methods=['POST'])
def subscription_webhook():
    """ Handles Razorpay recurring billing webhooks. """
    signature = request.headers.get('x-razorpay-signature')
    
    # 1. Verify Signature (Re-using our P0-1 WebhookVerifier)
    from src.infrastructure.security.webhook_verifier import WebhookVerifier
    if not WebhookVerifier.verify_razorpay_signature(request.data, signature):
        return jsonify({"error": "Unauthorized"}), 401
        
    payload = request.json
    event = payload.get('event')
    sub_payload = payload.get('payload', {}).get('subscription', {}).get('entity', {})
    
    sub_id = sub_payload.get('id')
    user_uid = sub_payload.get('notes', {}).get('uid') # Passed during creation
    
    if not sub_id or not user_uid:
        return jsonify({"error": "Missing mapping data"}), 400
        
    ref = db.collection('user_subscriptions').document(user_uid)
    
    if event == 'subscription.charged':
        ref.set({
            'status': 'active',
            'subscriptionId': sub_id,
            'currentPeriodEnd': firestore.SERVER_TIMESTAMP, # Math done in Cloud Func
            'entitlements': ['priority_dispatch', 'zero_commission']
        }, merge=True)
    elif event in ['subscription.halted', 'subscription.cancelled']:
        ref.update({
            'status': 'expired',
            'entitlements': []
        })
        
    return jsonify({"status": "processed"}), 200
