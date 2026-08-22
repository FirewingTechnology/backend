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
        sub_store = {}
        return MockCollection(name, sub_store)

class MockCollection:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def document(self, doc_id=None):
        if not doc_id:
            import uuid
            doc_id = str(uuid.uuid4())
        return MockDocRef(doc_id, self.store)

    def add(self, data):
        import uuid
        doc_id = str(uuid.uuid4())
        self.store[doc_id] = data.copy()
        return None, MockDocRef(doc_id, self.store)

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
            self.collections[name] = {}
        return MockCollection(name, self.collections[name])

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
        self.security_service = SecurityEventService(self.db)
        
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
            'wallet': {'balance': 0.0}
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

    def test_01_online_payment_valid_otp_success(self):
        """Scenario 1: Online payment (paid) + valid OTP -> Successful completion & 10% commission deduction."""
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.assertEqual(len(plain_otp), 6)
        
        result = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['paymentConfirmed'])

        # Check job doc
        job = self.db.collection('job_requests').document(self.test_job_id).get().to_dict()
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['commission'], 50.0) # 10% of 500
        self.assertEqual(job['proEarning'], 450.0) # 90% of 500
        self.assertEqual(job['settlementStatus'], 'settled')

        # Check Pro wallet balance (100 initial + 450 earning = 550)
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 550.0)

        # Check ledger record
        ledger = self.db.collection('wallet_ledger').document(f"SETTLE_{self.test_job_id}").get().to_dict()
        self.assertEqual(ledger['amount'], 450.0)
        self.assertEqual(ledger['commissionDeducted'], 50.0)

    def test_02_online_payment_unpaid_blocked(self):
        """Scenario 2: Online payment unpaid + valid OTP -> Holds in payment_pending, blocks completion."""
        self._seed_job(payment_method='online', payment_status='pending', cost=500.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        result = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        
        self.assertFalse(result['success'])
        self.assertEqual(result['code'], 'PAYMENT_NOT_VERIFIED')
        self.assertEqual(result['status'], 'payment_pending')
        
        # Verify wallet was NOT credited
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 100.0)

    def test_03_online_payment_failed_blocked(self):
        """Scenario 3: Online payment failed + valid OTP -> Blocks completion."""
        self._seed_job(payment_method='online', payment_status='failed', cost=200.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        result = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        
        self.assertFalse(result['success'])
        self.assertEqual(result['code'], 'PAYMENT_NOT_VERIFIED')

    def test_04_invalid_otp_rejected(self):
        """Scenario 4: Valid job + Invalid OTP -> Throws INVALID_OTP."""
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        
        self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.verify_otp(self.test_job_id, "000000", self.test_pro_id, "127.0.0.1")
        self.assertIn("INVALID_OTP", str(ctx.exception))

    def test_05_cash_payment_flow(self):
        """Scenario 6: Cash payment + valid OTP + Pro confirmation -> SUCCESS, commission due recorded."""
        self._seed_job(payment_method='cash', payment_status='cash_pending', cost=300.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        
        # Verify OTP -> moves to payment_pending
        otp_res = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(otp_res['success'])
        self.assertEqual(otp_res['status'], 'payment_pending')
        
        # Pro confirms cash collected
        conf_res = self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")
        self.assertTrue(conf_res['success'])
        self.assertEqual(conf_res['status'], 'completed')
        
        # Commission due ledger record
        comm_ledger = self.db.collection('wallet_ledger').document(f"COMM_DUE_{self.test_job_id}").get().to_dict()
        self.assertEqual(comm_ledger['amount'], 30.0) # 10% of 300
        self.assertEqual(comm_ledger['type'], 'commission_due')

    def test_06_direct_upi_flow(self):
        """Scenario 8: Direct UPI + valid OTP + Pro confirmation -> SUCCESS."""
        self._seed_job(payment_method='upi', payment_status='upi_pending', cost=400.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        
        conf_res = self.otp_service.confirm_direct_payment(self.test_job_id, self.test_pro_id, True, "127.0.0.1")
        self.assertTrue(conf_res['success'])
        self.assertEqual(conf_res['status'], 'completed')

    def test_07_idempotent_double_settlement(self):
        """Scenario 9 & 10: Double completion invocation does not double-credit wallet."""
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        
        plain_otp = self.otp_service.request_completion(self.test_job_id, self.test_pro_id, "127.0.0.1")
        
        # Request 1
        res1 = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res1['success'])
        self.assertEqual(res1['status'], 'completed')
        
        # Request 2 (duplicate tap)
        res2 = self.otp_service.verify_otp(self.test_job_id, plain_otp, self.test_pro_id, "127.0.0.1")
        self.assertTrue(res2['success'])
        self.assertEqual(res2['status'], 'completed')

        # Balance must only have credited once (100 + 450 = 550)
        pro_wallet = self.db.collection('wallets').document(self.test_pro_id).get().to_dict()
        self.assertEqual(pro_wallet['balance'], 550.0)

    def test_08_unauthorized_pro_blocked(self):
        """Scenario 11 & 12: Unauthorized Pro attempting to complete another pro's job is rejected."""
        self._seed_job(payment_method='online', payment_status='paid', cost=500.0)
        
        with self.assertRaises(ValueError) as ctx:
            self.otp_service.request_completion(self.test_job_id, "pro_intruder_999", "127.0.0.1")
        self.assertIn("UNAUTHORIZED_PRO", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()
