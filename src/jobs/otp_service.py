import logging
from google.cloud import firestore
from src.jobs.otp_repository import OtpRepository
from src.finance.repository.escrow_repository import EscrowRepository
from src.infrastructure.redis.lock_service import RedisLockService
from src.core.security.event_service import SecurityEventService
from src.core.security.event_models import SecurityEventType, SecurityEventSeverity
from src.infrastructure.firebase.fcm_service import FCMService

logger = logging.getLogger("POWRSPly.OtpService")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

class OtpService:
    def __init__(self, db: firestore.Client, lock_service: RedisLockService,
                 otp_repo: OtpRepository, escrow_repo: EscrowRepository,
                 security_service: SecurityEventService):
        self.db = db
        self.lock_service = lock_service
        self.otp_repo = otp_repo
        self.escrow_repo = escrow_repo
        self.security_service = security_service

    def request_completion(self, job_id: str, pro_uid: str, ip_address: str) -> str:
        """Called by Pro App when tapping Work Completed. Generates OTP, stores hashed OTP on job, stores plain OTP in user private doc & sends FCM."""
        print(f"[JOB_COMPLETION_STARTED] Job ID: {job_id}, Pro UID: {pro_uid}, IP: {ip_address}")
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=10)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                print(f"[JOB_COMPLETION_FAILED] Job not found: {job_id}")
                raise ValueError("JOB_NOT_FOUND: Job record not found.")

            job_data = job_doc.to_dict() or {}
            print(f"[JOB_STATE_VALIDATED] Status: {job_data.get('status')}, PaymentMethod: {job_data.get('paymentMethod')}")

            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                print(f"[JOB_COMPLETION_FAILED] Unauthorized Pro: {pro_uid} vs assigned {assigned_pro}")
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            user_id = job_data.get('userId') or job_data.get('userUid')
            if not user_id:
                print(f"[JOB_COMPLETION_FAILED] Customer record missing on job: {job_id}")
                raise ValueError("INVALID_JOB_STATE: Customer record missing on job.")

            tx = self.db.transaction()
            plain_otp = self._run_generate_tx(tx, job_id)

            # Resolve customer FCM token safely
            fcm_token = None
            try:
                user_doc = self.db.collection('users').document(user_id).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict() or {}
                    fcm_token = user_data.get('fcmToken') or user_data.get('fcm_token') or user_data.get('token')
            except Exception as e:
                print(f"Error fetching customer FCM token: {e}")

            # Send FCM notification to user with OTP in data payload
            fcm_data = {
                "type": "COMPLETION_OTP",
                "jobId": job_id,
                "otp": plain_otp,
                "requiresAction": "true"
            }
            title = "Service Completion Code"
            body = f"Share code {plain_otp} with your electrician to confirm completion."

            if fcm_token:
                try:
                    FCMService.send_to_token(fcm_token, fcm_data, title=title, body=body, channel_id="powrsply_general_v1")
                except Exception as e:
                    print(f"FCM token delivery error: {e}")
            
            # Broadcast to user topic as fallback
            try:
                FCMService.send_to_topic(f"user_{user_id}", fcm_data, title=title, body=body, channel_id="powrsply_general_v1")
            except Exception as e:
                print(f"FCM topic fallback error: {e}")

            # Store OTP in user's private notification subcollection so User App displays it in real-time
            try:
                self.db.collection('users').document(user_id).collection('notifications').add({
                    'title': title,
                    'body': body,
                    'type': 'COMPLETION_OTP',
                    'jobId': job_id,
                    'otp': plain_otp,
                    'createdAt': firestore.SERVER_TIMESTAMP,
                    'read': False
                })
            except Exception as notif_err:
                print(f"User OTP notification subcollection write error: {notif_err}")

            return plain_otp
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def generate_otp(self, job_id: str, uid: str, ip_address: str) -> str:
        """Legacy helper endpoint."""
        return self.request_completion(job_id, uid, ip_address)

    def _run_generate_tx(self, tx, job_id):
        return self.otp_repo.generate_and_store_otp_tx(tx, job_id)

    def verify_otp(self, job_id: str, plain_otp: str, pro_uid: str,
                   ip_address: str, amount_paise: int = 0, commission_paise: int = 0):
        """Called by Pro App. Validates OTP, checks payment state, and atomically executes settlement."""
        print(f"[OTP_VALIDATION_STARTED] Job ID: {job_id}, Pro UID: {pro_uid}")
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                print(f"[JOB_COMPLETION_FAILED] Job not found: {job_id}")
                raise ValueError("JOB_NOT_FOUND: Job record not found.")

            job_data = job_doc.to_dict() or {}
            current_status = job_data.get('status')
            if current_status == 'completed':
                print(f"[JOB_COMPLETION_FAILED] Duplicate completion for {job_id}")
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentConfirmed": True,
                    "message": "This job has already been completed."
                }

            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                print(f"[JOB_COMPLETION_FAILED] Unauthorized Pro {pro_uid} for job {job_id}")
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            tx = self.db.transaction()
            result = self._run_verify_tx(tx, job_id, plain_otp)

            if result == "LOCKED_OUT":
                self.security_service.log(
                    SecurityEventType.OTP_BRUTE_FORCE,
                    SecurityEventSeverity.HIGH,
                    pro_uid, ip_address,
                    metadata={"jobId": job_id}
                )
                print(f"[JOB_COMPLETION_FAILED] Pro locked out due to OTP attempts: {pro_uid}")
                raise PermissionError("OTP_LOCKED: Account temporarily locked due to too many failed OTP attempts.")

            if result.startswith("INVALID_OTP"):
                attempts = int(result.split(":")[1]) if ":" in result else 1
                if attempts >= 3:
                    self.security_service.log(
                        SecurityEventType.OTP_BRUTE_FORCE,
                        SecurityEventSeverity.WARNING,
                        pro_uid, ip_address,
                        metadata={"jobId": job_id, "attempt": attempts}
                    )
                print(f"[JOB_COMPLETION_FAILED] Invalid OTP (attempt {attempts})")
                raise ValueError("INVALID_OTP: Invalid OTP entered. Please check with customer.")

            if result == "OTP_EXPIRED":
                print(f"[JOB_COMPLETION_FAILED] OTP Expired for {job_id}")
                raise ValueError("OTP_EXPIRED: The completion OTP has expired. Please request a new OTP.")

            if result.startswith("INVALID_STATUS") or result == "JOB_NOT_FOUND":
                print(f"[JOB_COMPLETION_FAILED] Cannot verify OTP due to status: {result}")
                raise ValueError(f"INVALID_JOB_STATE: Cannot complete job in current state ({result}).")

            print(f"[OTP_VALIDATED] OTP verified successfully for job {job_id}")

            # Inspect payment method and status
            updated_doc = job_ref.get()
            data = updated_doc.to_dict() or {}
            payment_method = (data.get('paymentMethod') or data.get('paymentMode') or 'online').lower()
            payment_status = (data.get('paymentStatus') or 'pending').lower()
            raw_cost = float(data.get('estimatedCost') or data.get('fixedPrice') or data.get('finalAmount') or 0.0)
            total_amount = max(raw_cost, 0.0)

            print(f"[PAYMENT_METHOD_VALIDATED] Method: {payment_method}, Status: {payment_status}, Amount: {total_amount}")

            # ── ONLINE PAYMENT SETTLEMENT FLOW ──────────────────────────
            if payment_method == 'online':
                is_paid = (payment_status in ['paid', 'confirmed', 'success']) or (data.get('paymentVerified') is True)
                if not is_paid:
                    print(f"[PAYMENT_STATUS_VALIDATED] Online payment NOT paid yet (status={payment_status})")
                    # Move completionStatus to otp_verified, but hold job in payment_pending
                    job_ref.update({
                        'completionStatus': 'otp_verified',
                        'lastUpdated': firestore.SERVER_TIMESTAMP
                    })
                    return {
                        "success": False,
                        "code": "PAYMENT_NOT_VERIFIED",
                        "status": "payment_pending",
                        "completionStatus": "otp_verified",
                        "paymentMethod": "online",
                        "paymentConfirmed": False,
                        "message": "Online payment has not been verified yet."
                    }

                # Online is paid -> Perform atomic completion transaction
                print(f"[JOB_COMPLETION_TRANSACTION_STARTED] Settling online payment for {job_id}")
                self._settle_online_job(job_id, pro_uid, total_amount)
                print(f"[JOB_COMPLETION_SUCCESS] Online Job {job_id} completed successfully")
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentMethod": "online",
                    "paymentConfirmed": True,
                    "message": "Job completed and payment settled successfully."
                }

            # ── CASH / DIRECT UPI FLOW ─────────────────────────────────
            else:
                norm_status = f"{payment_method}_pending" if not payment_status.endswith('_pending') else payment_status
                job_ref.update({
                    'completionStatus': 'payment_pending',
                    'paymentStatus': norm_status,
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                print(f"[PAYMENT_STATUS_VALIDATED] {payment_method} awaiting pro confirmation (job {job_id})")
                return {
                    "success": True,
                    "status": "payment_pending",
                    "completionStatus": "payment_pending",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": False,
                    "message": "OTP verified. Please confirm direct payment collection from customer."
                }
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def _settle_online_job(self, job_id: str, pro_uid: str, total_amount: float):
        """Authoritatively calculates platform commission and credits Pro wallet atomically."""
        commission_rate = 0.10 # 10% Platform commission
        commission_amount = round(total_amount * commission_rate, 2)
        pro_earning = round(total_amount - commission_amount, 2)

        job_ref = self.db.collection('job_requests').document(job_id)
        pro_wallet_ref = self.db.collection('wallets').document(pro_uid)
        pro_user_ref = self.db.collection('users').document(pro_uid)
        ledger_ref = self.db.collection('wallet_ledger').document(f"SETTLE_{job_id}")
        txn_ref = self.db.collection('transactions').document()

        @firestore.transactional
        def _settle_tx(transaction):
            # Idempotency check: Don't settle if ledger entry already exists
            ledger_snap = ledger_ref.get(transaction=transaction)
            if ledger_snap.exists:
                return True

            wallet_snap = pro_wallet_ref.get(transaction=transaction)
            wallet_data = wallet_snap.to_dict() if wallet_snap.exists else {}
            current_balance = float(wallet_data.get('balance', 0.0))
            new_balance = current_balance + pro_earning

            # 1. Update Pro Wallet
            transaction.set(pro_wallet_ref, {
                'balance': new_balance,
                'updatedAt': firestore.SERVER_TIMESTAMP
            }, merge=True)

            # 2. Update embedded wallet on user doc if exists
            user_snap = pro_user_ref.get(transaction=transaction)
            if user_snap.exists:
                user_data = user_snap.to_dict() or {}
                cur_wallet = user_data.get('wallet', {})
                cur_wallet['balance'] = new_balance
                transaction.update(pro_user_ref, {'wallet': cur_wallet})

            # 3. Create wallet_ledger record
            transaction.set(ledger_ref, {
                'userId': pro_uid,
                'jobId': job_id,
                'type': 'credit',
                'amount': pro_earning,
                'totalJobAmount': total_amount,
                'commissionDeducted': commission_amount,
                'reason': f"Job Earning (Job #{job_id})",
                'referenceId': f"SETTLE_{job_id}",
                'previousBalance': current_balance,
                'newBalance': new_balance,
                'timestamp': firestore.SERVER_TIMESTAMP
            })

            # 4. Create public transaction record for Pro
            transaction.set(txn_ref, {
                'userId': pro_uid,
                'jobId': job_id,
                'type': 'credit',
                'title': f"Job Earning (Job #{job_id})",
                'amount': pro_earning,
                'referenceId': f"SETTLE_{job_id}",
                'createdAt': firestore.SERVER_TIMESTAMP
            })

            # 5. Mark job completed authoritatively
            transaction.update(job_ref, {
                'status': 'completed',
                'completionStatus': 'completed',
                'paymentStatus': 'paid',
                'paymentVerified': True,
                'commission': commission_amount,
                'adminCommission': commission_amount,
                'proEarning': pro_earning,
                'settlementStatus': 'settled',
                'completedAt': firestore.SERVER_TIMESTAMP,
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })
            return True

        tx = self.db.transaction()
        _settle_tx(tx)

    def confirm_direct_payment(self, job_id: str, pro_uid: str, confirmed: bool, ip_address: str):
        """Called by Pro App when confirming receipt of Cash / Direct UPI."""
        print(f"[JOB_COMPLETION_TRANSACTION_STARTED] Direct payment confirm: job={job_id}, confirmed={confirmed}, pro={pro_uid}")
        lock_token = self.lock_service.acquire_lock(f"payment:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                print(f"[JOB_COMPLETION_FAILED] Job not found: {job_id}")
                raise ValueError("JOB_NOT_FOUND: Job record not found.")

            job_data = job_doc.to_dict() or {}
            if job_data.get('status') == 'completed':
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentConfirmed": True,
                    "message": "This job has already been completed."
                }

            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                print(f"[JOB_COMPLETION_FAILED] Unauthorized Pro {pro_uid} for direct payment on {job_id}")
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            completion_status = job_data.get('completionStatus')
            if completion_status not in ['otp_verified', 'payment_pending']:
                print(f"[JOB_COMPLETION_FAILED] OTP not verified prior to direct payment (status={completion_status})")
                raise ValueError("OTP_NOT_VERIFIED: Customer completion OTP must be verified before confirming payment.")

            payment_method = (job_data.get('paymentMethod') or job_data.get('paymentMode') or 'cash').lower()
            raw_cost = float(job_data.get('estimatedCost') or job_data.get('fixedPrice') or job_data.get('finalAmount') or 0.0)
            total_amount = max(raw_cost, 0.0)
            commission_amount = round(total_amount * 0.10, 2)
            pro_earning = round(total_amount - commission_amount, 2)

            if confirmed:
                ledger_ref = self.db.collection('wallet_ledger').document(f"COMM_DUE_{job_id}")
                ledger_snap = ledger_ref.get()
                if not ledger_snap.exists:
                    ledger_ref.set({
                        'userId': pro_uid,
                        'jobId': job_id,
                        'type': 'commission_due',
                        'amount': commission_amount,
                        'totalJobAmount': total_amount,
                        'reason': f"Platform Commission Due (Job #{job_id})",
                        'referenceId': f"COMM_DUE_{job_id}",
                        'timestamp': firestore.SERVER_TIMESTAMP
                    })

                job_ref.update({
                    'status': 'completed',
                    'completionStatus': 'completed',
                    'paymentStatus': f"{payment_method}_confirmed",
                    'paymentVerified': True,
                    'commission': commission_amount,
                    'adminCommission': commission_amount,
                    'proEarning': pro_earning,
                    'settlementStatus': 'commission_due',
                    'paymentConfirmedAt': firestore.SERVER_TIMESTAMP,
                    'paymentConfirmedBy': pro_uid,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                print(f"[JOB_COMPLETION_SUCCESS] Direct payment {payment_method} confirmed for job {job_id}")
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": True,
                    "message": "Direct payment confirmed. Job completed successfully."
                }
            else:
                job_ref.update({
                    'completionStatus': 'payment_pending',
                    'paymentStatus': f"{payment_method}_unconfirmed",
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                print(f"[PAYMENT_STATUS_VALIDATED] Direct payment unconfirmed for job {job_id}")
                return {
                    "success": False,
                    "code": "PAYMENT_NOT_VERIFIED",
                    "status": "payment_pending",
                    "completionStatus": "payment_pending",
                    "paymentMethod": payment_method,
                    "paymentConfirmed": False,
                    "message": "Payment unconfirmed by professional."
                }
        finally:
            self.lock_service.release_lock(f"payment:{job_id}", lock_token)

    def _run_verify_tx(self, tx, job_id, plain_otp):
        return self.otp_repo.verify_otp_tx(tx, job_id, plain_otp)

