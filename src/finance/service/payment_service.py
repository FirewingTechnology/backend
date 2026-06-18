from google.cloud import firestore
from src.finance.repository.wallet_repository import WalletRepository
from src.infrastructure.redis.lock_service import RedisLockService

class PaymentService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService, wallet_repo: WalletRepository):
        self.db = db
        self.lock_service = lock_service
        self.wallet_repo = wallet_repo

    def process_webhook(self, payment_id: str, uid: str, amount_paise: int):
        if not isinstance(amount_paise, int):
            raise TypeError("Financial amounts must be integers (paise).")

        lock_token = self.lock_service.acquire_lock(f"payment:{payment_id}", ttl_seconds=30)
        try:
            tx = self.db.transaction()
            status = self._run_tx(tx, uid, payment_id, amount_paise)
            return status
        finally:
            self.lock_service.release_lock(f"payment:{payment_id}", lock_token)

    @firestore.transactional
    def _run_tx(self, tx, uid, payment_id, amount_paise):
        return self.wallet_repo.execute_wallet_credit_tx(tx, uid, payment_id, amount_paise)
