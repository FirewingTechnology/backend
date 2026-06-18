import re
from google.cloud import firestore

PAN_REGEX = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$')

class KycRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def submit_kyc_tx(self, tx: firestore.Transaction, uid: str, pan_number: str,
                      selfie_url: str, bank_proof_url: str, address_proof_url: str) -> str:
        if not PAN_REGEX.match(pan_number):
            raise ValueError("Invalid PAN format.")

        kyc_ref = self.db.collection('kyc_submissions').document(uid)
        doc = kyc_ref.get(transaction=tx)

        # Prevent resubmission if already approved
        if doc.exists and doc.get('status') == 'approved':
            return "ALREADY_APPROVED"

        tx.set(kyc_ref, {
            'uid': uid,
            'panNumber': pan_number,
            'selfieUrl': selfie_url,
            'bankProofUrl': bank_proof_url,
            'addressProofUrl': address_proof_url,
            'status': 'pending',
            'reviewerId': None,
            'rejectionReason': None,
            'submittedAt': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        return "SUBMITTED"

    def update_kyc_status_tx(self, tx: firestore.Transaction, uid: str, status: str,
                              reviewer_id: str, rejection_reason: str = None) -> str:
        allowed_statuses = ['approved', 'rejected', 'action_required']
        if status not in allowed_statuses:
            raise ValueError(f"Invalid KYC status: {status}")

        kyc_ref = self.db.collection('kyc_submissions').document(uid)
        user_ref = self.db.collection('users').document(uid)

        doc = kyc_ref.get(transaction=tx)
        if not doc.exists:
            return "KYC_NOT_FOUND"

        current_status = doc.get('status')
        if current_status == 'approved':
            return "ALREADY_APPROVED"

        update_data = {
            'status': status,
            'reviewerId': reviewer_id,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        }
        if rejection_reason:
            update_data['rejectionReason'] = rejection_reason

        tx.update(kyc_ref, update_data)

        # Sync isKycVerified flag on user profile atomically
        tx.update(user_ref, {
            'isKycVerified': (status == 'approved'),
            'kycStatus': status,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        return "UPDATED"
