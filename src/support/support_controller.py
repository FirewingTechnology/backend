from flask import Blueprint, request, jsonify
from firebase_admin import firestore

support_api = Blueprint('support_controller', __name__)
db = firestore.client()

@support_api.route('/api/v2/support/tickets', methods=['POST'])
def create_ticket():
    data = request.json
    uid = data.get('uid')
    job_id = data.get('jobId')
    issue_type = data.get('issueType')
    description = data.get('description')
    
    if not uid or not issue_type:
        return jsonify({'error': 'Missing required fields'}), 400
        
    ticket = {
        'uid': uid,
        'jobId': job_id,
        'issueType': issue_type,
        'description': description,
        'status': 'open',
        'createdAt': firestore.SERVER_TIMESTAMP,
        'slaDeadline': firestore.SERVER_TIMESTAMP # In reality, add +24 hours
    }
    
    _, doc_ref = db.collection('support_tickets').add(ticket)
    
    # Send FCM notification to support team (topic: support_alerts)
    from src.infrastructure.firebase.fcm_service import FCMService
    try:
        FCMService.send_to_topic(
            topic="admin_support_alerts",
            data={"type": "NEW_TICKET", "ticketId": doc_ref.id},
            title="New Support Ticket",
            body=f"{issue_type} reported."
        )
    except Exception:
        pass # Non-critical failure
        
    return jsonify({'status': 'created', 'ticketId': doc_ref.id}), 201

@support_api.route('/api/v2/support/tickets/<ticket_id>/escalate', methods=['PUT'])
def escalate_ticket(ticket_id):
    """ Escalate to Level 2 Support (Finance / Trust & Safety) """
    ref = db.collection('support_tickets').document(ticket_id)
    if not ref.get().exists:
        return jsonify({'error': 'Not found'}), 404
        
    ref.update({
        'status': 'escalated',
        'escalatedAt': firestore.SERVER_TIMESTAMP,
        'assignedTier': 'L2'
    })
    
    return jsonify({'status': 'escalated'}), 200
