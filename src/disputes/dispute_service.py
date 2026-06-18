from google.cloud import firestore
from src.disputes.dispute_repository import DisputeRepository
from src.infrastructure.redis.lock_service import RedisLockService
from src.core.logging.admin_logger import AdminLogger

class DisputeService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService,
                 repo: DisputeRepository, admin_logger: AdminLogger):
        self.db = db
        self.lock_service = lock_service
        self.repo = repo
        self.admin_logger = admin_logger

    def raise_dispute(self, job_id: str, user_uid: str, reason: str):
        lock_token = self.lock_service.acquire_lock(f"dispute:{job_id}", ttl_seconds=15)
        try:
            tx = self.db.transaction()
            result = self._run_create_tx(tx, job_id, user_uid, reason)
            if result == "ESCROW_NOT_FOUND":
                raise LookupError("No escrow hold found for this job.")
            if result.startswith("ESCROW_NOT_HELD"):
                raise ValueError(f"Escrow already released or resolved: {result}")
            return result  # Returns "CREATED:{disputeId}"
        finally:
            self.lock_service.release_lock(f"dispute:{job_id}", lock_token)

    @firestore.transactional
    def _run_create_tx(self, tx, job_id, user_uid, reason):
        return self.repo.create_dispute_tx(tx, job_id, user_uid, reason)

    def resolve_dispute(self, dispute_id: str, resolution: str, admin_uid: str, ip_address: str):
        lock_token = self.lock_service.acquire_lock(f"dispute_resolve:{dispute_id}", ttl_seconds=15)
        try:
            tx = self.db.transaction()
            result = self._run_resolve_tx(tx, dispute_id, resolution, admin_uid)

            if result not in ["RESOLVED"]:
                raise ValueError(f"Resolution failed: {result}")

            # Immutable admin audit trail
            self.admin_logger.log_action(
                admin_uid=admin_uid,
                action=f"DISPUTE_{resolution.upper()}",
                target_id=dispute_id,
                metadata={"resolution": resolution},
                ip_address=ip_address
            )
            return result
        finally:
            self.lock_service.release_lock(f"dispute_resolve:{dispute_id}", lock_token)

    @firestore.transactional
    def _run_resolve_tx(self, tx, dispute_id, resolution, admin_uid):
        return self.repo.resolve_dispute_tx(tx, dispute_id, resolution, admin_uid)
