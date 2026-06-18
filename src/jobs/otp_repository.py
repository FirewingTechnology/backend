import secrets
import datetime
import bcrypt
from google.cloud import firestore

class OtpRepository:
    def __init__(self, db: firestore.Client):
        self.db = db

    def generate_and_store_otp_tx(self, tx: firestore.Transaction, job_id: str) -> str:
        """
        Generates a 6-digit OTP, bcrypt hashes it, stores it on the job doc.
        Returns the PLAIN OTP to be shown to the user. Never stores plain OTP.
        """
        job_ref = self.db.collection('job_requests').document(job_id)
        doc = job_ref.get(transaction=tx)

        if not doc.exists:
            raise ValueError("Job not found")

        if doc.get('status') != 'in_progress':
            raise ValueError(f"Invalid status for OTP generation: {doc.get('status')}")

        # Generate 6-digit plain OTP (zero-padded)
        plain_otp = str(secrets.randbelow(900000) + 100000)

        # bcrypt hash - store only hash, never plaintext
        otp_hash = bcrypt.hashpw(plain_otp.encode(), bcrypt.gensalt()).decode()
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)

        tx.update(job_ref, {
            'otpHash': otp_hash,
            'otpExpiresAt': expires_at,
            'otpAttempts': 0,
            'status': 'otp_pending',
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        # Return plain OTP only — it travels only to the requesting user app response
        return plain_otp

    def verify_otp_tx(self, tx: firestore.Transaction, job_id: str, plain_otp: str) -> str:
        """
        Reads job atomically. Validates OTP. Returns status string.
        """
        job_ref = self.db.collection('job_requests').document(job_id)
        doc = job_ref.get(transaction=tx)

        if not doc.exists:
            return "JOB_NOT_FOUND"

        status = doc.get('status')
        if status != 'otp_pending':
            return f"INVALID_STATUS:{status}"

        attempts = doc.get('otpAttempts', 0)
        if attempts >= 5:
            return "LOCKED_OUT"

        # Check expiry
        expires_at = doc.get('otpExpiresAt')
        now = datetime.datetime.now(datetime.timezone.utc)
        if expires_at and now > expires_at:
            return "OTP_EXPIRED"

        otp_hash = doc.get('otpHash', '')
        is_valid = bcrypt.checkpw(plain_otp.encode(), otp_hash.encode())

        if not is_valid:
            # Increment attempts atomically
            tx.update(job_ref, {
                'otpAttempts': firestore.Increment(1),
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })
            return f"INVALID_OTP:{attempts + 1}"

        # OTP valid → transition status to 'escrow' and clear OTP fields
        tx.update(job_ref, {
            'status': 'escrow',
            'otpHash': firestore.DELETE_FIELD,  # Purge hash after use
            'otpExpiresAt': firestore.DELETE_FIELD,
            'otpAttempts': firestore.DELETE_FIELD,
            'otpVerifiedAt': firestore.SERVER_TIMESTAMP,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        return "VERIFIED"
