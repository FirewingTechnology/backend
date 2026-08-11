import json
import logging
from datetime import datetime, timezone
from firebase_admin import messaging

logger = logging.getLogger(__name__)

class ChatService:
    def __init__(self, db, redis_client):
        self.db = db
        self.redis = redis_client
        self.PRESENCE_TTL = 30
        self.TYPING_TTL = 5
        self.ACTIVE_CHAT_TTL = 7200 # 2 hours

    def authorize_and_create_room(self, booking_id: str, customer_id: str, pro_id: str) -> dict:
        """
        Validates that booking status is accepted or in_progress, and creates/gets chat room.
        Room ID format: jobId_customerId_professionalId
        """
        try:
            if not booking_id or not customer_id or not pro_id:
                raise Exception("booking_id, customer_id, and pro_id are required.")

            job_doc = self.db.collection('job_requests').document(booking_id).get()
            if not job_doc.exists:
                raise Exception("Booking not found.")

            job_data = job_doc.to_dict()
            valid_statuses = ['accepted', 'on_the_way', 'arrived', 'in_progress']
            if job_data.get('status') not in valid_statuses:
                raise Exception("Chat room creation is only allowed for active jobs (Accepted or In Progress).")

            room_id = f"{booking_id}_{customer_id}_{pro_id}"
            room_ref = self.db.collection('chat_rooms').document(room_id)
            room_doc = room_ref.get()

            now_str = datetime.now(timezone.utc).isoformat()
            if not room_doc.exists:
                room_data = {
                    "roomId": room_id,
                    "bookingId": booking_id,
                    "customerId": customer_id,
                    "professionalId": pro_id,
                    "status": "active",
                    "lastMessage": "",
                    "lastMessageType": "text",
                    "lastMessageTime": now_str,
                    "lastSender": "",
                    "customerUnread": 0,
                    "professionalUnread": 0,
                    "createdAt": now_str,
                    "updatedAt": now_str
                }
                room_ref.set(room_data)
            else:
                room_data = room_doc.to_dict()

            return {"success": True, "roomId": room_id, "room": room_data}
        except Exception as e:
            logger.error(f"Error authorizing room for booking {booking_id}: {str(e)}")
            raise e

    def set_presence(self, user_id: str, status: str, room_id: str = None) -> dict:
        try:
            if self.redis:
                if status == "online":
                    self.redis.setex(f"presence:{user_id}", self.PRESENCE_TTL, "online")
                    if room_id:
                        self.redis.setex(f"active:{user_id}", self.ACTIVE_CHAT_TTL, room_id)
                else:
                    self.redis.delete(f"presence:{user_id}")
                    self.redis.delete(f"active:{user_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Error setting presence for {user_id}: {str(e)}")
            raise e

    def set_typing(self, room_id: str, user_id: str, typing_type: str = "typing") -> dict:
        try:
            if self.redis:
                if typing_type in ["typing", "recording"]:
                    self.redis.setex(f"typing:{room_id}:{user_id}", self.TYPING_TTL, typing_type)
                else:
                    self.redis.delete(f"typing:{room_id}:{user_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"Error setting typing for {user_id} in {room_id}: {str(e)}")
            raise e

    def get_presence_and_typing(self, room_id: str, user_id: str, peer_id: str) -> dict:
        try:
            is_online = False
            is_typing = False
            typing_status = None

            if self.redis:
                is_online = bool(self.redis.get(f"presence:{peer_id}"))
                raw_typing = self.redis.get(f"typing:{room_id}:{peer_id}")
                if raw_typing:
                    is_typing = True
                    typing_status = raw_typing.decode('utf-8') if isinstance(raw_typing, bytes) else str(raw_typing)

            return {
                "success": True,
                "peerId": peer_id,
                "isOnline": is_online,
                "isTyping": is_typing,
                "typingStatus": typing_status
            }
        except Exception as e:
            logger.error(f"Error fetching presence for peer {peer_id}: {str(e)}")
            return {"success": False, "isOnline": False, "isTyping": False}

    def notify_message(self, room_id: str, message_id: str, sender_id: str, receiver_id: str, message_text: str, sender_name: str, msg_type: str = "text") -> dict:
        try:
            if self.redis:
                # 1. Rate Limiting Check: max 60 messages per minute
                rate_limit_key = f"ratelimit:msg:{sender_id}"
                count = self.redis.incr(rate_limit_key)
                if count == 1:
                    self.redis.expire(rate_limit_key, 60)
                if count > 60:
                    raise Exception("Rate limit exceeded")

                # 2. Check if receiver is actively viewing this room
                active_chat = self.redis.get(f"active:{receiver_id}")
                if active_chat:
                    active_str = active_chat.decode('utf-8') if isinstance(active_chat, bytes) else str(active_chat)
                    if active_str == room_id:
                        return {"notified": False, "reason": "user_active_in_chat"}

            # 3. Trigger FCM Push Notification
            user_doc = self.db.collection('users').document(receiver_id).get()
            if not user_doc.exists:
                user_doc = self.db.collection('electricians').document(receiver_id).get()
            if not user_doc.exists:
                return {"notified": False, "reason": "user_not_found"}

            user_data = user_doc.to_dict()
            fcm_token = user_data.get('fcmToken')
            if not fcm_token:
                return {"notified": False, "reason": "no_fcm_token"}

            snippet = message_text if msg_type == 'text' else f"[{msg_type.upper()}] Message"

            msg = messaging.Message(
                notification=messaging.Notification(
                    title=f"New message from {sender_name}",
                    body=snippet,
                ),
                data={
                    "type": "chat",
                    "roomId": room_id,
                    "messageId": message_id
                },
                android=messaging.AndroidConfig(
                    notification=messaging.AndroidNotification(
                        channel_id="powrsply_v3_channel",
                        sound="powrsply_notification",
                    )
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound="powrsply_notification.mp3")
                    )
                ),
                token=fcm_token
            )
            response = messaging.send(msg)
            return {"notified": True, "message_id": response}
        except Exception as e:
            logger.error(f"Error sending notification for message {message_id}: {str(e)}")
            raise e

    def moderate_message(self, message_id: str, room_id: str, action: str) -> dict:
        try:
            if action == 'delete':
                self.db.collection('chat_rooms').document(room_id).collection('messages').document(message_id).update({
                    'deleted': 'for_everyone',
                    'text': 'This message was removed by an admin.',
                    'image': None,
                    'voice': None,
                    'document': None
                })
            return {"success": True}
        except Exception as e:
            logger.error(f"Error moderating message {message_id}: {str(e)}")
            raise e
