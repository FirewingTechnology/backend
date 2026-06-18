from google.cloud import firestore
from src.finance.repository.withdrawal_repository import WithdrawalRepository
from src.infrastructure.redis.lock_service import RedisLockService
from src.finance.infrastructure.razorpayx.payout_client import PayoutClient
from src.finance.domain.exceptions import InsufficientFundsError, ExternalAPIError, PayoutAPIError

class WithdrawalService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService, repo: WithdrawalRepository, payout_client: PayoutClient):
        self.db = db
        self.lock_service = lock_service
        self.repo = repo
        self.payout_client = payout_client

    def initiate_withdrawal(self, uid: str, amount_paise: int, idempotency_key: str):
        if not isinstance(amount_paise, int):
            raise TypeError("Financial amounts must be integers (paise).")
            
        lock_token = self.lock_service.acquire_lock(f"withdraw:{uid}", ttl_seconds=15)
        try:
            # 1. Lock Funds in Firestore
            tx = self.db.transaction()
            status = self._run_lock_tx(tx, uid, amount_paise, idempotency_key)
            if status == "ALREADY_PROCESSED": return
            if status == "INSUFFICIENT_FUNDS": raise InsufficientFundsError()

            # 2. Call RazorpayX 
            try:
                payout_res = self.payout_client.create_payout(uid, amount_paise, idempotency_key)
                self.db.collection('processed_withdrawals').document(idempotency_key).update({
                    'status': 'payout_pending',
                    'payoutId': payout_res.get('id', 'unknown')
                })
            except PayoutAPIError as e:
                # 3. Synchronous Failure -> Release Locked Funds
                tx_rollback = self.db.transaction()
                self._run_release_tx(tx_rollback, uid, amount_paise, idempotency_key, str(e))
                raise ExternalAPIError("Payout API failed, funds restored.")
        finally:
            self.lock_service.release_lock(f"withdraw:{uid}", lock_token)

    @firestore.transactional
    def _run_lock_tx(self, tx, uid, amount_paise, idempotency_key):
        return self.repo.lock_funds_tx(tx, uid, amount_paise, idempotency_key)

    @firestore.transactional
    def _run_release_tx(self, tx, uid, amount_paise, idempotency_key, reason):
        self.repo.release_locked_funds_tx(tx, uid, amount_paise, idempotency_key, reason)

    def process_payout_success(self, payout_id: str, idempotency_key: str, uid: str, amount_paise: int):
        lock_token = self.lock_service.acquire_lock(f"withdraw:{uid}")
        try:
            tx = self.db.transaction()
            self._run_complete_tx(tx, uid, amount_paise, idempotency_key, payout_id)
            
            # Send Notification
            from src.infrastructure.firebase.fcm_service import FCMService
            amount_inr = amount_paise / 100
            FCMService.send_to_topic(
                topic=f"pro_direct_{uid}",
                data={"type": "FINANCE_UPDATE", "status": "withdrawal_success"},
                title="Withdrawal Processed 💰",
                body=f"₹{amount_inr} has been sent to your bank account."
            )
        finally:
            self.lock_service.release_lock(f"withdraw:{uid}", lock_token)
            
    @firestore.transactional
    def _run_complete_tx(self, tx, uid, amount_paise, idempotency_key, payout_id):
        self.repo.complete_withdrawal_tx(tx, uid, amount_paise, idempotency_key, payout_id)

    def process_payout_failure(self, payout_id: str, idempotency_key: str, uid: str, amount_paise: int, reason: str):
        lock_token = self.lock_service.acquire_lock(f"withdraw:{uid}")
        try:
            tx = self.db.transaction()
            self._run_reverse_tx(tx, uid, amount_paise, idempotency_key, payout_id, reason)
        finally:
            self.lock_service.release_lock(f"withdraw:{uid}", lock_token)
            
    @firestore.transactional
    def _run_reverse_tx(self, tx, uid, amount_paise, idempotency_key, payout_id, reason):
        self.repo.reverse_withdrawal_tx(tx, uid, amount_paise, idempotency_key, payout_id, reason)
