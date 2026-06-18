from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError

dispute_api = Blueprint('dispute_api', __name__)

@dispute_api.route('/api/v2/disputes/raise', methods=['POST'])
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
