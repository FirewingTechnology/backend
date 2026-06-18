from firebase_admin import firestore
from datetime import datetime

class BusinessMetricsAggregator:
    def __init__(self, db: firestore.Client):
        self.db = db

    def aggregate_daily_metrics(self, date_str: str = None):
        """
        Runs nightly via Cron. Rolls up all completed jobs for the given date.
        date_str format: YYYY-MM-DD
        """
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
            
        start_date = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_date = datetime.strptime(f"{date_str} 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        # Query completed jobs for the day
        jobs_ref = self.db.collection('jobs')
        query = jobs_ref.where('status', '==', 'completed_cleared') \
                        .where('completedAt', '>=', start_date) \
                        .where('completedAt', '<=', end_date)
                        
        docs = query.stream()
        
        total_gmv_paise = 0
        total_commission_paise = 0
        job_count = 0
        active_pros = set()
        active_users = set()
        
        for doc in docs:
            data = doc.to_dict()
            total_gmv_paise += data.get('amountPaise', 0)
            total_commission_paise += data.get('commissionPaise', 0)
            job_count += 1
            
            if data.get('proUid'):
                active_pros.add(data['proUid'])
            if data.get('userUid'):
                active_users.add(data['userUid'])
                
        # Write to Analytics Collection
        metrics_data = {
            'date': date_str,
            'gmvPaise': total_gmv_paise,
            'netRevenuePaise': total_commission_paise,
            'completedJobs': job_count,
            'activeProsCount': len(active_pros),
            'activeUsersCount': len(active_users),
            'averageJobValuePaise': (total_gmv_paise / job_count) if job_count > 0 else 0,
            'calculatedAt': firestore.SERVER_TIMESTAMP
        }
        
        self.db.collection('analytics_daily').document(date_str).set(metrics_data)
        return metrics_data
