import json
import time
from datetime import datetime, timezone
from firebase_admin import messaging

class VoiceService:
    def __init__(self, db, redis_client=None):
        self.db = db
        self.redis = redis_client

    def _set_call_state_redis(self, call_id: str, state_data: dict, ttl: int = 7200):
        if self.redis:
            try:
                self.redis.setex(f"call:{call_id}", ttl, json.dumps(state_data))
            except Exception as e:
                print(f"[VoiceService V4] Redis set error: {e}")

    def _get_call_state_redis(self, call_id: str) -> dict:
        if self.redis:
            try:
                data = self.redis.get(f"call:{call_id}")
                if data:
                    return json.loads(data)
            except Exception as e:
                print(f"[VoiceService V4] Redis get error: {e}")
        return None

    def create_call(self, call_id: str, booking_id: str, caller_id: str, callee_id: str, caller_name: str = "User") -> dict:
        now_str = datetime.now(timezone.utc).isoformat()
        call_data = {
            "callId": call_id,
            "bookingId": booking_id,
            "customerId": caller_id,
            "professionalId": callee_id,
            "callerId": caller_id,
            "calleeId": callee_id,
            "callerName": caller_name,
            "status": "calling",
            "quality": "good",
            "createdAt": now_str,
            "startedAt": now_str,
            "answeredAt": None,
            "endedAt": None,
            "duration": 0,
            "endedBy": None
        }

        # Save to Redis
        self._set_call_state_redis(call_id, call_data)

        # Save to Firestore call_logs
        try:
            self.db.collection('call_logs').document(call_id).set(call_data)
        except Exception as e:
            print(f"[VoiceService V4] Firestore create_call error: {e}")

        # FCM Push Notification to Callee
        self.send_fcm_call_notification(callee_id, {
            "type": "incoming_call",
            "callId": call_id,
            "bookingId": booking_id,
            "callerId": caller_id,
            "callerName": caller_name
        })

        return call_data

    def set_ringing(self, call_id: str) -> dict:
        call_data = self._get_call_state_redis(call_id) or {}
        call_data["status"] = "ringing"
        self._set_call_state_redis(call_id, call_data)
        try:
            self.db.collection('call_logs').document(call_id).update({"status": "ringing"})
        except Exception:
            pass
        return call_data

    def accept_call(self, call_id: str) -> dict:
        now_str = datetime.now(timezone.utc).isoformat()
        call_data = self._get_call_state_redis(call_id) or {}
        call_data["status"] = "accepted"
        call_data["answeredAt"] = now_str

        self._set_call_state_redis(call_id, call_data)

        try:
            self.db.collection('call_logs').document(call_id).update({
                "status": "accepted",
                "answeredAt": now_str
            })
        except Exception as e:
            print(f"[VoiceService V4] Firestore accept_call error: {e}")

        caller_id = call_data.get("callerId")
        if caller_id:
            self.send_fcm_call_notification(caller_id, {
                "type": "call_accepted",
                "callId": call_id
            })

        return call_data

    def reject_call(self, call_id: str, rejected_by: str = None, is_busy: bool = False) -> dict:
        now_str = datetime.now(timezone.utc).isoformat()
        call_data = self._get_call_state_redis(call_id) or {}
        final_status = "busy" if is_busy else "rejected"
        
        call_data["status"] = final_status
        call_data["endedAt"] = now_str
        call_data["endedBy"] = rejected_by

        self._set_call_state_redis(call_id, call_data, ttl=300)

        try:
            self.db.collection('call_logs').document(call_id).update({
                "status": final_status,
                "endedAt": now_str,
                "endedBy": rejected_by
            })
        except Exception as e:
            print(f"[VoiceService V4] Firestore reject_call error: {e}")

        caller_id = call_data.get("callerId")
        if caller_id:
            self.send_fcm_call_notification(caller_id, {
                "type": "call_rejected" if not is_busy else "call_busy",
                "callId": call_id
            })

        return call_data

    def end_call(self, call_id: str, ended_by: str = None, status: str = "ended") -> dict:
        now_str = datetime.now(timezone.utc).isoformat()
        call_data = self._get_call_state_redis(call_id) or {}
        
        answered_at = call_data.get("answeredAt")
        duration = 0
        if answered_at:
            try:
                start_dt = datetime.fromisoformat(answered_at.replace("Z", "+00:00"))
                end_dt = datetime.now(timezone.utc)
                duration = max(0, int((end_dt - start_dt).total_seconds()))
            except Exception:
                duration = 0

        call_data["status"] = status
        call_data["endedAt"] = now_str
        call_data["duration"] = duration
        call_data["endedBy"] = ended_by

        self._set_call_state_redis(call_id, call_data, ttl=300)

        try:
            self.db.collection('call_logs').document(call_id).update({
                "status": status,
                "endedAt": now_str,
                "duration": duration,
                "endedBy": ended_by
            })
        except Exception as e:
            print(f"[VoiceService V4] Firestore end_call error: {e}")

        peer_id = call_data.get("calleeId") if ended_by == call_data.get("callerId") else call_data.get("callerId")
        if peer_id:
            self.send_fcm_call_notification(peer_id, {
                "type": "call_ended" if status == "ended" else "call_cancelled",
                "callId": call_id,
                "duration": str(duration)
            })

        return call_data

    def update_call_quality(self, call_id: str, quality: str):
        try:
            self.db.collection('call_logs').document(call_id).update({"quality": quality})
        except Exception:
            pass

    def get_history(self, user_id: str) -> list:
        try:
            docs = self.db.collection('call_logs').where('callerId', '==', user_id).stream()
            logs = [d.to_dict() for d in docs]
            docs2 = self.db.collection('call_logs').where('calleeId', '==', user_id).stream()
            logs.extend([d.to_dict() for d in docs2])
            
            logs.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
            return logs
        except Exception as e:
            print(f"[VoiceService V4] Error fetching history: {e}")
            return []

    def send_fcm_call_notification(self, user_id: str, data_payload: dict):
        try:
            user_doc = self.db.collection('users').document(user_id).get()
            if not user_doc.exists:
                user_doc = self.db.collection('electricians').document(user_id).get()
            
            if user_doc.exists:
                fcm_token = user_doc.to_dict().get('fcmToken')
                if fcm_token:
                    title = "Incoming Voice Call" if data_payload.get('type') == 'incoming_call' else "Voice Call Update"
                    body = f"Call from {data_payload.get('callerName', 'User')}" if data_payload.get('type') == 'incoming_call' else "Call update"
                    message = messaging.Message(
                        data=data_payload,
                        token=fcm_token,
                        notification=messaging.Notification(title=title, body=body)
                    )
                    messaging.send(message)
        except Exception as e:
            print(f"[VoiceService V4] FCM send error for user {user_id}: {e}")
