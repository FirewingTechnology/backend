from flask import Blueprint, request, jsonify
from firebase_admin import firestore

referral_api = Blueprint('referral_controller', __name__)
db = firestore.client()

@referral_api.route('/api/v2/growth/referral/apply', methods=['POST'])
def apply_referral():
    """ Applies a referral code during signup, logging it immutably. """
    data = request.json
    new_user_uid = data.get('uid')
    referral_code = data.get('referralCode')
    device_id = data.get('deviceId')
    
    if not new_user_uid or not referral_code or not device_id:
        return jsonify({'error': 'Missing fields'}), 400
        
    # Find the owner of the code
    codes = db.collection('referral_codes').where('code', '==', referral_code).limit(1).stream()
    referrer_doc = next(codes, None)
    
    if not referrer_doc:
        return jsonify({'error': 'Invalid code'}), 404
        
    referrer_uid = referrer_doc.get('ownerUid')
    
    if referrer_uid == new_user_uid:
        return jsonify({'error': 'Cannot refer yourself'}), 400
        
    # Simple Device fingerprinting for fraud prevention
    fingerprint_ref = db.collection('device_fingerprints').document(device_id)
    if fingerprint_ref.get().exists:
        return jsonify({'error': 'Device already used for referral'}), 403
        
    # Create the pending event
    event_ref = db.collection('referral_events').document(new_user_uid)
    event_ref.set({
        'referrerUid': referrer_uid,
        'referredUid': new_user_uid,
        'status': 'pending_activation',
        'codeUsed': referral_code,
        'createdAt': firestore.SERVER_TIMESTAMP
    })
    
    # Log device
    fingerprint_ref.set({'uid': new_user_uid, 'timestamp': firestore.SERVER_TIMESTAMP})
    
    return jsonify({'status': 'applied', 'rewardPaise': 50000}), 200
