from flask import Blueprint, request, jsonify
from firebase_admin import firestore

notification_api = Blueprint('notification_controller', __name__)
db = firestore.client()

@notification_api.route('/api/v2/notifications/track', methods=['POST'])
def track_delivery():
    """
    Logs delivery/opened receipts from Flutter FCM listeners to calculate delivery success rates.
    """
    data = request.json
    job_id = data.get('jobId')
    pro_uid = data.get('proUid')
    status = data.get('status') # 'DELIVERED' or 'OPENED'
    
    if not job_id or not pro_uid or not status:
        return jsonify({'error': 'Missing required fields'}), 400
        
    doc_id = f"{job_id}_{pro_uid}"
    ref = db.collection('notification_delivery_logs').document(doc_id)
    
    update_data = {
        'status': status,
        f'{status.lower()}At': firestore.SERVER_TIMESTAMP,
        'jobId': job_id,
        'proUid': pro_uid
    }
    
    ref.set(update_data, merge=True)
    return jsonify({'success': True}), 200

@notification_api.route('/api/v2/jobs/active_offers', methods=['GET'])
def get_active_offers():
    """
    Missed Job Recovery: Returns jobs within 5km that are still in 'searching' state.
    Used by Pro App on startup to recover missed FCM pings.
    """
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    
    if not lat or not lng:
        return jsonify({'error': 'lat and lng required'}), 400
        
    # In production, query Redis GEO for precise distance.
    # For architecture demo, we fallback to Firestore status query.
    query = db.collection('jobs').where('status', '==', 'searching').limit(5)
    docs = query.stream()
    
    offers = []
    for doc in docs:
        job = doc.to_dict()
        job['id'] = doc.id
        offers.append(job)
        
    return jsonify({'offers': offers}), 200
