import time
import datetime
import uuid
import logging
from typing import Optional, Dict, Any, Tuple
from google.cloud import firestore

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = 25
PRESENCE_LEASE_DURATION_SEC = 90
LOCATION_STALE_TIMEOUT_SEC = 300

class PresenceService:
    """
    Server-Authoritative Presence Service.
    Enforces that presence leases (isOnline, isAvailable, presenceExpiresAt, lastHeartbeatAt)
    are calculated and recorded exclusively with server authority and timestamps.
    """

    def __init__(self, db, redis_client=None):
        self.db = db
        self.redis = redis_client

    def _get_active_session_id(self, uid: str) -> Optional[str]:
        """Gets the active deviceSessionId from Redis or Firestore."""
        if self.redis:
            try:
                session_bytes = self.redis.get(f"presence:session:{uid}")
                if session_bytes:
                    return session_bytes.decode('utf-8') if isinstance(session_bytes, bytes) else str(session_bytes)
            except Exception as e:
                logger.warning(f"Redis session lookup failed for {uid}: {e}")

        # Fallback to Firestore
        try:
            doc = self.db.collection('users').document(uid).get()
            if doc.exists:
                return doc.to_dict().get('deviceSessionId')
        except Exception as e:
            logger.error(f"Firestore session lookup failed for {uid}: {e}")
        return None

    def _set_active_session_id(self, uid: str, session_id: str):
        """Stores the active deviceSessionId in Redis."""
        if self.redis:
            try:
                self.redis.set(f"presence:session:{uid}", session_id)
            except Exception as e:
                logger.warning(f"Redis set session failed for {uid}: {e}")

    def go_online(self, uid: str, lat: Optional[float] = None, lng: Optional[float] = None) -> Tuple[bool, Dict[str, Any], int]:
        """
        Transition Pro to ONLINE state.
        Validates Pro eligibility, creates a new unique deviceSessionId, and calculates server lease.
        """
        user_ref = self.db.collection('users').document(uid)
        user_snap = user_ref.get()

        if not user_snap.exists:
            return False, {"error": "USER_NOT_FOUND", "message": "Pro user not found."}, 404

        user_data = user_snap.to_dict() or {}

        # 1. Pro Qualification & Suspension Check
        a_status = str(user_data.get('accountStatus', '')).lower()
        v_status = str(user_data.get('verificationStatus', '')).lower()

        if a_status == 'suspended' or v_status == 'suspended':
            return False, {"error": "PRO_SUSPENDED", "message": "Your account is currently suspended."}, 403

        # Generate a unique server-authoritative deviceSessionId
        new_session_id = f"{uid}_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
        self._set_active_session_id(uid, new_session_id)

        now_utc = datetime.datetime.utcnow()
        lease_expiry = now_utc + datetime.timedelta(seconds=PRESENCE_LEASE_DURATION_SEC)

        presence_data: Dict[str, Any] = {
            'isOnline': True,
            'isAvailable': True,
            'deviceSessionId': new_session_id,
            'presenceExpiresAt': lease_expiry,
            'lastHeartbeatAt': firestore.SERVER_TIMESTAMP,
            'updatedAt': firestore.SERVER_TIMESTAMP,
        }

        if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
            presence_data['currentLocation'] = firestore.GeoPoint(lat, lng)
            presence_data['location'] = firestore.GeoPoint(lat, lng)
            presence_data['lastLocationAt'] = firestore.SERVER_TIMESTAMP
            presence_data['lastLocationUpdate'] = firestore.SERVER_TIMESTAMP

        # Atomically update users/{uid} and electricians/{uid}
        batch = self.db.batch()
        batch.set(user_ref, presence_data, merge=True)
        
        elec_ref = self.db.collection('electricians').document(uid)
        batch.set(elec_ref, presence_data, merge=True)
        batch.commit()

        # Update Redis cache
        if self.redis:
            try:
                self.redis.hset("pros_last_seen", uid, int(time.time()))
                if lat is not None and lng is not None:
                    self.redis.geoadd("pros_geo", (lng, lat, uid))
                    # Default tier if available
                    tier = user_data.get('rankingTier', 'Bronze')
                    self.redis.hset("pros_tiers", uid, tier)
            except Exception as e:
                logger.warning(f"Redis update failed in go_online for {uid}: {e}")

        logger.info(f"[Presence] PRO_ONLINE: Pro {uid[:6]}*** online with lease {PRESENCE_LEASE_DURATION_SEC}s (Session: {new_session_id})")

        return True, {
            "success": True,
            "deviceSessionId": new_session_id,
            "presenceExpiresAt": lease_expiry.isoformat() + "Z",
            "leaseDurationSec": PRESENCE_LEASE_DURATION_SEC,
            "heartbeatIntervalSec": HEARTBEAT_INTERVAL_SEC
        }, 200

    def record_heartbeat(self, uid: str, session_id: str, lat: Optional[float] = None, lng: Optional[float] = None) -> Tuple[bool, Dict[str, Any], int]:
        """
        Authoritatively renews Pro presence lease if deviceSessionId matches current server session.
        """
        if not session_id:
            return False, {"error": "MISSING_SESSION_ID", "message": "deviceSessionId is required."}, 400

        active_session = self._get_active_session_id(uid)
        if not active_session or active_session != session_id:
            logger.warning(f"[Presence] STALE_SESSION_REJECTED: Heartbeat from Pro {uid[:6]}*** rejected. Active: {active_session}, Provided: {session_id}")
            return False, {
                "error": "STALE_SESSION_REJECTED",
                "message": "Heartbeat rejected. This device session is no longer active."
            }, 403

        # Check account suspension status
        user_ref = self.db.collection('users').document(uid)
        user_snap = user_ref.get()
        if not user_snap.exists:
            return False, {"error": "USER_NOT_FOUND", "message": "Pro user not found."}, 404

        user_data = user_snap.to_dict() or {}
        a_status = str(user_data.get('accountStatus', '')).lower()
        v_status = str(user_data.get('verificationStatus', '')).lower()

        if a_status == 'suspended' or v_status == 'suspended':
            # Force Pro offline if suspended
            self.go_offline(uid, session_id)
            return False, {"error": "PRO_SUSPENDED", "message": "Account is suspended."}, 403

        now_utc = datetime.datetime.utcnow()
        lease_expiry = now_utc + datetime.timedelta(seconds=PRESENCE_LEASE_DURATION_SEC)

        update_data: Dict[str, Any] = {
            'isOnline': True,
            'isAvailable': True,
            'presenceExpiresAt': lease_expiry,
            'lastHeartbeatAt': firestore.SERVER_TIMESTAMP,
            'updatedAt': firestore.SERVER_TIMESTAMP,
        }

        if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
            update_data['currentLocation'] = firestore.GeoPoint(lat, lng)
            update_data['location'] = firestore.GeoPoint(lat, lng)
            update_data['lastLocationAt'] = firestore.SERVER_TIMESTAMP
            update_data['lastLocationUpdate'] = firestore.SERVER_TIMESTAMP

        batch = self.db.batch()
        batch.set(user_ref, update_data, merge=True)
        elec_ref = self.db.collection('electricians').document(uid)
        batch.set(elec_ref, update_data, merge=True)
        batch.commit()

        if self.redis:
            try:
                self.redis.hset("pros_last_seen", uid, int(time.time()))
                if lat is not None and lng is not None:
                    self.redis.geoadd("pros_geo", (lng, lat, uid))
            except Exception as e:
                logger.warning(f"Redis update failed in heartbeat for {uid}: {e}")

        return True, {
            "success": True,
            "presenceExpiresAt": lease_expiry.isoformat() + "Z",
            "leaseDurationSec": PRESENCE_LEASE_DURATION_SEC
        }, 200

    def go_offline(self, uid: str, session_id: Optional[str] = None) -> Tuple[bool, Dict[str, Any], int]:
        """
        Transition Pro to OFFLINE state.
        Immediately expires presence lease and removes Pro from active spatial indices.
        """
        now_utc = datetime.datetime.utcnow()
        expired_lease = now_utc - datetime.timedelta(seconds=1)

        offline_data = {
            'isOnline': False,
            'isAvailable': False,
            'presenceExpiresAt': expired_lease,
            'lastSeen': firestore.SERVER_TIMESTAMP,
            'updatedAt': firestore.SERVER_TIMESTAMP,
        }

        batch = self.db.batch()
        batch.set(self.db.collection('users').document(uid), offline_data, merge=True)
        batch.set(self.db.collection('electricians').document(uid), offline_data, merge=True)
        batch.commit()

        if self.redis:
            try:
                self.redis.zrem("pros_geo", uid)
                self.redis.hdel("pros_last_seen", uid)
                self.redis.delete(f"presence:session:{uid}")
            except Exception as e:
                logger.warning(f"Redis cleanup failed in go_offline for {uid}: {e}")

        logger.info(f"[Presence] PRO_OFFLINE: Pro {uid[:6]}*** set to OFFLINE.")
        return True, {"success": True, "isOnline": False}, 200

    def validate_active_presence(self, uid: str) -> Tuple[bool, str]:
        """
        Strict server-authoritative validation of Pro active presence.
        Used at the final pre-dispatch stage to eliminate race conditions.
        """
        now_utc = datetime.datetime.utcnow()
        current_time_sec = int(time.time())

        # Check Redis quick index if available
        if self.redis:
            try:
                last_seen_bytes = self.redis.hget("pros_last_seen", uid)
                if not last_seen_bytes:
                    return False, "NO_REDIS_LAST_SEEN"
                last_seen_sec = int(last_seen_bytes.decode('utf-8'))
                if current_time_sec - last_seen_sec > PRESENCE_LEASE_DURATION_SEC:
                    return False, f"REDIS_LAST_SEEN_STALE ({current_time_sec - last_seen_sec}s > {PRESENCE_LEASE_DURATION_SEC}s)"
            except Exception as e:
                logger.warning(f"Redis check in validate_active_presence failed for {uid}: {e}")

        # Authoritative Firestore check
        try:
            doc = self.db.collection('users').document(uid).get()
            if not doc.exists:
                return False, "DOC_NOT_FOUND"

            data = doc.to_dict() or {}

            if not data.get('isOnline') or not data.get('isAvailable', True):
                return False, "ONLINE_OR_AVAILABLE_FALSE"

            a_status = str(data.get('accountStatus', '')).lower()
            v_status = str(data.get('verificationStatus', '')).lower()
            if a_status == 'suspended' or v_status == 'suspended':
                return False, "PRO_SUSPENDED"

            # Check presence lease expiration
            presence_expires_at = data.get('presenceExpiresAt')
            if isinstance(presence_expires_at, datetime.datetime):
                # Ensure UTC comparison
                exp = presence_expires_at.replace(tzinfo=None)
                if exp <= now_utc:
                    return False, f"LEASE_EXPIRED ({exp} <= {now_utc})"
            elif presence_expires_at is None:
                return False, "NO_PRESENCE_EXPIRES_AT"

            # Check heartbeat timestamp freshness
            last_hb = data.get('lastHeartbeatAt')
            if isinstance(last_hb, datetime.datetime):
                hb_time = last_hb.replace(tzinfo=None)
                if (now_utc - hb_time).total_seconds() > PRESENCE_LEASE_DURATION_SEC:
                    return False, f"HEARTBEAT_STALE ({(now_utc - hb_time).total_seconds()}s)"

            # Check location freshness if location is used
            last_loc = data.get('lastLocationAt') or data.get('lastLocationUpdate')
            if isinstance(last_loc, datetime.datetime):
                loc_time = last_loc.replace(tzinfo=None)
                if (now_utc - loc_time).total_seconds() > LOCATION_STALE_TIMEOUT_SEC:
                    return False, f"LOCATION_STALE ({(now_utc - loc_time).total_seconds()}s)"

            return True, "ACTIVE"

        except Exception as e:
            logger.error(f"validate_active_presence error for {uid}: {e}")
            return False, f"ERROR: {e}"

    def reconcile_expired_presence(self) -> int:
        """
        Background Reconciliation Task (Layer 2 Cleanup).
        Finds all documents marked isOnline == True whose presence lease has expired,
        and atomically resets them to isOnline = False, isAvailable = False.
        """
        now_utc = datetime.datetime.utcnow()
        expired_count = 0

        try:
            # Query online users using existing isOnline index
            online_users = self.db.collection('users') \
                .where('isOnline', '==', True) \
                .limit(200) \
                .get()

            for doc in online_users:
                data = doc.to_dict() or {}
                exp = data.get('presenceExpiresAt')
                is_expired = False
                if isinstance(exp, datetime.datetime):
                    if exp.replace(tzinfo=None) <= now_utc:
                        is_expired = True
                elif exp is None:
                    is_expired = True

                if is_expired:
                    uid = doc.id
                    logger.info(f"[PresenceReconciliation] PRESENCE_EXPIRED: Pro {uid[:6]}*** lease expired. Resetting to OFFLINE.")
                    self.go_offline(uid)
                    expired_count += 1

            # Also sweep electricians collection
            online_elecs = self.db.collection('electricians') \
                .where('isOnline', '==', True) \
                .limit(200) \
                .get()

            for doc in online_elecs:
                data = doc.to_dict() or {}
                exp = data.get('presenceExpiresAt')
                is_expired = False
                if isinstance(exp, datetime.datetime):
                    if exp.replace(tzinfo=None) <= now_utc:
                        is_expired = True
                elif exp is None:
                    is_expired = True

                if is_expired:
                    uid = doc.id
                    if uid not in [d.id for d in online_users]:
                        logger.info(f"[PresenceReconciliation] PRESENCE_EXPIRED: Electrician doc {uid[:6]}*** lease expired. Resetting to OFFLINE.")
                        self.go_offline(uid)
                        expired_count += 1
            # Redis cache sweep: remove any pros with last_seen older than 90s from pros_geo
            if self.redis:
                try:
                    current_time_sec = int(time.time())
                    all_pros = self.redis.hgetall("pros_last_seen")
                    for pro_uid_bytes, last_seen_bytes in all_pros.items():
                        pro_uid = pro_uid_bytes.decode('utf-8') if isinstance(pro_uid_bytes, bytes) else str(pro_uid_bytes)
                        last_seen_sec = int(last_seen_bytes.decode('utf-8') if isinstance(last_seen_bytes, bytes) else last_seen_bytes)
                        if current_time_sec - last_seen_sec > PRESENCE_LEASE_DURATION_SEC:
                            self.redis.zrem("pros_geo", pro_uid)
                            self.redis.hdel("pros_last_seen", pro_uid)
                            self.redis.hdel("pros_tiers", pro_uid)
                            logger.info(f"[PresenceReconciliation] REDIS_EVICTED: Pro {pro_uid[:6]}*** evicted from Redis cache (stale {current_time_sec - last_seen_sec}s).")
                except Exception as re:
                    logger.warning(f"Redis sweep warning during reconciliation: {re}")

        except Exception as e:
            logger.error(f"Error during presence reconciliation: {e}")

        return expired_count
