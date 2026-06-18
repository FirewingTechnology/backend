from google.cloud import firestore
from src.infrastructure.redis.lock_service import RedisLockService
from src.finance.repository.escrow_repository import EscrowRepository

class EscrowService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService, repo: EscrowRepository):
        self.db = db
        self.lock_service = lock_service
        self.repo = repo

    def process_job_completion(self, job_id: str, pro_uid: str, amount_paise: int, commission_paise: int):
        lock_token = self.lock_service.acquire_lock(f"escrow:{job_id}")
        try:
            tx = self.db.transaction()
            return self._run_lock_funds(tx, job_id, pro_uid, amount_paise, commission_paise)
        finally:
            self.lock_service.release_lock(f"escrow:{job_id}", lock_token)
            
    @firestore.transactional
    def _run_lock_funds(self, tx, job_id, pro_uid, amount_paise, commission_paise):
        return self.repo.lock_job_funds_tx(tx, job_id, pro_uid, amount_paise, commission_paise)

    def release_escrow(self, job_id: str):
        lock_token = self.lock_service.acquire_lock(f"escrow:{job_id}")
        try:
            tx = self.db.transaction()
            return self._run_release_funds(tx, job_id)
        finally:
            self.lock_service.release_lock(f"escrow:{job_id}", lock_token)

    @firestore.transactional
    def _run_release_funds(self, tx, job_id):
        return self.repo.release_escrow_tx(tx, job_id)
