import math
from typing import Dict, Any
from src.core.security.event_service import SecurityEventService

class FraudDetector:
    def __init__(self, security_service: SecurityEventService):
        self.security_service = security_service

    def detect_self_booking(self, user_uid: str, pro_uid: str, job_id: str, user_phone: str = "", pro_phone: str = "") -> bool:
        """Immediately flag and block Pro for 1 hour if a Pro attempts self-booking."""
        is_self = False
        if user_uid and pro_uid and user_uid == pro_uid:
            is_self = True
        elif user_phone and pro_phone and user_phone == pro_phone:
            is_self = True

        if is_self:
            from datetime import datetime, timedelta
            from firebase_admin import firestore
            
            suspended_until = datetime.utcnow() + timedelta(hours=1)
            reason = "Suspicious Activity: Self-booking attempt detected. Account blocked for 1 hour."
            
            # 1. Log critical security event
            try:
                self.security_service.log_event(
                    event_type="FRAUD_SELF_BOOKING",
                    severity="CRITICAL",
                    source_ip="backend_detector",
                    details={
                        "jobId": job_id,
                        "proUid": pro_uid,
                        "userUid": user_uid,
                        "reason": reason,
                        "suspendedUntil": suspended_until.isoformat()
                    }
                )
            except Exception as e:
                print(f"[SECURITY_LOG_ERROR] {e}")

            # 2. Block Pro account in Firestore for 1 hour
            try:
                db = firestore.client()
                target_uid = pro_uid or user_uid
                update_payload = {
                    'accountStatus': 'suspended',
                    'verificationStatus': 'suspended',
                    'suspendedUntil': suspended_until,
                    'blockReason': reason,
                    'isOnline': False,
                    'isAvailable': False,
                    'flaggedCount': firestore.Increment(1),
                    'updatedAt': firestore.SERVER_TIMESTAMP
                }
                db.collection('users').document(target_uid).set(update_payload, merge=True)
                db.collection('electricians').document(target_uid).set(update_payload, merge=True)
                
                # Write to admin audit logs
                db.collection('admin_logs').add({
                    'adminEmail': 'SYSTEM_FRAUD_DETECTOR',
                    'action': 'FRAUD_SELF_BOOKING_BLOCKED',
                    'details': f'Pro {target_uid} attempted self-booking for job {job_id}. Pro account blocked for 1 hour.',
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"[ACCOUNT_SUSPENSION_ERROR] {e}")

            return True
        return False

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
