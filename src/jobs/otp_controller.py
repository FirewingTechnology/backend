from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError

otp_api = Blueprint('otp_api', __name__)

def _parse_error_code(err_str: str):
    """Extract code and message from formatted exceptions 'CODE: message'."""
    if ":" in err_str:
        parts = err_str.split(":", 1)
        code = parts[0].strip()
        msg = parts[1].strip()
        return code, msg
    return "SERVER_ERROR", err_str

@otp_api.route('/api/v2/jobs/request-completion', methods=['POST'])
@otp_api.route('/api/jobs/<job_id>/request-completion', methods=['POST'])
def request_completion(job_id=None):
    """Called by Pro App when tapping Work Completed. Triggers server-side OTP generation and FCM delivery."""
    payload = request.json or {}
    job_id = job_id or payload.get('jobId')
    pro_uid = payload.get('proUid') or payload.get('uid')

    if not job_id or not pro_uid:
        return jsonify({
            "success": False,
            "code": "INVALID_PARAMETERS",
            "message": "jobId and proUid are required"
        }), 400

    otp_service = current_app.config.get('OTP_SERVICE')
    if not otp_service:
        return jsonify({
            "success": False,
            "code": "SERVICE_UNAVAILABLE",
            "message": "OTP Service is currently initializing. Please try again."
        }), 503

    try:
        plain_otp = otp_service.request_completion(job_id, pro_uid, request.remote_addr)
        return jsonify({
            "success": True,
            "status": "otp_pending",
            "message": "Completion OTP sent to customer.",
            "expiresInMinutes": 15
        }), 200
    except ValueError as e:
        code, msg = _parse_error_code(str(e))
        return jsonify({
            "success": False,
            "code": code,
            "message": msg
        }), 400
    except LockAcquisitionError:
        return jsonify({
            "success": False,
            "code": "CONCURRENT_REQUEST",
            "message": "Another operation is in progress. Please try again."
        }), 409
    except Exception as e:
        print(f"OTP generate error: {e}")
        return jsonify({
            "success": False,
            "code": "SERVER_ERROR",
            "message": "Failed to request completion OTP. Please try again."
        }), 500


@otp_api.route('/api/v2/jobs/otp/generate', methods=['POST'])
def generate_otp():
    """Legacy helper route for OTP generation."""
    return request_completion()


@otp_api.route('/api/v2/jobs/otp/verify', methods=['POST'])
@otp_api.route('/api/jobs/<job_id>/verify-completion-otp', methods=['POST'])
def verify_otp(job_id=None):
    """Called by Pro App when electrician enters 6-digit OTP."""
    payload = request.json or {}
    job_id = job_id or payload.get('jobId')
    plain_otp = payload.get('otp')
    pro_uid = payload.get('proUid') or payload.get('uid')

    if not all([job_id, plain_otp, pro_uid]):
        return jsonify({
            "success": False,
            "code": "INVALID_PARAMETERS",
            "message": "jobId, otp, and proUid are required"
        }), 400

    otp_service = current_app.config.get('OTP_SERVICE')
    if not otp_service:
        return jsonify({
            "success": False,
            "code": "SERVICE_UNAVAILABLE",
            "message": "OTP Service is currently initializing. Please try again."
        }), 503

    try:
        result = otp_service.verify_otp(job_id, str(plain_otp), pro_uid, request.remote_addr)
        if isinstance(result, dict) and not result.get("success", True):
            return jsonify(result), 400
        return jsonify(result), 200
    except PermissionError as e:
        code, msg = _parse_error_code(str(e))
        return jsonify({
            "success": False,
            "code": code,
            "message": msg
        }), 429
    except ValueError as e:
        code, msg = _parse_error_code(str(e))
        return jsonify({
            "success": False,
            "code": code,
            "message": msg
        }), 400
    except LockAcquisitionError:
        return jsonify({
            "success": False,
            "code": "CONCURRENT_REQUEST",
            "message": "Another operation is in progress. Please try again."
        }), 409
    except Exception as e:
        print(f"OTP verify error: {e}")
        return jsonify({
            "success": False,
            "code": "SERVER_ERROR",
            "message": "Failed to verify completion OTP. Please try again."
        }), 500


@otp_api.route('/api/v2/jobs/confirm-direct-payment', methods=['POST'])
@otp_api.route('/api/jobs/<job_id>/confirm-direct-payment', methods=['POST'])
def confirm_direct_payment(job_id=None):
    """Called by Pro App when confirming receipt of Cash / Direct UPI."""
    payload = request.json or {}
    job_id = job_id or payload.get('jobId')
    pro_uid = payload.get('proUid') or payload.get('uid')
    confirmed = payload.get('confirmed', True)

    if not job_id or not pro_uid:
        return jsonify({
            "success": False,
            "code": "INVALID_PARAMETERS",
            "message": "jobId and proUid are required"
        }), 400

    otp_service = current_app.config.get('OTP_SERVICE')
    if not otp_service:
        return jsonify({
            "success": False,
            "code": "SERVICE_UNAVAILABLE",
            "message": "Payment Service is currently initializing. Please try again."
        }), 503

    try:
        result = otp_service.confirm_direct_payment(job_id, pro_uid, bool(confirmed), request.remote_addr)
        if isinstance(result, dict) and not result.get("success", True):
            return jsonify(result), 400
        return jsonify(result), 200
    except ValueError as e:
        code, msg = _parse_error_code(str(e))
        return jsonify({
            "success": False,
            "code": code,
            "message": msg
        }), 400
    except LockAcquisitionError:
        return jsonify({
            "success": False,
            "code": "CONCURRENT_REQUEST",
            "message": "Another operation is in progress. Please try again."
        }), 409
    except Exception as e:
        print(f"Payment confirmation error: {e}")
        return jsonify({
            "success": False,
            "code": "SERVER_ERROR",
            "message": "Failed to process payment confirmation. Please try again."
        }), 500

