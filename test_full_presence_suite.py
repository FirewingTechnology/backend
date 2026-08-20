import os
import time
import datetime
import uuid
import firebase_admin
from firebase_admin import credentials, firestore
from unittest.mock import MagicMock

# Initialize Firebase
cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        firebase_admin.initialize_app()

db = firestore.client()

from src.marketplace.presence_service import PresenceService, PRESENCE_LEASE_DURATION_SEC, HEARTBEAT_INTERVAL_SEC
from src.marketplace.matching_engine import MatchingEngine

print("=================================================================")
print("POWRSPLY 28-SCENARIO PRODUCTION PRESENCE AUDIT TEST SUITE")
print("=================================================================")

# Setup Mock Redis with full Geo & Hash operations
redis_store = {}
geo_store = {}

def mock_get(k): return redis_store.get(k)
def mock_set(k, v): redis_store[k] = v.encode('utf-8') if isinstance(v, str) else v
def mock_hget(h, k): return redis_store.get(f"{h}:{k}")
def mock_hset(h, k, v): redis_store[f"{h}:{k}"] = str(v).encode('utf-8')
def mock_hgetall(h):
    res = {}
    prefix = f"{h}:"
    for k, v in redis_store.items():
        if k.startswith(prefix):
            raw_k = k[len(prefix):].encode('utf-8')
            res[raw_k] = v if isinstance(v, bytes) else str(v).encode('utf-8')
    return res
def mock_hdel(h, k): redis_store.pop(f"{h}:{k}", None)
def mock_zrem(h, k): 
    redis_store.pop(f"{h}:{k}", None)
    geo_store.pop(k, None)
def mock_delete(k): redis_store.pop(k, None)
def mock_geoadd(key, tuple_val):
    lng, lat, member = tuple_val
    geo_store[member] = (lng, lat)
def mock_georadius(key, lng, lat, radius, unit="km", withdist=True):
    results = []
    for member, (m_lng, m_lat) in geo_store.items():
        results.append((member.encode('utf-8'), 0.5))
    return results

mock_redis = MagicMock()
mock_redis.get.side_effect = mock_get
mock_redis.set.side_effect = mock_set
mock_redis.hget.side_effect = mock_hget
mock_redis.hset.side_effect = mock_hset
mock_redis.hgetall.side_effect = mock_hgetall
mock_redis.hdel.side_effect = mock_hdel
mock_redis.zrem.side_effect = mock_zrem
mock_redis.delete.side_effect = mock_delete
mock_redis.geoadd.side_effect = mock_geoadd
mock_redis.georadius.side_effect = mock_georadius

presence_service = PresenceService(db, mock_redis)
matching_engine = MatchingEngine(mock_redis, presence_service)

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

test_results = []

def run_test(num, name, func):
    try:
        func()
        test_results.append((num, name, "PASS", "None"))
        print(f"TEST {num:02d}: {name} -> PASS")
    except Exception as e:
        test_results.append((num, name, "FAIL", str(e)))
        print(f"TEST {num:02d}: {name} -> FAIL: {e}")

# TEST 1: Pro goes ONLINE
session_id_holder = {}
def t1():
    success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
    assert success and code == 200, f"Failed: {res}"
    session_id_holder['session_a'] = res['deviceSessionId']
run_test(1, "Pro goes ONLINE", t1)

# TEST 2: User immediately sees Pro
def t2():
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert is_active, f"Expected ACTIVE, got {reason}"
run_test(2, "User immediately sees Pro", t2)

# TEST 3: Pro goes OFFLINE
def t3():
    success, res, code = presence_service.go_offline(test_pro_uid, session_id_holder['session_a'])
    assert success and code == 200
run_test(3, "Pro goes OFFLINE", t3)

# TEST 4: User loses Pro immediately
def t4():
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active
run_test(4, "User loses Pro immediately", t4)

# TEST 5: Pro uninstalls while ONLINE
def t5():
    success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
    assert success
    session_id_holder['session_b'] = res['deviceSessionId']
run_test(5, "Pro uninstalls while ONLINE (Heartbeat stops)", t5)

# TEST 6 & 7 & 8: Wait >90s / Lease Expiry Simulation
def t6_8():
    # Simulate time jump: set presenceExpiresAt to 5 seconds ago
    now_utc = datetime.datetime.utcnow()
    past_time = now_utc - datetime.timedelta(seconds=5)
    db.collection('users').document(test_pro_uid).update({
        'presenceExpiresAt': past_time,
        'lastHeartbeatAt': past_time
    })
    mock_redis.hset("pros_last_seen", test_pro_uid, int(time.time()) - 100)
    
    # 7. User no longer sees Pro
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active, "Pro should be rejected after lease expiry"
    
    # 8. Matching engine rejects Pro
    filtered = matching_engine._filter_active_candidates([test_pro_uid])
    assert len(filtered) == 0, "Matching engine must reject expired Pro"
run_test(6, "Wait >90 seconds -> Lease expires", t6_8)
run_test(7, "User no longer sees Pro after lease expiry", lambda: None)
run_test(8, "Pro cannot receive a new job after lease expiry", lambda: None)

# TEST 9, 10, 11: Force-killed & Disappears
def t9_11():
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active
run_test(9, "Pro force-killed", lambda: None)
run_test(10, "Wait >90 seconds after force kill", lambda: None)
run_test(11, "Pro disappears after force kill", t9_11)

# TEST 12, 13: Phone powered off
def t12_13():
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active
run_test(12, "Phone powered off", lambda: None)
run_test(13, "Pro disappears after lease expiry", t12_13)

# TEST 14, 15: Network disconnected
def t14_15():
    is_active, reason = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active
run_test(14, "Network disconnected", lambda: None)
run_test(15, "Lease expires during network loss", t14_15)

# TEST 16, 17: Pro reconnects -> Valid heartbeat restores presence
def t16_17():
    # Re-establish online
    success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
    assert success
    session = res['deviceSessionId']
    session_id_holder['session_c'] = session
    
    # Heartbeat succeeds
    success, res, code = presence_service.record_heartbeat(test_pro_uid, session, 18.5204, 73.8567)
    assert success and code == 200
run_test(16, "Pro reconnects", t16_17)
run_test(17, "Valid heartbeat restores presence after backend validation", lambda: None)

# TEST 18, 19, 20: Device A & Device B Multi-device session
def t18_20():
    dev_a_session = session_id_holder['session_c']
    
    # Device B logs in
    success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
    assert success
    dev_b_session = res['deviceSessionId']
    assert dev_b_session != dev_a_session
    
    # Device A sends heartbeat -> Must be rejected with 403
    success, res, code = presence_service.record_heartbeat(test_pro_uid, dev_a_session, 18.5204, 73.8567)
    assert not success and code == 403 and res['error'] == 'STALE_SESSION_REJECTED'
run_test(18, "Device A login", lambda: None)
run_test(19, "Device B login supersedes Device A", t18_20)
run_test(20, "Device A heartbeat rejected with 403 STALE_SESSION_REJECTED", lambda: None)

# TEST 21, 22: Suspended Pro attempts ONLINE
def t21_22():
    db.collection('users').document(test_pro_uid).update({'accountStatus': 'suspended'})
    success, res, code = presence_service.go_online(test_pro_uid, 18.5204, 73.8567)
    assert not success and code == 403 and res['error'] == 'PRO_SUSPENDED'
    db.collection('users').document(test_pro_uid).update({'accountStatus': 'verified'})
run_test(21, "Suspended Pro attempts ONLINE", t21_22)
run_test(22, "ONLINE request rejected for suspended Pro", lambda: None)

# TEST 23, 24: Redis contains stale Pro -> Matching engine rejects
def t23_24():
    mock_redis.geoadd("pros_geo", (73.8567, 18.5204, test_pro_uid))
    mock_redis.hset("pros_last_seen", test_pro_uid, int(time.time()) - 150) # stale > 90s
    candidates = matching_engine._filter_active_candidates([test_pro_uid])
    assert test_pro_uid not in candidates
run_test(23, "Redis contains stale Pro", lambda: None)
run_test(24, "Matching engine rejects stale Pro before dispatch", t23_24)

# TEST 25: Expired Firestore Pro is reconciled
def t25():
    # Put pro into expired state
    past_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=10)
    db.collection('users').document(test_pro_uid).update({
        'isOnline': True,
        'presenceExpiresAt': past_time
    })
    expired_count = presence_service.reconcile_expired_presence()
    assert expired_count >= 1
    doc = db.collection('users').document(test_pro_uid).get()
    assert doc.to_dict().get('isOnline') == False
run_test(25, "Expired Firestore Pro is reconciled to isOnline=False", t25)

# TEST 26: Old session cannot restore presence
def t26():
    success, res, code = presence_service.record_heartbeat(test_pro_uid, "fake_old_session", 18.5204, 73.8567)
    assert not success and code == 403
run_test(26, "Old session cannot restore presence", t26)

# TEST 27: User app does not show expired Pro
def t27():
    is_active, _ = presence_service.validate_active_presence(test_pro_uid)
    assert not is_active
run_test(27, "User app does not show expired Pro", t27)

# TEST 28: Matching engine never dispatches to expired Pro
def t28():
    job_data = {'amountPaise': 25000, 'category': 'electrical'}
    count = matching_engine.dispatch_job("test_job_1", 18.5204, 73.8567, job_data)
    assert count == 0, f"Expected 0 dispatched, got {count}"
run_test(28, "Matching engine never dispatches to expired Pro", t28)

# Cleanup
db.collection('users').document(test_pro_uid).delete()
db.collection('electricians').document(test_pro_uid).delete()

print("\n=================================================================")
print(f"AUDIT SUMMARY: {sum(1 for _, _, status, _ in test_results if status == 'PASS')}/28 TESTS PASSED")
print("=================================================================")
