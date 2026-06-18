import firebase_admin
from firebase_admin import messaging
from typing import Dict, Any

class FCMService:
    @staticmethod
    def send_to_topic(topic: str, data: Dict[str, str], title: str = None, body: str = None) -> str:
        """Send a message to a specific topic after regex sanitization."""
        import re
        sanitized_topic = re.sub(r'[^a-zA-Z0-9-_.~%]', '', topic)
        if not sanitized_topic:
            raise ValueError("Invalid topic name")
            
        message = messaging.Message(
            data=data,
            topic=sanitized_topic,
        )
        if title and body:
            message.notification = messaging.Notification(title=title, body=body)
            
        return messaging.send(message)

    @staticmethod
    def send_to_token(token: str, data: Dict[str, str], title: str = None, body: str = None) -> str:
        """Send a message to a specific device token."""
        message = messaging.Message(
            data=data,
            token=token,
        )
        if title and body:
            message.notification = messaging.Notification(title=title, body=body)
            
        return messaging.send(message)
