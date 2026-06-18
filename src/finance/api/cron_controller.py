from flask import Blueprint, request, jsonify, current_app
import datetime
from src.infrastructure.redis.exceptions import LockAcquisitionError

cron_api = Blueprint('cron_api', __name__)

@cron_api.route('/api/v2/finance/cron/release-escrow', methods=['POST'])
def release_escrow():
    # In production, verify Google Cloud Scheduler signature via OIDC here
    
    db = current_app.config.get('FIRESTORE_DB')
    escrow_service = current_app.config.get('ESCROW_SERVICE')
    
    if not db or not escrow_service:
        return jsonify({"error": "Service not configured"}), 500

    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Query for held funds past their release date
    try:
        holds_ref = db.collection('escrow_holds')
        query = holds_ref.where('status', '==', 'held').where('releaseAt', '<=', now).limit(500)
        docs = query.stream()
        
        success_count = 0
        for doc in docs:
            job_id = doc.get('jobId')
            try:
                result = escrow_service.release_escrow(job_id)
                if result == "SUCCESS":
                    success_count += 1
            except LockAcquisitionError:
                # Skip and retry next cron run
                continue
                
        return jsonify({"status": "completed", "releasedCount": success_count}), 200
        
    except Exception as e:
        print(f"Cron Escrow error: {e}")
        return jsonify({"error": str(e)}), 500

@cron_api.route('/api/v2/finance/cron/reconciliation', methods=['POST'])
def run_reconciliation():
    reconciliation_service = current_app.config.get('RECONCILIATION_SERVICE')
    if not reconciliation_service:
        return jsonify({"error": "Service not configured"}), 500
        
    try:
        report = reconciliation_service.run_nightly_audit()
        return jsonify(report), 200
    except Exception as e:
        print(f"Reconciliation error: {e}")
        return jsonify({"error": str(e)}), 500
