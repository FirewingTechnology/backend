from src.core.security.event_repository import SecurityEventRepository
from src.core.security.event_models import SecurityEventSeverity, SecurityEventType

class SecurityEventService:
    def __init__(self, repo: SecurityEventRepository):
        self.repo = repo

    def log(self, event_type: SecurityEventType, severity: SecurityEventSeverity, uid: str,
            ip_address: str, device_id: str = "", metadata: dict = None):
        e_type = event_type.value if hasattr(event_type, 'value') else str(event_type)
        s_sev = severity.value if hasattr(severity, 'value') else str(severity)
        self.repo.insert_event(
            event_type=e_type,
            severity=s_sev,
            uid=uid,
            ip_address=ip_address,
            device_id=device_id,
            metadata=metadata or {}
        )
        # Alert threshold: > 5 HIGH/CRITICAL events per UID in 10 minutes → lock
        if s_sev in ["HIGH", "CRITICAL", SecurityEventSeverity.HIGH, SecurityEventSeverity.CRITICAL]:
            count = self.repo.count_high_events_last_10_min(uid)
            if count >= 5:
                self._trigger_account_lock(uid)

    def log_event(self, event_type: str, severity: str, source_ip: str, details: dict):
        uid = details.get('proUid') or details.get('uid') or details.get('userUid') or "unknown"
        self.log(
            event_type=event_type,
            severity=severity,
            uid=uid,
            ip_address=source_ip,
            metadata=details
        )

    def _trigger_account_lock(self, uid: str):
        """Marks the user account as locked in Firestore."""
        try:
            self.repo.db.collection('users').document(uid).update({
                'accountLocked': True,
                'accountLockedAt': __import__('google.cloud.firestore', fromlist=['firestore']).SERVER_TIMESTAMP
            })
        except Exception as e:
            print(f"[ACCOUNT_LOCK_ERROR] {e}")
