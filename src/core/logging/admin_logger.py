from google.cloud import firestore
import datetime

class AdminLogger:
    def __init__(self, db: firestore.Client):
        self.db = db

    def log_action(self, admin_uid: str, action: str, target_id: str, metadata: dict, ip_address: str):
        self.db.collection('admin_audit_logs').add({
            'actorId': admin_uid,
            'action': action,
            'targetId': target_id,
            'metadata': metadata,
            'ipAddress': ip_address,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
