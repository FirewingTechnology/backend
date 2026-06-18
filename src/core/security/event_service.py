from src.core.security.event_repository import SecurityEventRepository
from src.core.security.event_models import SecurityEventSeverity, SecurityEventType

class SecurityEventService:
    def __init__(self, repo: SecurityEventRepository):
        self.repo = repo

    def log(self, event_type: SecurityEventType, severity: SecurityEventSeverity, uid: str,
            ip_address: str, device_id: str = "", metadata: dict = None):
        self.repo.insert_event(
            event_type=event_type.value,
            severity=severity.value,
            uid=uid,
            ip_address=ip_address,
            device_id=device_id,
            metadata=metadata or {}
        )
        # Alert threshold: > 5 HIGH/CRITICAL events per UID in 10 minutes → lock
        if severity in [SecurityEventSeverity.HIGH, SecurityEventSeverity.CRITICAL]:
            count = self.repo.count_high_events_last_10_min(uid)
            if count >= 5:
                self._trigger_account_lock(uid)

    def _trigger_account_lock(self, uid: str):
        """Marks the user account as locked in Firestore."""
        try:
            self.repo.db.collection('users').document(uid).update({
                'accountLocked': True,
                'accountLockedAt': __import__('google.cloud.firestore', fromlist=['firestore']).SERVER_TIMESTAMP
            })
        except Exception as e:
            print(f"[ACCOUNT_LOCK_ERROR] {e}")
