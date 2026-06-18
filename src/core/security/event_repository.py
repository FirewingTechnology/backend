from google.cloud import firestore

class SecurityEventRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def insert_event(self, event_type: str, severity: str, uid: str, ip_address: str, device_id: str, metadata: dict):
        """
        Non-blocking async insert. Never raises. Never blocks financial flows.
        """
        try:
            import threading
            def _bg_insert():
                try:
                    self.db.collection('security_events').add({
                        'eventType': event_type,
                        'severity': severity,
                        'uid': uid,
                        'ipAddress': ip_address,
                        'deviceId': device_id,
                        'metadata': metadata,
                        'createdAt': firestore.SERVER_TIMESTAMP
                    })
                except Exception as e:
                    print(f"[SECURITY_LOG_ERROR] Background insert failed: {e}")
            
            threading.Thread(target=_bg_insert, daemon=True).start()
        except Exception as e:
            # Fail silently — security logging must NEVER block financial transactions
            print(f"[SECURITY_LOG_ERROR] Failed to start insert thread: {e}")

    def count_high_events_last_10_min(self, uid: str) -> int:
        import datetime
        threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
        docs = (self.db.collection('security_events')
                .where('uid', '==', uid)
                .where('severity', 'in', ['HIGH', 'CRITICAL'])
                .where('createdAt', '>=', threshold)
                .stream())
        return sum(1 for _ in docs)
