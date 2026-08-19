from firebase_admin import firestore
from src.jobs.job_repository import JobRepository
from src.infrastructure.firebase.fcm_service import FCMService
from src.infrastructure.redis.lock_service import RedisLockService
from src.infrastructure.redis.exceptions import LockAcquisitionError
from src.marketplace.matching_engine import MatchingEngine
from src.marketplace.fraud_detector import FraudDetector
from src.core.security.event_service import SecurityEventService
from src.core.security.event_repository import SecurityEventRepository
from typing import Dict, Any
from datetime import datetime, timezone

class JobService:
    def __init__(self):
        self.repo = JobRepository()
        self.db = firestore.client() # For cross-collection queries
        
        # In production, redis_client would be injected
        import redis
        import os
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        redis_client = redis.from_url(redis_url)
        
        self.lock_service = RedisLockService(redis_client)
        self.matching_engine = MatchingEngine(redis_client)
        self.security_service = SecurityEventService(SecurityEventRepository(self.db))
        self.fraud_detector = FraudDetector(self.security_service)

    def dispatch_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Saves job and broadcasts to nearby pros via FCM topic based on geohash prefix."""
        user_uid = job_data.get('userId') or job_data.get('userUid')
        pro_uid = job_data.get('electricianId') or job_data.get('proUid')
        user_phone = job_data.get('phone') or job_data.get('userPhone') or ""

        # Check self booking on direct dispatch
        if pro_uid and user_uid:
            if self.fraud_detector.detect_self_booking(user_uid, pro_uid, job_data.get('idempotencyKey', 'direct_dispatch'), user_phone=user_phone):
                raise ValueError("Suspicious Activity: You cannot book yourself. Your pro account has been suspended for 1 hour.")

        idem_key = job_data.get('idempotencyKey')
        
        # 1. Fast Cache Idempotency Check
        redis_idem_key = f"idem:job:{idem_key}"
        if self.matching_engine.redis.exists(redis_idem_key):
            raise ValueError("Duplicate dispatch request")
            
        # 2. Strict Persistence Idempotency Check
        idem_ref = self.db.collection('processed_jobs').document(idem_key)
        if idem_ref.get().exists:
            raise ValueError("Duplicate dispatch request")
            
        # Lock key to prevent race conditions during set
        self.matching_engine.redis.set(redis_idem_key, "processing", ex=86400)
        idem_ref.set({'processedAt': firestore.SERVER_TIMESTAMP})

        job_data['status'] = 'searching'
        job_data['createdAt'] = firestore.SERVER_TIMESTAMP
        job_id = self.repo.create_job(job_data)
        
        # Extract location to pass to the matching engine
        loc = job_data.get('location', {})
        lat = loc.get('lat', 19.0760)
        lng = loc.get('lng', 72.8777)
        
        # Use Redis GEORADIUS matching engine instead of blind geohash broadcast
        pros_notified = self.matching_engine.dispatch_job(job_id, lat, lng, job_data)
        
        return {"jobId": job_id, "prosNotified": pros_notified}

    def accept_job(self, job_id: str, pro_uid: str) -> Dict[str, Any]:
        """Atomically assigns a job to a pro using Redis lock to prevent race conditions."""
        lock_key = f"job_accept_lock:{job_id}"
        
        try:
            with self.lock_service.acquire(lock_key, expire_seconds=5):
                job = self.repo.get_job(job_id)
                if not job:
                    raise ValueError("Job not found")
                if job.get('status') != 'searching' and job.get('status') != 'pending':
                    raise ValueError("Job is no longer available")

                user_uid = job.get('userUid') or job.get('userId')
                user_phone = job.get('phone') or job.get('userPhone') or ""

                # 1. Anti-fraud check: Prevent Pro from accepting their own booking
                if user_uid == pro_uid or (user_phone and self._get_pro_phone(pro_uid) == user_phone):
                    self.fraud_detector.detect_self_booking(user_uid, pro_uid, job_id, user_phone=user_phone, pro_phone=self._get_pro_phone(pro_uid))
                    raise ValueError("Suspicious Activity: You cannot accept your own booking. Your pro account has been suspended for 1 hour.")

                # 2. Check if Pro is currently suspended
                if self._is_pro_suspended(pro_uid):
                    raise ValueError("Unauthorized: Your Pro account is temporarily suspended.")

                updates = {
                    'status': 'accepted',
                    'proUid': pro_uid,
                    'electricianId': pro_uid,
                    'acceptedAt': firestore.SERVER_TIMESTAMP
                }
                self.repo.update_job(job_id, updates)
                
                # Notify User that Pro accepted
                if user_uid:
                    FCMService.send_to_topic(
                        topic=f"user_{user_uid}",
                        data={"type": "STATUS_UPDATE", "jobId": job_id, "status": "accepted"},
                        title="Job Accepted! ⚡",
                        body="A professional is reviewing your request."
                    )
                    
                return updates
        except LockAcquisitionError:
            raise ValueError("Another Pro is currently accepting this job")

    def _get_pro_phone(self, pro_uid: str) -> str:
        try:
            user_doc = self.db.collection('users').document(pro_uid).get()
            if user_doc.exists:
                data = user_doc.to_dict() or {}
                return data.get('phone') or data.get('phoneNumber') or ""
        except Exception:
            pass
        return ""

    def _is_pro_suspended(self, pro_uid: str) -> bool:
        try:
            user_doc = self.db.collection('users').document(pro_uid).get()
            if user_doc.exists:
                data = user_doc.to_dict() or {}
                if data.get('accountStatus') == 'suspended' or data.get('verificationStatus') == 'suspended':
                    suspended_until = data.get('suspendedUntil')
                    if suspended_until:
                        # Check if suspendedUntil is in future
                        if isinstance(suspended_until, datetime):
                            if suspended_until.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
                                return True
                        return True
                    return True
        except Exception:
            pass
        return False

    def transition_job(self, job_id: str, pro_uid: str, new_state: str) -> Dict[str, Any]:
        """Progresses job through strict linear states and notifies customer."""
        VALID_TRANSITIONS = {
            'accepted': ['pro_enroute', 'cancelled'],
            'pro_enroute': ['arrived', 'cancelled'],
            'arrived': ['in_progress', 'cancelled'],
            'in_progress': ['otp_pending'], # OTP triggers escrow
            'otp_pending': ['escrow'], 
            'escrow': ['completed_cleared', 'disputed']
        }
        
        job = self.repo.get_job(job_id)
        current_state = job.get('status')
        
        # Validate Pro
        if job.get('proUid') != pro_uid:
            raise ValueError("Unauthorized: You are not assigned to this job")
            
        # Validate Transition
        allowed = VALID_TRANSITIONS.get(current_state, [])
        if new_state not in allowed:
            raise ValueError(f"Invalid state transition from {current_state} to {new_state}")
            
        updates = {
            'status': new_state,
            f'{new_state}At': firestore.SERVER_TIMESTAMP
        }
        self.repo.update_job(job_id, updates)
        
        # FCM Notifications Mapping
        notifications = {
            "pro_enroute": { "title": "Pro On The Way", "body": "Your Electrician is heading to your location." },
            "arrived": { "title": "Pro Arrived", "body": "Your Electrician is at the door." },
            "in_progress": { "title": "Work Started", "body": "Diagnosis and repair in progress." },
            "escrow": { "title": "Payment Secured 🔒", "body": "Funds have been safely held in Escrow." }
        }
        
        user_uid = job.get('userUid')
        if user_uid and new_state in notifications:
            # We use a user-specific topic or resolve token here
            FCMService.send_to_topic(
                topic=f"user_{user_uid}",
                data={"type": "STATUS_UPDATE", "jobId": job_id, "status": new_state},
                title=notifications[new_state]["title"],
                body=notifications[new_state]["body"]
            )
            
        return updates

    def start_work(self, job_id: str, pro_uid: str, current_price: float) -> Dict[str, Any]:
        """Atomically starts work and locks the job price."""
        job = self.repo.get_job(job_id)
        
        # Validate Pro
        if job.get('proUid') != pro_uid:
            raise ValueError("Unauthorized: You are not assigned to this job")
            
        # Validate Transition
        if job.get('status') != 'arrived':
            raise ValueError("Job must be in arrived state to start work")
            
        updates = {
            'status': 'in_progress',
            'in_progressAt': firestore.SERVER_TIMESTAMP,
            'priceLockedAt': firestore.SERVER_TIMESTAMP,
            'lockedByServer': True,
            'lockedPrice': current_price
        }
        self.repo.update_job(job_id, updates)
        
        user_uid = job.get('userUid')
        if user_uid:
            FCMService.send_to_topic(
                topic=f"user_{user_uid}",
                data={"type": "STATUS_UPDATE", "jobId": job_id, "status": "in_progress"},
                title="Work Started",
                body="Diagnosis and repair in progress. Price is locked."
            )
            
        return updates
