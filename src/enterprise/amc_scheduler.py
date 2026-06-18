from firebase_admin import firestore
from datetime import datetime, timedelta
import uuid

class AMCScheduler:
    """
    Automates job creation for long-running Enterprise Annual Maintenance Contracts.
    """
    def __init__(self, db: firestore.Client):
        self.db = db

    def run_daily_schedule(self):
        """
        Cron job running at 2:00 AM. 
        Scans all active AMC contracts and auto-generates a Job document 
        if a maintenance visit is scheduled for tomorrow.
        """
        now = datetime.now()
        tomorrow_start = datetime(now.year, now.month, now.day) + timedelta(days=1)
        tomorrow_end = tomorrow_start + timedelta(days=1, seconds=-1)
        
        contracts_ref = self.db.collection('amc_contracts')
        # Fetch active contracts that have scheduled visits remaining
        query = contracts_ref.where('status', '==', 'active')
        docs = query.stream()
        
        batch = self.db.batch()
        jobs_created = 0
        
        for doc in docs:
            contract = doc.to_dict()
            # Logic: We assume the contract has an array of 'scheduledDates'
            # In production, this would be an array of timestamps
            scheduled_dates = contract.get('scheduledDates', [])
            
            for index, visit_date in enumerate(scheduled_dates):
                if isinstance(visit_date, datetime) and tomorrow_start <= visit_date <= tomorrow_end:
                    # Time to generate the job!
                    job_id = str(uuid.uuid4())
                    job_ref = self.db.collection('jobs').document(job_id)
                    
                    job_data = {
                        'jobId': job_id,
                        'userUid': contract.get('companyId'),
                        'category': contract.get('category', 'amc_general'),
                        'status': 'searching', # Will trigger Matching Engine
                        'isAmc': True,
                        'amcId': doc.id,
                        'visitNumber': index + 1,
                        'location': contract.get('location', {}),
                        'amountPaise': 0, # AMC jobs are pre-paid
                        'scheduledFor': visit_date,
                        'createdAt': firestore.SERVER_TIMESTAMP
                    }
                    
                    batch.set(job_ref, job_data)
                    jobs_created += 1
                    
                    # Update contract completed visits counter later upon actual completion
                    break # Only one visit per day max
                    
        if jobs_created > 0:
            batch.commit()
            
        return jobs_created
