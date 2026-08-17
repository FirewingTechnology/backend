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
        Returns the PLAIN OTP to be shown to the user via private Firestore doc.
        """
        job_ref = self.db.collection('job_requests').document(job_id)
        doc = job_ref.get(transaction=tx)

        if not doc.exists:
            raise ValueError("Job not found")

        status = doc.get('status')
        if status not in ['in_progress', 'arrived', 'completion_requested', 'otp_pending']:
            raise ValueError(f"Invalid status for OTP generation: {status}")

        completion_status = doc.get('completionStatus')
        issued_at = doc.get('completionOtpIssuedAt')
        now = datetime.datetime.now(datetime.timezone.utc)

        if completion_status == 'otp_pending' and issued_at:
            if isinstance(issued_at, datetime.datetime):
                elapsed = (now - issued_at).total_seconds()
                if elapsed < 45:
                    raise ValueError(f"COOLDOWN_ACTIVE: Please wait {int(45 - elapsed)} seconds before requesting another OTP.")

        # Cryptographically secure 6-digit plain OTP
        plain_otp = str(secrets.randbelow(900000) + 100000)

        # bcrypt hash - store only hash, never plaintext on job request
        otp_hash = bcrypt.hashpw(plain_otp.encode(), bcrypt.gensalt()).decode()
        expires_at = now + datetime.timedelta(minutes=15)

        tx.update(job_ref, {
            'completionOtpHash': otp_hash,
            'completionOtpExpiresAt': expires_at,
            'completionOtpAttempts': 0,
            'completionOtpIssuedAt': firestore.SERVER_TIMESTAMP,
            'completionStatus': 'otp_pending',
            'status': 'completion_requested',
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })

        return plain_otp

    def verify_otp_tx(self, tx: firestore.Transaction, job_id: str, plain_otp: str) -> str:
        """
        Reads job atomically. Validates OTP. Returns status string.
        """
        job_ref = self.db.collection('job_requests').document(job_id)
        doc = job_ref.get(transaction=tx)

        if not doc.exists:
            return "JOB_NOT_FOUND"

        completion_status = doc.get('completionStatus')
        status = doc.get('status')
        if completion_status != 'otp_pending' and status != 'completion_requested':
            return f"INVALID_STATUS:{completion_status or status}"

        attempts = doc.get('completionOtpAttempts', doc.get('otpAttempts', 0))
        if attempts >= 5:
            return "LOCKED_OUT"

        # Check expiry
        expires_at = doc.get('completionOtpExpiresAt', doc.get('otpExpiresAt'))
        now = datetime.datetime.now(datetime.timezone.utc)
        if expires_at and now > expires_at:
            return "OTP_EXPIRED"

        otp_hash = doc.get('completionOtpHash', doc.get('otpHash', ''))
        if not otp_hash:
            return "NO_OTP_FOUND"

        is_valid = bcrypt.checkpw(plain_otp.encode(), otp_hash.encode())

        if not is_valid:
            # Increment attempts atomically
            tx.update(job_ref, {
                'completionOtpAttempts': firestore.Increment(1),
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })
            return f"INVALID_OTP:{attempts + 1}"

        # OTP valid → transition completionStatus to 'otp_verified' and clear hash
        tx.update(job_ref, {
            'completionStatus': 'otp_verified',
            'completionVerifiedAt': firestore.SERVER_TIMESTAMP,
            'completionOtpHash': firestore.DELETE_FIELD,
            'completionOtpExpiresAt': firestore.DELETE_FIELD,
            'completionOtpAttempts': firestore.DELETE_FIELD,
            'lastUpdated': firestore.SERVER_TIMESTAMP
        })
        return "VERIFIED"
