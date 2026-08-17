from google.cloud import firestore
from src.jobs.otp_repository import OtpRepository
from src.finance.repository.escrow_repository import EscrowRepository
from src.infrastructure.redis.lock_service import RedisLockService
from src.core.security.event_service import SecurityEventService
from src.core.security.event_models import SecurityEventType, SecurityEventSeverity
from src.infrastructure.firebase.fcm_service import FCMService

class OtpService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService,
                 otp_repo: OtpRepository, escrow_repo: EscrowRepository,
                 security_service: SecurityEventService):
        self.db = db
        self.lock_service = lock_service
        self.otp_repo = otp_repo
        self.escrow_repo = escrow_repo
        self.security_service = security_service

    def request_completion(self, job_id: str, pro_uid: str, ip_address: str) -> str:
        """Called by Pro App when tapping Work Completed. Generates OTP, stores hashed OTP on job, stores plain OTP in user private doc & sends FCM."""
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=10)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                raise ValueError("Job not found")

            job_data = job_doc.to_dict()
            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                raise ValueError("Unauthorized: You are not assigned to this job")

            user_id = job_data.get('userId') or job_data.get('userUid')
            if not user_id:
                raise ValueError("Customer record missing on job")

            tx = self.db.transaction()
            plain_otp = self._run_generate_tx(tx, job_id)

            # Send FCM notification to user with OTP in data payload
            fcm_data = {
                "type": "COMPLETION_OTP",
                "jobId": job_id,
                "otp": plain_otp,
                "requiresAction": "true"
            }
            title = "Service Completion Code"
            body = f"Share code {plain_otp} with your electrician to confirm completion."

            if fcm_token:
                try:
                    FCMService.send_to_token(fcm_token, fcm_data, title=title, body=body, channel_id="powrsply_general_v1")
                except Exception as e:
                    print(f"FCM token delivery error: {e}")
            
            # Broadcast to user topic as fallback
            try:
                FCMService.send_to_topic(f"user_{user_id}", fcm_data, title=title, body=body, channel_id="powrsply_general_v1")
            except Exception as e:
                print(f"FCM topic fallback error: {e}")

            return plain_otp
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def generate_otp(self, job_id: str, uid: str, ip_address: str) -> str:
        """Legacy helper endpoint."""
        return self.request_completion(job_id, uid, ip_address)

    @firestore.transactional
    def _run_generate_tx(self, tx, job_id):
        return self.otp_repo.generate_and_store_otp_tx(tx, job_id)

    def verify_otp(self, job_id: str, plain_otp: str, pro_uid: str,
                   ip_address: str, amount_paise: int = 0, commission_paise: int = 0):
        """Called by Pro App. Validates OTP and processes payment decision."""
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                raise ValueError("Job not found")

            job_data = job_doc.to_dict()
            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                raise ValueError("Unauthorized: You are not assigned to this job")

            tx = self.db.transaction()
            result = self._run_verify_tx(tx, job_id, plain_otp)

            if result == "LOCKED_OUT":
                self.security_service.log(
                    SecurityEventType.OTP_BRUTE_FORCE,
                    SecurityEventSeverity.HIGH,
                    pro_uid, ip_address,
                    metadata={"jobId": job_id}
                )
                raise PermissionError("Account locked due to too many OTP attempts.")

            if result.startswith("INVALID_OTP"):
                attempts = int(result.split(":")[1])
                if attempts >= 3:
                    self.security_service.log(
                        SecurityEventType.OTP_BRUTE_FORCE,
                        SecurityEventSeverity.WARNING,
                        pro_uid, ip_address,
                        metadata={"jobId": job_id, "attempt": attempts}
                    )
                raise ValueError("Invalid OTP.")

            if result == "OTP_EXPIRED":
                raise ValueError("OTP has expired.")

            if result.startswith("INVALID_STATUS") or result == "JOB_NOT_FOUND":
                raise ValueError(f"Cannot verify OTP: {result}")

            # Re-read updated doc to inspect payment method
            updated_doc = job_ref.get()
            data = updated_doc.to_dict()
            payment_method = (data.get('paymentMethod') or 'online').lower()
            payment_status = (data.get('paymentStatus') or 'pending').lower()

            if payment_method == 'online' and (payment_status == 'paid' or data.get('paymentVerified') is True):
                # Online & Paid -> Mark completed immediately
                job_ref.update({
                    'status': 'completed',
                    'completionStatus': 'completed',
                    'paymentConfirmed': True,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return {
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentMethod": "online",
                    "paymentConfirmed": True,
                    "message": "Job completed successfully."
                }
            else:
                # Cash / Direct UPI -> Move to payment_pending
                job_ref.update({
                    'completionStatus': 'payment_pending',
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return {
                    "status": "payment_pending",
                    "completionStatus": "payment_pending",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": False,
                    "message": "OTP verified. Awaiting payment confirmation."
                }
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def confirm_direct_payment(self, job_id: str, pro_uid: str, confirmed: bool, ip_address: str):
        """Called by Pro App when confirming receipt of Cash / Direct UPI."""
        lock_token = self.lock_service.acquire_lock(f"payment:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                raise ValueError("Job not found")

            job_data = job_doc.to_dict()
            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                raise ValueError("Unauthorized: You are not assigned to this job")

            completion_status = job_data.get('completionStatus')
            if completion_status not in ['otp_verified', 'payment_pending']:
                raise ValueError("Completion OTP must be verified before payment confirmation.")

            payment_method = (job_data.get('paymentMethod') or 'cash').lower()

            if confirmed:
                job_ref.update({
                    'status': 'completed',
                    'completionStatus': 'completed',
                    'paymentStatus': 'confirmed',
                    'paymentConfirmedAt': firestore.SERVER_TIMESTAMP,
                    'paymentConfirmedBy': pro_uid,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return {
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": True,
                    "message": "Direct payment confirmed. Job completed."
                }
            else:
                job_ref.update({
                    'completionStatus': 'payment_pending',
                    'paymentStatus': f"{payment_method}_unconfirmed",
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return {
                    "status": "payment_pending",
                    "completionStatus": "payment_pending",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": False,
                    "message": "Payment unconfirmed by professional."
                }
        finally:
            self.lock_service.release_lock(f"payment:{job_id}", lock_token)

    @firestore.transactional
    def _run_verify_tx(self, tx, job_id, plain_otp):
        return self.otp_repo.verify_otp_tx(tx, job_id, plain_otp)
