import time
import datetime
import math
import uuid
import firebase_admin
from firebase_admin import credentials, firestore
from unittest.mock import MagicMock

import os
cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Cred init failed: {e}")
        firebase_admin.initialize_app()

db = firestore.client()

from src.marketplace.presence_service import PresenceService, PRESENCE_LEASE_DURATION_SEC, HEARTBEAT_INTERVAL_SEC

print("=================================================================")
print("POWRSPLY SERVER-AUTHORITATIVE PRESENCE HARDENING TEST SUITE")
print("=================================================================")

# Setup mock redis
mock_redis = MagicMock()
redis_store = {}
def mock_get(k): return redis_store.get(k)
def mock_set(k, v): redis_store[k] = v.encode('utf-8') if isinstance(v, str) else v
def mock_hget(h, k): return redis_store.get(f"{h}:{k}")
def mock_hset(h, k, v): redis_store[f"{h}:{k}"] = str(v).encode('utf-8')
def mock_hdel(h, k): redis_store.pop(f"{h}:{k}", None)
def mock_zrem(h, k): redis_store.pop(f"{h}:{k}", None)
def mock_delete(k): redis_store.pop(k, None)

mock_redis.get.side_effect = mock_get
mock_redis.set.side_effect = mock_set
mock_redis.hget.side_effect = mock_hget
mock_redis.hset.side_effect = mock_hset
mock_redis.hdel.side_effect = mock_hdel
mock_redis.zrem.side_effect = mock_zrem
mock_redis.delete.side_effect = mock_delete

presence_service = PresenceService(db, mock_redis)

# Test pro user setup
test_pro_uid = f"test_pro_{uuid.uuid4().hex[:8]}"
db.collection('users').document(test_pro_uid).set({
    'name': 'Master Electrician Test',
    'phone': '+919999999999',
    'role': 'electrician',
    'verificationStatus': 'approved',
    'accountStatus': 'verified',
    'isOnline': False,
    'isAvailable': False,
})

print(f"\n[TEST SETUP] Created test pro: {test_pro_uid}")

# 1. TEST GO ONLINE
print("\n--- TEST 1: Pro taps GO ONLINE ---")
success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
assert success and code == 200, f"go_online failed: {res}"
session_1 = res['deviceSessionId']
print(f"PASS: Granted 90s server lease (Session: {session_1}, Expiry: {res['presenceExpiresAt']})")

# Verify active presence
is_active, reason = presence_service.validate_active_presence(test_pro_uid)
assert is_active, f"Pro should be active: {reason}"
print(f"PASS: Authoritative validation -> ACTIVE ({reason})")

# 2. TEST VALID HEARTBEAT
print("\n--- TEST 2: Pro sends valid 25s heartbeat with Session 1 ---")
success, res, code = presence_service.record_heartbeat(test_pro_uid, session_1, 18.5205, 73.8568)
assert success and code == 200, f"heartbeat failed: {res}"
print(f"PASS: Heartbeat accepted, lease renewed to: {res['presenceExpiresAt']}")

# 3. TEST OLD / HIJACKED SESSION REJECTION
print("\n--- TEST 3: Stale / Hijacked Session Heartbeat ---")
fake_stale_session = "old_stale_session_12345"
success, res, code = presence_service.record_heartbeat(test_pro_uid, fake_stale_session, 18.5205, 73.8568)
assert not success and code == 403, f"Expected 403 rejection, got {code}"
print(f"PASS: Stale session correctly REJECTED with 403: {res['error']}")

# 4. TEST MULTI-DEVICE SESSION SUPERSEDING
print("\n--- TEST 4: New Login on Device 2 supersedes Device 1 ---")
success, res, code = presence_service.go_online(test_pro_uid, 18.5210, 73.8570)
assert success and code == 200
session_2 = res['deviceSessionId']
assert session_2 != session_1
print(f"PASS: Session 2 established ({session_2})")

# Device 1 tries heartbeat again
success, res, code = presence_service.record_heartbeat(test_pro_uid, session_1, 18.5205, 73.8568)
assert not success and code == 403, f"Old session 1 should be rejected, got {code}"
print("PASS: Session 1 cannot renew presence after Session 2 became active.")

# 5. TEST GO OFFLINE
print("\n--- TEST 5: Pro taps GO OFFLINE ---")
success, res, code = presence_service.go_offline(test_pro_uid, session_2)
assert success and code == 200
is_active, reason = presence_service.validate_active_presence(test_pro_uid)
assert not is_active
print(f"PASS: Pro set OFFLINE immediately -> Rejected by validator: {reason}")

# 6. TEST SUSPENDED PRO REJECTION
print("\n--- TEST 6: Suspended Pro attempts to go online ---")
db.collection('users').document(test_pro_uid).update({'accountStatus': 'suspended'})
success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
assert not success and code == 403
print(f"PASS: Suspended Pro blocked from going online: {res['error']}")

# Clean up test user
db.collection('users').document(test_pro_uid).delete()
db.collection('electricians').document(test_pro_uid).delete()

print("\n=================================================================")
print("ALL SERVER-AUTHORITATIVE PRESENCE TESTS PASSED SUCCESSFULLY (6/6)")
print("=================================================================")
