from google.cloud import firestore
from src.jobs.otp_repository import OtpRepository
from src.finance.repository.escrow_repository import EscrowRepository
from src.infrastructure.redis.lock_service import RedisLockService
from src.core.security.event_service import SecurityEventService
from src.core.security.event_models import SecurityEventType, SecurityEventSeverity

class OtpService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService,
                 otp_repo: OtpRepository, escrow_repo: EscrowRepository,
                 security_service: SecurityEventService):
        self.db = db
        self.lock_service = lock_service
        self.otp_repo = otp_repo
        self.escrow_repo = escrow_repo
        self.security_service = security_service

    def generate_otp(self, job_id: str, uid: str, ip_address: str) -> str:
        """Called by User App. Returns plain OTP."""
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=10)
        try:
            tx = self.db.transaction()
            plain_otp = self._run_generate_tx(tx, job_id)
            return plain_otp
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    @firestore.transactional
    def _run_generate_tx(self, tx, job_id):
        return self.otp_repo.generate_and_store_otp_tx(tx, job_id)

    def verify_otp(self, job_id: str, plain_otp: str, pro_uid: str,
                   ip_address: str, amount_paise: int, commission_paise: int):
        """Called by Pro App. Validates OTP and triggers escrow lock."""
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=15)
        try:
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
                # Log warning at attempt 3+
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

            # OTP verified → trigger escrow hold
            if result == "VERIFIED":
                escrow_tx = self.db.transaction()
                self._run_escrow_tx(escrow_tx, job_id, pro_uid, amount_paise, commission_paise)

        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    @firestore.transactional
    def _run_verify_tx(self, tx, job_id, plain_otp):
        return self.otp_repo.verify_otp_tx(tx, job_id, plain_otp)

    @firestore.transactional
    def _run_escrow_tx(self, tx, job_id, pro_uid, amount_paise, commission_paise):
        self.escrow_repo.lock_job_funds_tx(tx, job_id, pro_uid, amount_paise, commission_paise)
