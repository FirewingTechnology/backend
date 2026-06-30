from flask import Blueprint, request, jsonify, current_app
from google.cloud import firestore
from src.infrastructure.redis.exceptions import LockAcquisitionError
from firebase_admin import auth
from functools import wraps

kyc_api = Blueprint('kyc_api', __name__)

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

def _get_db_and_lock() -> tuple:
    db = current_app.config.get('FIRESTORE_DB')
    kyc_repo = current_app.config.get('KYC_REPO')
    lock_service = current_app.config.get('LOCK_SERVICE')
    admin_logger = current_app.config.get('ADMIN_LOGGER')
    return db, kyc_repo, lock_service, admin_logger


@kyc_api.route('/api/v2/kyc/submit', methods=['POST'])
@require_auth
def submit_kyc():
    payload = request.json
    uid = payload.get('uid')
    pan_number = payload.get('panNumber')
    selfie_url = payload.get('selfieUrl')
    bank_proof_url = payload.get('bankProofUrl')
    address_proof_url = payload.get('addressProofUrl')

    if not all([uid, pan_number, selfie_url, bank_proof_url, address_proof_url]):
        return jsonify({"error": "All fields required: uid, panNumber, selfieUrl, bankProofUrl, addressProofUrl"}), 400

    db, kyc_repo, lock_service, _ = _get_db_and_lock()
    lock_token = lock_service.acquire_lock(f"kyc:{uid}", ttl_seconds=15)
    try:
        tx = db.transaction()
        result = _run_submit_tx(tx, kyc_repo, uid, pan_number, selfie_url, bank_proof_url, address_proof_url)

        if result == "ALREADY_APPROVED":
            return jsonify({"error": "KYC already approved for this account."}), 400
        return jsonify({"status": "submitted"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"KYC submit error: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        lock_service.release_lock(f"kyc:{uid}", lock_token)


@firestore.transactional
def _run_submit_tx(tx, kyc_repo, uid, pan_number, selfie_url, bank_proof_url, address_proof_url):
    return kyc_repo.submit_kyc_tx(tx, uid, pan_number, selfie_url, bank_proof_url, address_proof_url)


@kyc_api.route('/api/v2/kyc/admin/approve', methods=['POST'])
@require_admin
def admin_update_kyc():
    """Admin-only. Must be protected by admin auth middleware in production."""
    payload = request.json
    uid = payload.get('uid')
    status = payload.get('status')  # 'approved', 'rejected', 'action_required'
    admin_uid = payload.get('adminUid')
    rejection_reason = payload.get('rejectionReason')

    if not all([uid, status, admin_uid]):
        return jsonify({"error": "uid, status, adminUid required"}), 400

    db, kyc_repo, lock_service, admin_logger = _get_db_and_lock()
    lock_token = lock_service.acquire_lock(f"kyc:{uid}", ttl_seconds=15)
    try:
        tx = db.transaction()
        result = _run_update_tx(tx, kyc_repo, uid, status, admin_uid, rejection_reason)

        if result == "KYC_NOT_FOUND":
            return jsonify({"error": "KYC submission not found."}), 404
        if result == "ALREADY_APPROVED":
            return jsonify({"error": "Already approved."}), 400

        admin_logger.log_action(
            admin_uid=admin_uid,
            action=f"KYC_{status.upper()}",
            target_id=uid,
            metadata={"rejectionReason": rejection_reason},
            ip_address=request.remote_addr
        )
        return jsonify({"status": result}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent request. Try again."}), 409
    except Exception as e:
        print(f"KYC admin error: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        lock_service.release_lock(f"kyc:{uid}", lock_token)


@firestore.transactional
def _run_update_tx(tx, kyc_repo, uid, status, admin_uid, rejection_reason):
    return kyc_repo.update_kyc_status_tx(tx, uid, status, admin_uid, rejection_reason)
