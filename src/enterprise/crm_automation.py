from firebase_admin import firestore
from datetime import datetime, timedelta
from src.infrastructure.firebase.fcm_service import FCMService

class CRMAutomation:
    """
    Handles Advanced CRM functions like Win-back campaigns for dormant users.
    """
    def __init__(self, db: firestore.Client):
        self.db = db

    def run_winback_campaign(self, dormant_days: int = 90):
        """
        Cron job. Scans for users whose last completed job is older than dormant_days.
        Sends them a targeted FCM push notification.
        """
        cutoff_date = datetime.now() - timedelta(days=dormant_days)
        
        users_ref = self.db.collection('users')
        # We assume the user profile tracks 'lastJobCompletedAt'
        query = users_ref.where('lastJobCompletedAt', '<=', cutoff_date) \
                         .where('winbackSent', '==', False)
        docs = query.stream()
        
        batch = self.db.batch()
        notifications_sent = 0
        
        for doc in docs:
            user_uid = doc.id
            
            # Send Notification
            FCMService.send_to_topic(
                topic=f"user_{user_uid}",
                data={"type": "PROMO", "code": "MISSYOU20"},
                title="We miss you! 🛠️",
                body="It's been a while since your last service. Enjoy 20% off your next repair!"
            )
            notifications_sent += 1
            
            # Mark user so we don't spam them every day
            batch.update(doc.reference, {'winbackSent': True, 'winbackSentAt': firestore.SERVER_TIMESTAMP})
            
            if notifications_sent >= 400:
                batch.commit()
                batch = self.db.batch()
                
        if notifications_sent > 0:
            batch.commit()
            
        return notifications_sent
