from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError

otp_api = Blueprint('otp_api', __name__)

@otp_api.route('/api/v2/jobs/otp/generate', methods=['POST'])
def generate_otp():
    """Called by User App after job marked in_progress."""
    payload = request.json
    job_id = payload.get('jobId')
    uid = payload.get('uid')

    if not job_id or not uid:
        return jsonify({"error": "jobId and uid required"}), 400

    otp_service = current_app.config.get('OTP_SERVICE')
    try:
        plain_otp = otp_service.generate_otp(job_id, uid, request.remote_addr)
        # Plain OTP delivered only over HTTPS to the authenticated user
        return jsonify({"otp": plain_otp, "expiresInMinutes": 15}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"OTP generate error: {e}")
        return jsonify({"error": "Internal error"}), 500


@otp_api.route('/api/v2/jobs/otp/verify', methods=['POST'])
def verify_otp():
    """Called by Pro App when electrician enters OTP."""
    payload = request.json
    job_id = payload.get('jobId')
    plain_otp = payload.get('otp')
    pro_uid = payload.get('proUid')
    # Integer paise only
    amount_paise = payload.get('amountPaise')
    commission_paise = payload.get('commissionPaise')

    if not all([job_id, plain_otp, pro_uid, amount_paise is not None, commission_paise is not None]):
        return jsonify({"error": "jobId, otp, proUid, amountPaise, commissionPaise required"}), 400

    try:
        amount_paise = int(amount_paise)
        commission_paise = int(commission_paise)
    except (TypeError, ValueError):
        return jsonify({"error": "amountPaise and commissionPaise must be integers"}), 400

    otp_service = current_app.config.get('OTP_SERVICE')
    try:
        otp_service.verify_otp(job_id, plain_otp, pro_uid, request.remote_addr, amount_paise, commission_paise)
        return jsonify({"status": "verified", "message": "Job moved to escrow."}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 429
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"OTP verify error: {e}")
        return jsonify({"error": "Internal error"}), 500
