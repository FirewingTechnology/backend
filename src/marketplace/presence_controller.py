import logging
from flask import Blueprint, request, jsonify, current_app
from firebase_admin import auth as firebase_auth
from src.marketplace.presence_service import PresenceService

logger = logging.getLogger(__name__)

presence_api = Blueprint('presence_api', __name__)

def require_pro_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "UNAUTHORIZED", "message": "Missing Bearer token"}), 401
        
        token = auth_header.split('Bearer ')[1]
        try:
            decoded_token = firebase_auth.verify_id_token(token)
            request.user = decoded_token
        except Exception as e:
            logger.warning(f"Auth token verification failed: {e}")
            return jsonify({"error": "FORBIDDEN", "message": "Invalid token"}), 403
            
        return f(*args, **kwargs)
    return wrapped

def _get_service() -> PresenceService:
    db = current_app.config['FIRESTORE_DB']
    redis_client = current_app.config.get('REDIS_CLIENT')
    return PresenceService(db, redis_client)

@presence_api.route('/api/v2/presence/online', methods=['POST'])
@require_pro_auth
def pro_go_online():
    """
    Pro taps GO ONLINE.
    Validates identity & qualifications, creates unique deviceSessionId,
    and grants server-authoritative 90s presence lease.
    """
    try:
        uid = request.user.get('uid')
        data = request.json or {}
        
        lat = data.get('latitude')
        lng = data.get('longitude')
        if lat is not None:
            lat = float(lat)
        if lng is not None:
            lng = float(lng)

        service = _get_service()
        success, result, status_code = service.go_online(uid, lat, lng)
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error in pro_go_online: {e}")
        return jsonify({"error": "INTERNAL_ERROR", "message": str(e)}), 500

@presence_api.route('/api/v2/presence/heartbeat', methods=['POST'])
@require_pro_auth
def pro_heartbeat():
    """
    Pro periodic 25s heartbeat request.
    Validates active session ID and renews 90s server lease.
    Rejects stale/hijacked sessions with 403 STALE_SESSION_REJECTED.
    """
    try:
        uid = request.user.get('uid')
        data = request.json or {}
        session_id = data.get('deviceSessionId')
        
        lat = data.get('latitude')
        lng = data.get('longitude')
        if lat is not None:
            lat = float(lat)
        if lng is not None:
            lng = float(lng)

        service = _get_service()
        success, result, status_code = service.record_heartbeat(uid, session_id, lat, lng)
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error in pro_heartbeat: {e}")
        return jsonify({"error": "INTERNAL_ERROR", "message": str(e)}), 500

@presence_api.route('/api/v2/presence/offline', methods=['POST'])
@require_pro_auth
def pro_go_offline():
    """
    Pro taps GO OFFLINE or logs out.
    Immediately expires presence lease and removes Pro from active spatial index.
    """
    try:
        uid = request.user.get('uid')
        data = request.json or {}
        session_id = data.get('deviceSessionId')

        service = _get_service()
        success, result, status_code = service.go_offline(uid, session_id)
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error in pro_go_offline: {e}")
        return jsonify({"error": "INTERNAL_ERROR", "message": str(e)}), 500

@presence_api.route('/api/v2/presence/reconcile', methods=['POST'])
def run_presence_reconciliation():
    """
    Periodic background reconciliation endpoint.
    Scans for expired leases and marks them offline.
    """
    try:
        service = _get_service()
        expired_count = service.reconcile_expired_presence()
        return jsonify({
            "success": True,
            "expiredRecordsReconciled": expired_count
        }), 200
    except Exception as e:
        logger.error(f"Error in run_presence_reconciliation: {e}")
        return jsonify({"error": "INTERNAL_ERROR", "message": str(e)}), 500
