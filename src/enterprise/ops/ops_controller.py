from flask import Blueprint, request, jsonify
from firebase_admin import firestore
import time

ops_api = Blueprint('ops_controller', __name__)
db = firestore.client()

def require_super_admin(func):
    def wrapper(*args, **kwargs):
        role = request.headers.get('x-admin-role')
        if role != 'SUPER_ADMIN':
            return jsonify({'error': 'Unauthorized: Super Admin access required'}), 403
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@ops_api.route('/api/v2/ops/command_center/live', methods=['GET'])
@require_super_admin
def get_command_center_metrics():
    """
    Returns aggregated heavy metrics for the Operations Command Center.
    In production, this would query a pre-aggregated 'ops_metrics_realtime' document 
    maintained by Cloud Functions to avoid massive read costs.
    """
    doc_ref = db.collection('ops_metrics_realtime').document('global_stats')
    doc = doc_ref.get()
    
    if doc.exists:
        data = doc.to_dict()
    else:
        # Fallback empty state
        data = {
            'jobsSearching': 0,
            'jobsEnroute': 0,
            'jobsActive': 0,
            'jobsCompletedToday': 0,
            'jobsStuck': 0,
            'escrowLiabilityPaise': 0,
            'pendingWithdrawalsPaise': 0,
            'pendingKyc': 0,
            'activeDisputes': 0,
            'fraudAlerts': 0,
            'apiErrorRate': 0.01,
            'fcmDeliveryRate': 0.99
        }
        
    return jsonify(data), 200

@ops_api.route('/api/v2/ops/war_room', methods=['GET'])
@require_super_admin
def get_war_room_live():
    """
    Extremely fast, lightweight polling endpoint for the Launch War Room.
    Returns immediate, critical pulse metrics.
    """
    pulse = {
        'timestamp': int(time.time()),
        'activeUsers': 1205, # Mock pulse data from Redis sessions
        'activeJobs': 45,
        'paymentsTodayPaise': 4500000,
        'withdrawalsTodayPaise': 2000000,
        'criticalErrors': 0,
        'infrastructureHealth': 'GREEN'
    }
    
    return jsonify(pulse), 200
