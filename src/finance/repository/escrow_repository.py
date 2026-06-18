from google.cloud import firestore
import datetime

class EscrowRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def lock_job_funds_tx(self, tx: firestore.Transaction, job_id: str, pro_uid: str, amount_paise: int, commission_paise: int):
        wallet_ref = self.db.collection('wallets').document(pro_uid)
        escrow_ref = self.db.collection('escrow_holds').document(job_id)
        
        if escrow_ref.get(transaction=tx).exists: 
            return "ALREADY_HELD"

        net_payout = amount_paise - commission_paise
        
        tx.update(wallet_ref, {
            'escrowBalance': firestore.Increment(net_payout),
            'ledgerBalance': firestore.Increment(net_payout),
            'totalEarned': firestore.Increment(net_payout),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        
        release_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
        
        tx.set(escrow_ref, {
            'jobId': job_id, 
            'proUid': pro_uid, 
            'amountPaise': net_payout,
            'commissionPaise': commission_paise, 
            'status': 'held',
            'releaseAt': release_time
        })
        return "SUCCESS"

    def release_escrow_tx(self, tx: firestore.Transaction, job_id: str):
        escrow_ref = self.db.collection('escrow_holds').document(job_id)
        doc = escrow_ref.get(transaction=tx)
        
        if not doc.exists or doc.get('status') != 'held': 
            return "ALREADY_RELEASED"
            
        pro_uid = doc.get('proUid')
        amount = doc.get('amountPaise')
        wallet_ref = self.db.collection('wallets').document(pro_uid)
        
        tx.update(wallet_ref, {
            'escrowBalance': firestore.Increment(-amount),
            'availableBalance': firestore.Increment(amount),
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        
        tx.update(escrow_ref, {
            'status': 'released', 
            'releasedAt': firestore.SERVER_TIMESTAMP
        })
        return "SUCCESS"
