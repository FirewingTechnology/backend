from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError
from firebase_admin import auth
from functools import wraps

dispute_api = Blueprint('dispute_api', __name__)

def require_auth(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header.split('Bearer ')[1]
        try:
            decoded_token = auth.verify_id_token(token)
            request.user = decoded_token
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapped

def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header.split('Bearer ')[1]
        try:
            decoded_token = auth.verify_id_token(token)
            request.user = decoded_token
            if decoded_token.get('role') not in ['admin', 'super_admin']:
                return jsonify({"error": "Forbidden: Requires Admin Role"}), 403
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapped

@dispute_api.route('/api/v2/disputes/raise', methods=['POST'])
@require_auth
def raise_dispute():
    payload = request.json
    job_id = payload.get('jobId')
    user_uid = payload.get('userUid')
    reason = payload.get('reason')

    if not all([job_id, user_uid, reason]):
        return jsonify({"error": "jobId, userUid, reason required"}), 400

    dispute_service = current_app.config.get('DISPUTE_SERVICE')
    try:
        result = dispute_service.raise_dispute(job_id, user_uid, reason)
        dispute_id = result.split(":")[1]
        return jsonify({"status": "raised", "disputeId": dispute_id}), 200
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"Dispute raise error: {e}")
        return jsonify({"error": "Internal error"}), 500


@dispute_api.route('/api/v2/disputes/resolve', methods=['POST'])
@require_admin
def resolve_dispute():
    """Admin-only endpoint. Must be protected by admin auth middleware in production."""
    payload = request.json
    dispute_id = payload.get('disputeId')
    resolution = payload.get('resolution')  # 'resolved_customer', 'resolved_pro', 'dismissed'
    admin_uid = payload.get('adminUid')

    if not all([dispute_id, resolution, admin_uid]):
        return jsonify({"error": "disputeId, resolution, adminUid required"}), 400

    valid_resolutions = ['resolved_customer', 'resolved_pro', 'dismissed']
    if resolution not in valid_resolutions:
        return jsonify({"error": f"Invalid resolution. Must be one of: {valid_resolutions}"}), 400

    dispute_service = current_app.config.get('DISPUTE_SERVICE')
    try:
        dispute_service.resolve_dispute(dispute_id, resolution, admin_uid, request.remote_addr)
        return jsonify({"status": "resolved"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"Dispute resolve error: {e}")
        return jsonify({"error": "Internal error"}), 500
