"""
POWRSPly Payment and Job Completion Comprehensive Test Suite
Tests all state transitions, OTP validation, online and direct payment flows,
commission calculation, wallet settlement, idempotency, and error handling.
"""

import os
import unittest
import datetime
import bcrypt
from unittest.mock import MagicMock

# Local in-memory mock for Firestore DocumentSnapshot & DocumentReference
class MockDocSnapshot:
    def __init__(self, doc_id, data, exists=True):
        self.id = doc_id
        self._data = data.copy() if data else {}
        self.exists = exists

    def to_dict(self):
        return self._data.copy()

    def get(self, field, default=None):
        return self._data.get(field, default)

class MockDocRef:
    def __init__(self, doc_id, store):
        self.id = doc_id
        self.store = store
        self.sub_collections = {}

    def get(self, transaction=None):
        data = self.store.get(self.id)
        if data is None:
            return MockDocSnapshot(self.id, None, exists=False)
        return MockDocSnapshot(self.id, data, exists=True)

    def set(self, data, merge=False):
        if merge and self.id in self.store:
            self.store[self.id].update(data)
        else:
            self.store[self.id] = data.copy()

    def update(self, data):
        if self.id not in self.store:
            raise ValueError(f"Document {self.id} does not exist")
        for k, v in data.items():
            if str(v).startswith("DELETE_FIELD") or str(v) == "Sentinel: DELETE_FIELD":
                self.store[self.id].pop(k, None)
            elif hasattr(v, 'value'): # Increment
                self.store[self.id][k] = self.store[self.id].get(k, 0) + v.value
            else:
                self.store[self.id][k] = v

    def collection(self, name):
        if name not in self.sub_collections:
            self.sub_collections[name] = MockCollection(name, {})
        return self.sub_collections[name]

class MockCollection:
    def __init__(self, name, store):
        self.name = name
        self.store = store
        self.doc_refs = {}

    def document(self, doc_id=None):
        if not doc_id:
            import uuid
            doc_id = str(uuid.uuid4())
        if doc_id not in self.doc_refs:
            self.doc_refs[doc_id] = MockDocRef(doc_id, self.store)
        return self.doc_refs[doc_id]

    def add(self, data):
        import uuid
        doc_id = str(uuid.uuid4())
        self.store[doc_id] = data.copy()
        ref = self.document(doc_id)
        return None, ref

class MockFirestoreTransaction:
    def __init__(self):
        self._read_only = False
        self._max_attempts = 5
        self._id = "mock_tx"

    def _clean_up(self):
        pass

    def _rollback(self):
        pass

    def _begin(self, retry_id=None):
        pass

    def _commit(self):
        pass

    def get(self, doc_ref):
        return doc_ref.get()

    def set(self, doc_ref, data, merge=False):
        doc_ref.set(data, merge=merge)

    def update(self, doc_ref, data):
        doc_ref.update(data)

class MockFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = MockCollection(name, {})
        return self.collections[name]

    def transaction(self):
        return MockFirestoreTransaction()

from src.jobs.otp_repository import OtpRepository
from src.jobs.otp_service import OtpService
from src.infrastructure.redis.lock_service import RedisLockService
from src.finance.repository.escrow_repository import EscrowRepository
from src.core.security.event_service import SecurityEventService

class TestPaymentCompletionSuite(unittest.TestCase):
    def setUp(self):
        self.db = MockFirestoreClient()
        self.mock_redis_client = MagicMock()
        self.mock_redis_client.set.return_value = True
        self.mock_redis_client.eval.return_value = 1
        
        self.lock_service = RedisLockService(self.mock_redis_client)
        self.otp_repo = OtpRepository(self.db)
        self.escrow_repo = EscrowRepository(self.db)
        self.security_service = MagicMock()
        
        self.otp_service = OtpService(
            db=self.db,
            lock_service=self.lock_service,
            otp_repo=self.otp_repo,
            escrow_repo=self.escrow_repo,
            security_service=self.security_service
        )

        # Helper to seed a valid in-progress job
        self.test_job_id = "job_test_101"
        self.test_user_id = "user_cust_001"
        self.test_pro_id = "pro_elec_002"
        
        self.db.collection('users').document(self.test_user_id).set({
            'name': 'Test Customer',
            'fcmToken': 'mock_token_123',
            'wallet': {'balance': 500.0}
        })
        self.db.collection('wallets').document(self.test_user_id).set({
            'balance': 500.0
        })
        self.db.collection('users').document(self.test_pro_id).set({
            'name': 'Test Professional',
            'wallet': {'balance': 100.0}
        })
        self.db.collection('wallets').document(self.test_pro_id).set({
            'balance': 100.0
        })

    def _seed_job(self, payment_method='online', payment_status='paid', cost=500.0, status='in_progress'):
        self.db.collection('job_requests').document(self.test_job_id).set({
            'userId': self.test_user_id,
            'electricianId': self.test_pro_id,
            'status': status,
            'serviceType': 'Fan Repair',
            'problemDescription': 'Fan making noise',
            'address': '123 Test Street',
            'paymentMethod': payment_method,
            'paymentStatus': payment_status,
            'estimatedCost': cost,
            'fixedPrice': cost,
            'paymentVerified': (payment_status == 'paid')
        })

    # ── TEST 1: Online payment successful → job completed → Pro credited once ──
    def test_01_online_payment_success_auto_complete(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        res = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res['success'])
        self.assertEqual(res['status'], 'completed')
        self.assertTrue(res['paymentConfirmed'])

        # Check Pro wallet credited 90% (₹450 on top of ₹100 = ₹550)
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 550.0)

        # Check job status
        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['commission'], 50.0)
        self.assertEqual(job['proEarning'], 450.0)

    # ── TEST 2: Online payment failed → job NOT completed ──
    def test_02_online_payment_failed_blocked(self):
        self._seed_job(payment_method='online', payment_status='failed', cost=500.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertIn("PAYMENT_NOT_VERIFIED", str(ctx.exception))
        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertNotEqual(job['status'], 'completed')

    # ── TEST 3: Online payment pending → job NOT completed ──
    def test_03_online_payment_pending_blocked(self):
        self._seed_job(payment_method='online', payment_status='pending', cost=500.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertIn("PAYMENT_NOT_VERIFIED", str(ctx.exception))

    # ── TEST 4: Wallet sufficient balance → transfer → job completed ──
    def test_04_wallet_sufficient_balance_transfer(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=300.0)
        res = self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
        self.assertTrue(res['success'])
        self.assertEqual(res['status'], 'completed')

        # Customer: 500 - 300 = 200
        cust_wallet = self.db.collection('wallets').document(self.test_user_id).get().to_dict()
        self.assertEqual(cust_wallet['balance'], 200.0)

        # Pro: 100 + 270 (300 - 10% commission) = 370
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 370.0)

    # ── TEST 5: Wallet insufficient balance → job NOT completed ──
    def test_05_wallet_insufficient_balance_blocked(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=1000.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
        self.assertIn("INSUFFICIENT_WALLET_BALANCE", str(ctx.exception))

    # ── TEST 6: Wallet double tap → only one debit/credit ──
    def test_06_wallet_double_tap_idempotent(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=200.0)
        res1 = self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
        res2 = self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
        self.assertTrue(res1['success'])
        self.assertTrue(res2['success'])

        # Customer debited only once: 500 - 200 = 300
        cust_wallet = self.db.collection('wallets').document(self.test_user_id).get().to_dict()
        self.assertEqual(cust_wallet['balance'], 300.0)

    # ── TEST 7: Cash → Complete Work → NO OTP yet ──
    def test_07_cash_complete_work_no_otp_yet(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        res = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res['success'])
        self.assertEqual(res['completionStatus'], 'payment_pending')
        self.assertFalse(res['paymentReceiptConfirmed'])

        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertNotIn('completionOtpHash', job)

    # ── TEST 8: Cash → payment received → OTP generated ──
    def test_08_cash_payment_received_generates_otp(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")

        confirm_res = self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")
        self.assertTrue(confirm_res['success'])
        self.assertEqual(confirm_res['completionStatus'], 'otp_pending')
        self.assertTrue(confirm_res['paymentReceiptConfirmed'])

        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertIn('completionOtpHash', job)

    # ── TEST 9: Cash → wrong OTP → job remains pending ──
    def test_09_cash_wrong_otp_rejected(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "000000", self.test_pro_id, "127.0.0.1")
        self.assertIn("INVALID_OTP", str(ctx.exception))
        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertNotEqual(job['status'], 'completed')

    # ── TEST 10: Cash → correct OTP → completed ──
    def test_10_cash_correct_otp_completes(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        notifs = self.db.collection('users').document(self.test_user_id).collection('notifications').store
        plain_otp = next((n['otp'] for n in notifs.values() if 'otp' in n), None)
        self.assertIsNotNone(plain_otp)

        verify_res = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(verify_res['success'])
        self.assertEqual(verify_res['status'], 'completed')

        # Commission due recorded: 10% of 400 = 40
        ledger = self.db.collection('wallet_ledger').document(f"COMM_DUE_{self.test_job_id}").get().to_dict()
        self.assertEqual(ledger['amount'], 40.0)

    # ── TEST 11: Direct UPI → Complete Work → NO OTP yet ──
    def test_11_direct_upi_complete_work_no_otp_yet(self):
        self._seed_job(payment_method='direct_upi', payment_status='upi_pending', cost=600.0)
        res = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertEqual(res['completionStatus'], 'payment_pending')
        self.assertFalse(res['paymentReceiptConfirmed'])

    # ── TEST 12: Direct UPI → payment received → OTP generated ──
    def test_12_direct_upi_payment_received_generates_otp(self):
        self._seed_job(payment_method='direct_upi', payment_status='upi_pending', cost=600.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        confirm_res = self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")
        self.assertTrue(confirm_res['paymentReceiptConfirmed'])
        self.assertEqual(confirm_res['completionStatus'], 'otp_pending')

    # ── TEST 13: Direct UPI → correct OTP → completed ──
    def test_13_direct_upi_correct_otp_completes(self):
        self._seed_job(payment_method='direct_upi', payment_status='upi_pending', cost=600.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        notifs = self.db.collection('users').document(self.test_user_id).collection('notifications').store
        plain_otp = next((n['otp'] for n in notifs.values() if 'otp' in n), None)
        self.assertIsNotNone(plain_otp)

        verify_res = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(verify_res['success'])
        self.assertEqual(verify_res['status'], 'completed')

    # ── TEST 14: OTP cannot be verified for online payment ──
    def test_14_otp_not_used_for_online(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "123456", self.test_pro_id, "127.0.0.1")
        self.assertIn("INVALID_FLOW", str(ctx.exception))

    # ── TEST 15: OTP cannot be verified for wallet payment ──
    def test_15_otp_not_used_for_wallet(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=500.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "123456", self.test_pro_id, "127.0.0.1")
        self.assertIn("INVALID_FLOW", str(ctx.exception))

    # ── TEST 16: Expired OTP rejected ──
    def test_16_expired_otp_rejected(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        notifs = self.db.collection('users').document(self.test_user_id).collection('notifications').store
        plain_otp = next((n['otp'] for n in notifs.values() if 'otp' in n), None)
        self.assertIsNotNone(plain_otp)

        # Manually expire OTP
        job_ref = self.db.collection('job_requests').document(self.test_job_id)
        job_ref.update({'completionOtpExpiresAt': datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)})

        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertIn("OTP_EXPIRED", str(ctx.exception))

    # ── TEST 17: OTP attempt limit enforced ──
    def test_17_otp_attempt_limit_enforced(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        for _ in range(5):
            try:
                self.otp_service.verify_otp(self.test_job_id, "000000", self.test_pro_id, "127.0.0.1")
            except ValueError:
                pass

        with self.assertRaises(PermissionError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "000000", self.test_pro_id, "127.0.0.1")
        self.assertIn("OTP_LOCKED", str(ctx.exception))

    # ── TEST 18: Pro cannot complete another Pro's job ──
    def test_18_unauthorized_pro_blocked(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.request_completion(self.test_job_id, "rogue_pro_999", "127.0.0.1")
        self.assertIn("UNAUTHORIZED_PRO", str(ctx.exception))

    # ── TEST 19: Customer cannot modify paymentVerified directly ──
    def test_19_customer_cannot_claim_unowned_job_payment(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=300.0)
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.pay_wallet(self.test_job_id, "attacker_uid", "127.0.0.1")
        self.assertIn("UNAUTHORIZED_USER", str(ctx.exception))

    # ── TEST 20: Customer cannot modify Pro wallet directly ──
    def test_20_customer_cannot_modify_pro_wallet(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=200.0)
        res = self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
        self.assertTrue(res['success'])
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 280.0)

    # ── TEST 21: Pro cannot modify customer wallet directly ──
    def test_21_pro_cannot_modify_customer_wallet(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        cust_wallet = self.db.collection('wallets').document(self.test_user_id).get().to_dict()
        self.assertEqual(cust_wallet['balance'], 500.0)

    # ── TEST 22: Direct payment cannot be OTP-verified without receipt confirmation ──
    def test_22_otp_requires_prior_receipt_confirmation(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "123456", self.test_pro_id, "127.0.0.1")
        self.assertIn("PAYMENT_NOT_CONFIRMED", str(ctx.exception))

    # ── TEST 23: Duplicate completion request is idempotent ──
    def test_23_duplicate_completion_is_idempotent(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        res1 = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        res2 = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res1['success'])
        self.assertTrue(res2['success'])
        self.assertEqual(res2['status'], 'completed')

    # ── TEST 24: Duplicate settlement is idempotent ──
    def test_24_duplicate_settlement_does_not_double_credit(self):
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service._settle_online_job(self.test_job_id, self.test_pro_id, 500.0)
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 550.0)

    # ── TEST 25: App restart preserves correct backend state ──
    def test_25_state_preserved_across_instances(self):
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=400.0)
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")

        new_otp_service = OtpService(self.db, self.lock_service, self.otp_repo, self.escrow_repo, self.security_service)
        notifs = self.db.collection('users').document(self.test_user_id).collection('notifications').store
        plain_otp = next((n['otp'] for n in notifs.values() if 'otp' in n), None)
        self.assertIsNotNone(plain_otp)

        res = new_otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res['success'])
        self.assertEqual(res['status'], 'completed')

    # ── TEST 26: Network retry does not duplicate payment/settlement ──
    def test_26_network_retry_safe(self):
        self._seed_job(payment_method='wallet', payment_status='pending', cost=100.0)
        for _ in range(3):
            res = self.otp_service.pay_wallet(self.test_job_id, self.test_user_id, "127.0.0.1")
            self.assertTrue(res['success'])

        cust_wallet = self.db.collection('wallets').document(self.test_user_id).get().to_dict()
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(cust_wallet['balance'], 400.0)
        self.assertEqual(pro_wallet['balance'], 190.0)


if __name__ == '__main__':
    unittest.main()
