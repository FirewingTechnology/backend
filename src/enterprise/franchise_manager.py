from firebase_admin import firestore
from typing import Dict, Any

class FranchiseManager:
    """
    Handles territory-based revenue allocation for the Franchise model.
    """
    def __init__(self, db: firestore.Client):
        self.db = db

    def allocate_revenue_split(self, job_id: str, platform_commission_paise: int, geohash: str):
        """
        Called during Escrow clearance.
        Determines if the job occurred in a franchised territory.
        If so, splits the platform_commission_paise.
        """
        if platform_commission_paise <= 0:
            return None
            
        # 1. Find Franchise Territory via Geohash Prefix Match
        # In production, we'd query territories where geohash_prefix == job.geohash[:5]
        territories_ref = self.db.collection('territories')
        prefix = geohash[:5]
        query = territories_ref.where('geohashPrefixes', 'array_contains', prefix).limit(1)
        docs = list(query.stream())
        
        if not docs:
            # Platform keeps 100%
            return {
                "platformCut": platform_commission_paise,
                "franchiseCut": 0,
                "franchiseUid": None
            }
            
        territory = docs[0].to_dict()
        franchise_uid = territory.get('franchiseOwnerUid')
        revenue_share_percentage = float(territory.get('revenueSharePercentage', 0.20)) # 20% to franchise default
        
        franchise_cut_paise = int(platform_commission_paise * revenue_share_percentage)
        platform_cut_paise = platform_commission_paise - franchise_cut_paise
        
        # 2. Allocate to Franchise Wallet (Simulated Immutable Ledger Entry)
        ledger_ref = self.db.collection('wallet_ledger').document()
        ledger_ref.set({
            'userId': franchise_uid,
            'type': 'CREDIT',
            'amountPaise': franchise_cut_paise,
            'referenceId': f"franchise_rev_job_{job_id}",
            'status': 'success',
            'createdAt': firestore.SERVER_TIMESTAMP,
            'description': f"Franchise commission for job {job_id}"
        })
        
        # Also increment franchise wallet available balance
        wallet_ref = self.db.collection('wallets').document(franchise_uid)
        wallet_ref.set({
            'availableBalancePaise': firestore.Increment(franchise_cut_paise)
        }, merge=True)
        
        return {
            "platformCut": platform_cut_paise,
            "franchiseCut": franchise_cut_paise,
            "franchiseUid": franchise_uid
        }
