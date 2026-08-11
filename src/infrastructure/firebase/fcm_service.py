import firebase_admin
from firebase_admin import messaging
from typing import Dict, Any

class FCMService:
    @staticmethod
    def send_to_topic(topic: str, data: Dict[str, str], title: str = None, body: str = None, channel_id: str = None, sound: str = None) -> str:
        """Send a message to a specific topic after regex sanitization."""
        import re
        sanitized_topic = re.sub(r'[^a-zA-Z0-9-_.~%]', '', topic)
        if not sanitized_topic:
            raise ValueError("Invalid topic name")

        notif_type = data.get("type") if data else None
        is_job_request = notif_type in ["NEW_JOB_REQUEST", "NEW_JOB", "new_request"] or channel_id == "powrsply_job_requests_v1"
        
        target_channel = channel_id or ("powrsply_job_requests_v1" if is_job_request else "powrsply_general_v1")
        target_sound = sound or ("job_request_ring" if is_job_request else "powrsply_notification")

        android_config = messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id=target_channel,
                sound=target_sound,
            )
        )
        apns_config = messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound=f"{target_sound}.mp3")
            )
        )
        message = messaging.Message(
            data=data,
            topic=sanitized_topic,
            android=android_config,
            apns=apns_config,
        )
        if title and body:
            message.notification = messaging.Notification(title=title, body=body)
            
        try:
            res = messaging.send(message)
            print(f"[FCM SEND SUCCESS] type={notif_type} topic={sanitized_topic} messageId={res}")
            return res
        except Exception as e:
            print(f"[FCM SEND FAILURE] type={notif_type} topic={sanitized_topic} error={str(e)}")
            raise e

    @staticmethod
    def send_to_token(token: str, data: Dict[str, str], title: str = None, body: str = None, channel_id: str = None, sound: str = None) -> str:
        """Send a message to a specific device token."""
        notif_type = data.get("type") if data else None
        is_job_request = notif_type in ["NEW_JOB_REQUEST", "NEW_JOB", "new_request"] or channel_id == "powrsply_job_requests_v1"

        target_channel = channel_id or ("powrsply_job_requests_v1" if is_job_request else "powrsply_general_v1")
        target_sound = sound or ("job_request_ring" if is_job_request else "powrsply_notification")

        android_config = messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id=target_channel,
                sound=target_sound,
            )
        )
        apns_config = messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound=f"{target_sound}.mp3")
            )
        )
        message = messaging.Message(
            data=data,
            token=token,
            android=android_config,
            apns=apns_config,
        )
        if title and body:
            message.notification = messaging.Notification(title=title, body=body)
            
        try:
            res = messaging.send(message)
            print(f"[FCM SEND SUCCESS] type={notif_type} token={token[:15]}... messageId={res}")
            return res
        except Exception as e:
            print(f"[FCM SEND FAILURE] type={notif_type} token={token[:15]}... error={str(e)}")
            raise e
