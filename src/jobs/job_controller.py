from flask import Blueprint, request, jsonify
from marshmallow import ValidationError
from src.jobs.job_service import JobService
from src.finance.domain.validators import FinancialTransactionSchema
from firebase_admin import auth

job_api = Blueprint('job_controller', __name__)
job_service = JobService()

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

@job_api.route('/dispatch', methods=['POST'])
@require_auth
def dispatch_job():
    idempotency_key = request.headers.get('x-idempotency-key')
    if not idempotency_key:
        return jsonify({'error': 'x-idempotency-key header required'}), 400
        
    schema = FinancialTransactionSchema()
    try:
        data = schema.load(request.json)
        data['idempotencyKey'] = idempotency_key
    except ValidationError as err:
        return jsonify({'error': 'Validation Failed', 'details': err.messages}), 400

    if data.get('userId') != request.user.get('uid'):
         return jsonify({'error': 'Unauthorized: UID mismatch'}), 403

    try:
        result = job_service.dispatch_job(data)
        return jsonify({'status': 'searching', 'jobId': result['jobId'], 'prosNotified': result['prosNotified']}), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@job_api.route('/accept', methods=['POST'])
@require_auth
def accept_job():
    data = request.json
    if 'jobId' not in data or 'proUid' not in data:
        return jsonify({'error': 'jobId and proUid required'}), 400
        
    if data.get('proUid') != request.user.get('uid'):
         return jsonify({'error': 'Unauthorized: UID mismatch'}), 403

    try:
        updates = job_service.accept_job(data['jobId'], data['proUid'])
        return jsonify(updates), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@job_api.route('/transition', methods=['POST'])
@require_auth
def transition_job():
    data = request.json
    if 'jobId' not in data or 'proUid' not in data or 'newState' not in data:
        return jsonify({'error': 'jobId, proUid, newState required'}), 400
        
    if data.get('proUid') != request.user.get('uid'):
         return jsonify({'error': 'Unauthorized: UID mismatch'}), 403

    try:
        updates = job_service.transition_job(data['jobId'], data['proUid'], data['newState'])
        return jsonify(updates), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@job_api.route('/start_work', methods=['POST'])
@require_auth
def start_work():
    data = request.json
    if 'jobId' not in data or 'proUid' not in data or 'currentPrice' not in data:
        return jsonify({'error': 'jobId, proUid, currentPrice required'}), 400
        
    if data.get('proUid') != request.user.get('uid'):
         return jsonify({'error': 'Unauthorized: UID mismatch'}), 403

    try:
        updates = job_service.start_work(data['jobId'], data['proUid'], data['currentPrice'])
        return jsonify(updates), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
