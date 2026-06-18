from google.cloud import firestore

class DisputeRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    # Valid state machine transitions (enforced server-side)
    VALID_TRANSITIONS = {
        'raised': ['under_review'],
        'under_review': ['evidence_requested', 'resolved_customer', 'resolved_pro', 'dismissed'],
        'evidence_requested': ['under_review', 'resolved_customer', 'resolved_pro', 'dismissed'],
    }

    def create_dispute_tx(self, tx: firestore.Transaction, job_id: str, user_uid: str, reason: str) -> str:
        dispute_ref = self.db.collection('disputes').document()
        escrow_ref = self.db.collection('escrow_holds').document(job_id)
        job_ref = self.db.collection('job_requests').document(job_id)

        # Verify escrow exists and is held (not yet released)
        escrow_doc = escrow_ref.get(transaction=tx)
        if not escrow_doc.exists:
            return "ESCROW_NOT_FOUND"
        if escrow_doc.get('status') != 'held':
            return f"ESCROW_NOT_HELD:{escrow_doc.get('status')}"

        pro_uid = escrow_doc.get('proUid')

        # Freeze escrow — sets status to 'disputed' so cron worker skips it
        tx.update(escrow_ref, {
            'status': 'disputed',
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        # Create dispute document
        tx.set(dispute_ref, {
            'id': dispute_ref.id,
            'jobId': job_id,
            'proUid': pro_uid,
            'userUid': user_uid,
            'status': 'raised',
            'reason': reason,
            'createdAt': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        # Update job status
        tx.update(job_ref, {
            'status': 'disputed',
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        return f"CREATED:{dispute_ref.id}"

    def resolve_dispute_tx(self, tx: firestore.Transaction, dispute_id: str,
                           resolution: str, admin_uid: str) -> str:
        """
        resolution must be one of: 'resolved_customer', 'resolved_pro', 'dismissed'
        """
        allowed_resolutions = ['resolved_customer', 'resolved_pro', 'dismissed']
        if resolution not in allowed_resolutions:
            raise ValueError(f"Invalid resolution status: {resolution}")

        dispute_ref = self.db.collection('disputes').document(dispute_id)
        doc = dispute_ref.get(transaction=tx)

        if not doc.exists:
            return "DISPUTE_NOT_FOUND"

        current_status = doc.get('status')
        valid_next = self.VALID_TRANSITIONS.get(current_status, [])
        if resolution not in valid_next:
            return f"INVALID_TRANSITION:{current_status}→{resolution}"

        job_id = doc.get('jobId')
        pro_uid = doc.get('proUid')
        user_uid = doc.get('userUid')
        escrow_ref = self.db.collection('escrow_holds').document(job_id)
        escrow_doc = escrow_ref.get(transaction=tx)
        amount = escrow_doc.get('amountPaise', 0)

        if resolution == 'resolved_customer':
            # Refund user: remove escrow, credit user wallet
            user_wallet_ref = self.db.collection('wallets').document(user_uid)
            tx.update(user_wallet_ref, {
                'availableBalance': firestore.Increment(amount),
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })
            # Deduct from pro wallet
            pro_wallet_ref = self.db.collection('wallets').document(pro_uid)
            tx.update(pro_wallet_ref, {
                'escrowBalance': firestore.Increment(-amount),
                'ledgerBalance': firestore.Increment(-amount),
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })

        elif resolution == 'resolved_pro':
            # Release escrow to pro
            pro_wallet_ref = self.db.collection('wallets').document(pro_uid)
            tx.update(pro_wallet_ref, {
                'escrowBalance': firestore.Increment(-amount),
                'availableBalance': firestore.Increment(amount),
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })

        # Update escrow status
        tx.update(escrow_ref, {
            'status': 'resolved',
            'resolvedAt': firestore.SERVER_TIMESTAMP
        })

        # Update dispute document
        tx.update(dispute_ref, {
            'status': resolution,
            'resolvedBy': admin_uid,
            'resolvedAt': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        return "RESOLVED"
