from firebase_admin import firestore
from typing import Dict, Any

class TrustEngine:
    def __init__(self, db: firestore.Client):
        self.db = db

    def calculate_score(self, metrics: Dict[str, Any], penalties: Dict[str, Any]) -> int:
        """
        Calculates a 0-100 trust score based on approved business logic weights.
        """
        score = 50.0 # Base Score

        # Positive Signals
        avg_rating = float(metrics.get('averageRating', 0.0))
        repeat_rate = float(metrics.get('repeatCustomerRate', 0.0))
        completion_rate = float(metrics.get('completionRate', 0.0))

        score += (avg_rating * 5.0) # Max +25
        score += (repeat_rate * 10.0) # Max +10
        score += (completion_rate * 10.0) # Max +10

        # Negative Signals
        cancel_rate = float(metrics.get('cancellationRate', 0.0))
        disputes_lost = int(penalties.get('disputesLost', 0))
        security_violations = int(penalties.get('securityViolations', 0))

        score -= (cancel_rate * 15.0)
        score -= (disputes_lost * 20.0)
        score -= (security_violations * 30.0)

        # Clamp between 0 and 100
        final_score = max(0, min(100, int(score)))
        return final_score

    def determine_tier(self, score: int) -> str:
        if score >= 90:
            return 'Platinum'
        elif score >= 75:
            return 'Gold'
        elif score >= 50:
            return 'Silver'
        else:
            return 'Bronze'

    def recalculate_all_scores(self):
        """Cron job to recalculate scores for all active professionals."""
        profiles_ref = self.db.collection('pro_profiles')
        # In a massive dataset, you'd use batched reads or pagination
        docs = profiles_ref.stream()
        
        batch = self.db.batch()
        count = 0
        
        for doc in docs:
            data = doc.to_dict()
            metrics = data.get('metrics', {})
            penalties = data.get('penalties', {})
            
            new_score = self.calculate_score(metrics, penalties)
            new_tier = self.determine_tier(new_score)
            
            if data.get('trustScore') != new_score or data.get('tier') != new_tier:
                batch.update(doc.reference, {
                    'trustScore': new_score,
                    'tier': new_tier,
                    'lastScoredAt': firestore.SERVER_TIMESTAMP
                })
                count += 1
                
                # Commit every 400 writes to respect Firestore batch limits
                if count >= 400:
                    batch.commit()
                    batch = self.db.batch()
                    count = 0
                    
        if count > 0:
            batch.commit()
