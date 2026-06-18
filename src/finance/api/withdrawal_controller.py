from flask import Blueprint, request, jsonify, current_app
from src.infrastructure.redis.exceptions import LockAcquisitionError
from src.finance.domain.exceptions import InsufficientFundsError, ExternalAPIError

# Assuming there is a standard auth decorator, if not, we use firebase_admin to verify
from firebase_admin import auth

withdrawal_api = Blueprint('withdrawal_api', __name__)

def require_auth(f):
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
    wrapped.__name__ = f.__name__
    return wrapped

@withdrawal_api.route('/api/v2/finance/withdraw', methods=['POST'])
@require_auth
def withdraw_funds():
    payload = request.json
    uid = payload.get('uid')
    amount_paise = payload.get('amountPaise')
    idempotency_key = payload.get('idempotencyKey')
    
    if not uid or not amount_paise or not idempotency_key:
        return jsonify({"error": "uid, amountPaise, and idempotencyKey are required."}), 400
        
    try:
        amount_paise = int(amount_paise)
        if amount_paise <= 0:
            return jsonify({"error": "amountPaise must be greater than zero."}), 400
    except ValueError:
        return jsonify({"error": "amountPaise must be an integer."}), 400

    # Ensure UID matches authenticated user
    if uid != request.user.get('uid'):
        return jsonify({"error": "Unauthorized: UID mismatch"}), 403

    withdrawal_service = current_app.config.get('WITHDRAWAL_SERVICE')
    if not withdrawal_service:
        return jsonify({"error": "Withdrawal service not configured."}), 500

    try:
        withdrawal_service.initiate_withdrawal(uid, amount_paise, idempotency_key)
        return jsonify({"status": "processing", "message": "Withdrawal initiated."}), 200
    except InsufficientFundsError:
        return jsonify({"error": "Insufficient funds."}), 422
    except LockAcquisitionError:
        return jsonify({"error": "Concurrent withdrawal processing. Try again."}), 409
    except ExternalAPIError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        print(f"Withdrawal error: {e}")
        return jsonify({"error": "Internal failure."}), 500
