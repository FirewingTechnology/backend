import math
from typing import Dict, Any
from src.core.security.event_service import SecurityEventService

class FraudDetector:
    def __init__(self, security_service: SecurityEventService):
        self.security_service = security_service

    def detect_self_booking(self, user_uid: str, pro_uid: str, job_id: str):
        """Immediately flag if a Pro attempts to book themselves."""
        if user_uid == pro_uid:
            self.security_service.log_event(
                event_type="FRAUD_SELF_BOOKING",
                severity="CRITICAL",
                source_ip="backend_detector",
                details={
                    "jobId": job_id,
                    "proUid": pro_uid,
                    "reason": "Pro attempted to accept their own job request."
                }
            )
            # In a real system, you might immediately suspend the account here.

    def detect_location_spoofing(self, pro_uid: str, old_location: Dict[str, float], new_location: Dict[str, float], time_delta_seconds: int):
        """
        Flag if a Pro moves an impossible distance in a short time.
        """
        if time_delta_seconds <= 0:
            return

        # Haversine distance
        R = 6371000 # meters
        lat1, lon1 = math.radians(old_location['lat']), math.radians(old_location['lng'])
        lat2, lon2 = math.radians(new_location['lat']), math.radians(new_location['lng'])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance = R * c
        
        speed_mps = distance / time_delta_seconds
        speed_kmh = speed_mps * 3.6
        
        # If speed > 500 km/h in a city, likely GPS spoofing
        if speed_kmh > 500:
            self.security_service.log_event(
                event_type="FRAUD_LOCATION_SPOOFING",
                severity="HIGH",
                source_ip="backend_detector",
                details={
                    "proUid": pro_uid,
                    "speed_kmh": speed_kmh,
                    "distance_m": distance,
                    "time_s": time_delta_seconds
                }
            )

    def detect_rapid_withdrawals(self, pro_uid: str, withdrawal_count_24h: int):
        """Flag accounts withdrawing money abnormally fast (potential takeover)."""
        if withdrawal_count_24h >= 4:
            self.security_service.log_event(
                event_type="FRAUD_RAPID_WITHDRAWALS",
                severity="HIGH",
                source_ip="backend_detector",
                details={
                    "proUid": pro_uid,
                    "withdrawal_count_24h": withdrawal_count_24h,
                    "action_taken": "Withdrawal lock placed."
                }
            )
            # You would return a boolean here to block the withdrawal transaction
            return True
        return False
