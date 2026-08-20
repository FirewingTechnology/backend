import time
from typing import List, Dict, Any, Optional
from redis import Redis
from src.infrastructure.firebase.fcm_service import FCMService
from src.marketplace.presence_service import PresenceService

class MatchingEngine:
    """
    Handles massive-scale geographic matching using Redis GEO commands 
    and Server-Authoritative presence validation.
    """
    def __init__(self, redis_client: Redis, presence_service: Optional[PresenceService] = None):
        self.redis = redis_client
        self.presence_service = presence_service

    def update_pro_location(self, pro_uid: str, lat: float, lng: float, tier: str):
        """
        Pros push their location here. We store them in a single 'pros_geo' key 
        and maintain a separate hash for their current tier to enable weighted dispatch.
        """
        # Add to spatial index
        self.redis.geoadd("pros_geo", (lng, lat, pro_uid))
        # Add to tier hash
        self.redis.hset("pros_tiers", pro_uid, tier)
        # Add to timestamp hash to filter out offline pros
        self.redis.hset("pros_last_seen", pro_uid, int(time.time()))

    def run_memory_cleanup(self):
        """Cron task run every hour to evict pros who have been offline for 24h."""
        current_time = int(time.time())
        all_pros = self.redis.hgetall("pros_last_seen")
        
        for pro_uid_bytes, last_seen_bytes in all_pros.items():
            if current_time - int(last_seen_bytes.decode()) > 86400: # 24 hours
                pro_uid = pro_uid_bytes.decode()
                self.redis.zrem("pros_geo", pro_uid)
                self.redis.hdel("pros_tiers", pro_uid)
                self.redis.hdel("pros_last_seen", pro_uid)

    def _filter_active_candidates(self, candidate_list: List[str]) -> List[str]:
        """
        Performs final atomic server-authoritative presence verification 
        immediately before dispatching a job, eliminating race conditions.
        """
        if not self.presence_service:
            return candidate_list

        active_list = []
        for pro_uid in candidate_list:
            is_active, reason = self.presence_service.validate_active_presence(pro_uid)
            if not is_active:
                print(f"[MatchingEngine] PRE_DISPATCH_FILTERED: Dropping Pro {pro_uid[:6]}*** before dispatch. Reason: {reason}")
                continue
            active_list.append(pro_uid)
        return active_list

    def dispatch_job(self, job_id: str, lat: float, lng: float, job_data: Dict[str, Any]):
        """
        Performs the Weighted Batch Dispatch algorithm with atomic pre-dispatch presence validation.
        Batch 1: Platinum & Gold (1km)
        Batch 2: Silver (2km) 
        Batch 3: Bronze (5km)
        """
        # 1. Fetch all pros within 5km from Redis (very fast in-memory query)
        nearby_pros = self.redis.georadius("pros_geo", lng, lat, 5, unit="km", withdist=True)
        
        current_time = int(time.time())
        
        batch_1 = [] # Platinum/Gold < 1km
        batch_2 = [] # Silver < 2km
        batch_3 = [] # Bronze < 5km
        
        user_uid = job_data.get('userId') or job_data.get('userUid') or ""

        for pro in nearby_pros:
            pro_uid = pro[0].decode('utf-8')
            distance = pro[1]
            
            # Anti-fraud: Never dispatch a job to the user who requested it
            if user_uid and pro_uid == user_uid:
                continue

            # Check if online (server-authoritative lease: last seen <= 90 seconds ago)
            last_seen_bytes = self.redis.hget("pros_last_seen", pro_uid)
            if not last_seen_bytes:
                print(f"[MatchingEngine] STALE_PRO_FILTERED: Pro {pro_uid[:6]}*** has no last_seen record. Skipping dispatch.")
                continue
                
            last_seen_sec = int(last_seen_bytes.decode('utf-8'))
            if current_time - last_seen_sec > 90:
                print(f"[MatchingEngine] STALE_PRO_FILTERED: Pro {pro_uid[:6]}*** last heartbeat was {current_time - last_seen_sec}s ago (>90s). Skipping dispatch.")
                continue
                
            tier_bytes = self.redis.hget("pros_tiers", pro_uid)
            tier = tier_bytes.decode('utf-8') if tier_bytes else 'Bronze'
            
            if tier in ['Platinum', 'Gold'] and distance <= 1.0:
                batch_1.append(pro_uid)
            elif tier == 'Silver' and distance <= 2.0:
                batch_2.append(pro_uid)
            elif distance <= 5.0:
                batch_3.append(pro_uid)
                
        # 2. FINAL ATOMIC PRE-DISPATCH CHECK
        batch_1 = self._filter_active_candidates(batch_1)
        batch_2 = self._filter_active_candidates(batch_2)
        batch_3 = self._filter_active_candidates(batch_3)

        payload = {
            "type": "NEW_JOB_REQUEST",
            "jobId": job_id,
            "amountPaise": str(job_data.get('amountPaise', 0)),
            "category": job_data.get('category', 'general')
        }
        
        for pro_uid in batch_1:
            FCMService.send_to_topic(
                topic=f"pro_{pro_uid}",
                data=payload,
                title="⚡ Priority Job Dispatch!",
                body="You have priority access to a nearby electrical job.",
                channel_id="powrsply_job_requests_v1",
                sound="job_request_ring"
            )
            
        for pro_uid in batch_2:
            FCMService.send_to_topic(
                topic=f"pro_{pro_uid}",
                data=payload,
                title="⚡ New Job Available!",
                body="An electrical job is available near your location.",
                channel_id="powrsply_job_requests_v1",
                sound="job_request_ring"
            )
            
        for pro_uid in batch_3:
            FCMService.send_to_topic(
                topic=f"pro_{pro_uid}",
                data=payload,
                title="⚡ New Job Available!",
                body="An electrical job is available near your location.",
                channel_id="powrsply_job_requests_v1",
                sound="job_request_ring"
            )
            
        return len(batch_1) + len(batch_2) + len(batch_3)
