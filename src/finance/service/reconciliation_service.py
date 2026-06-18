from google.cloud import firestore

class ReconciliationService:
    def __init__(self, db: firestore.Client):
        self.db = db

    def run_nightly_audit(self):
        mismatches = []
        batch_size = 500
        last_doc = None
        
        while True:
            query = self.db.collection('wallets').order_by('__name__').limit(batch_size)
            if last_doc:
                query = query.start_after(last_doc)
                
            wallets = list(query.stream())
            if not wallets:
                break
                
            for w in wallets:
                data = w.to_dict()
                calculated_total = data.get('availableBalance', 0) + data.get('escrowBalance', 0) + data.get('lockedBalance', 0)
                ledger_total = data.get('ledgerBalance', 0)
                
                if calculated_total != ledger_total:
                    mismatches.append({
                        'uid': w.id, 
                        'expected': ledger_total, 
                        'actual': calculated_total
                    })
            
            last_doc = wallets[-1]
                
        report = {
            'mismatchCount': len(mismatches),
            'mismatches': mismatches,
            'status': 'CRITICAL_ALERT' if mismatches else 'CLEAN',
            'timestamp': firestore.SERVER_TIMESTAMP
        }
        self.db.collection('reconciliation_reports').add(report)
        return report
