import logging
import secrets
import datetime
import bcrypt
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

    def request_completion(self, job_id: str, pro_uid: str, ip_address: str) -> dict:
        """
        Called by Pro App when tapping 'Complete Work'.
        Flow depends strictly on paymentMethod:
          - online: verifies server-side payment, settles Pro wallet, marks job completed. NO OTP.
          - wallet: marks status=completion_requested, completionStatus=wallet_payment_pending. NO OTP.
          - cash / direct_upi: marks status=completion_requested, completionStatus=payment_pending. NO OTP yet.
        """
        print(f"[JOB_COMPLETION_STARTED] Job ID: {job_id}, Pro UID: {pro_uid}, IP: {ip_address}")
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                print(f"[JOB_COMPLETION_FAILED] Job not found: {job_id}")
                raise ValueError("JOB_NOT_FOUND: Job record not found.")

            job_data = job_doc.to_dict() or {}
            current_status = job_data.get('status')

            # Idempotency check: Already completed
            if current_status == 'completed':
                print(f"[JOB_COMPLETION_SUCCESS] Job {job_id} already completed.")
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentConfirmed": True,
                    "message": "This job has already been completed."
                }

            # Pro authorization check
            assigned_pro = job_data.get('electricianId') or job_data.get('proUid')
            if assigned_pro and assigned_pro != pro_uid:
                print(f"[JOB_COMPLETION_FAILED] Unauthorized Pro: {pro_uid} vs assigned {assigned_pro}")
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            user_id = job_data.get('userId') or job_data.get('userUid')
            if not user_id:
                print(f"[JOB_COMPLETION_FAILED] Customer record missing on job: {job_id}")
                raise ValueError("INVALID_JOB_STATE: Customer record missing on job.")

            payment_method = (job_data.get('paymentMethod') or job_data.get('paymentMode') or 'online').lower()
            if payment_method == 'upi':
                payment_method = 'direct_upi'

            raw_cost = float(job_data.get('estimatedCost') or job_data.get('fixedPrice') or job_data.get('finalAmount') or 0.0)
            total_amount = max(raw_cost, 0.0)

            print(f"[JOB_STATE_VALIDATED] Status: {current_status}, PaymentMethod: {payment_method}, Amount: {total_amount}")

            # ── 1. ONLINE RAZORPAY FLOW (NO OTP) ──────────────────────────
            if payment_method == 'online':
                payment_status = (job_data.get('paymentStatus') or 'pending').lower()
                is_paid = (payment_status in ['paid', 'confirmed', 'success']) or (job_data.get('paymentVerified') is True)
                if not is_paid:
                    print(f"[PAYMENT_STATUS_VALIDATED] Online payment NOT verified (status={payment_status})")
                    job_ref.update({
                        'status': 'completion_requested',
                        'completionStatus': 'payment_pending',
                        'lastUpdated': firestore.SERVER_TIMESTAMP
                    })
                    raise ValueError("PAYMENT_NOT_VERIFIED: Online payment has not been verified yet.")

                # Settle online job atomically
                print(f"[JOB_COMPLETION_TRANSACTION_STARTED] Settling online payment for {job_id}")
                self._settle_online_job(job_id, pro_uid, total_amount)
                self._notify_customer(user_id, job_id, "Job Completed", "Your service has been completed and payment settled successfully.")
                print(f"[JOB_COMPLETION_SUCCESS] Online Job {job_id} completed successfully.")
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentMethod": "online",
                    "paymentConfirmed": True,
                    "message": "Job completed and payment settled successfully."
                }

            # ── 2. USER WALLET FLOW (NO OTP) ──────────────────────────────
            elif payment_method == 'wallet':
                job_ref.update({
                    'status': 'completion_requested',
                    'completionStatus': 'wallet_payment_pending',
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                self._notify_customer(
                    user_id, job_id, "Wallet Payment Due",
                    f"Work is completed. Please confirm payment of ₹{total_amount:.0f} from your wallet."
                )
                print(f"[JOB_COMPLETION_REQUESTED] Wallet payment pending for job {job_id}")
                return {
                    "success": True,
                    "status": "completion_requested",
                    "completionStatus": "wallet_payment_pending",
                    "paymentMethod": "wallet",
                    "paymentConfirmed": False,
                    "message": "Completion requested. Waiting for customer wallet payment."
                }

            # ── 3. CASH / DIRECT UPI FLOW (NO OTP YET) ────────────────────
            else:
                job_ref.update({
                    'status': 'completion_requested',
                    'completionStatus': 'payment_pending',
                    'paymentReceiptConfirmed': False,
                    'paymentStatus': f"{payment_method}_pending",
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                label = "cash" if payment_method == 'cash' else "via UPI directly"
                self._notify_customer(
                    user_id, job_id, "Payment Due",
                    f"Work is completed. Please pay ₹{total_amount:.0f} {label} to your electrician."
                )
                print(f"[JOB_COMPLETION_REQUESTED] Direct payment {payment_method} pending for job {job_id}")
                return {
                    "success": True,
                    "status": "completion_requested",
                    "completionStatus": "payment_pending",
                    "paymentMethod": payment_method,
                    "paymentReceiptConfirmed": False,
                    "paymentConfirmed": False,
                    "message": "Payment pending. Please collect payment from customer."
                }
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def confirm_direct_payment(self, job_id: str, pro_uid: str, confirmed: bool, ip_address: str) -> dict:
        """
        Called by Pro App when confirming receipt of Cash / Direct UPI.
        ONLY AFTER PAYMENT RECEIPT CONFIRMATION: Generates secure completion OTP and delivers ONLY to customer.
        """
        print(f"[PAYMENT_CONFIRM_STARTED] job={job_id}, confirmed={confirmed}, pro={pro_uid}")
        lock_token = self.lock_service.acquire_lock(f"payment:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
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
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            user_id = job_data.get('userId') or job_data.get('userUid')
            payment_method = (job_data.get('paymentMethod') or job_data.get('paymentMode') or 'cash').lower()
            if payment_method == 'upi':
                payment_method = 'direct_upi'

            if not confirmed:
                job_ref.update({
                    'paymentReceiptConfirmed': False,
                    'paymentStatus': f"{payment_method}_unconfirmed",
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return {
                    "success": False,
                    "code": "PAYMENT_NOT_VERIFIED",
                    "message": "Payment unconfirmed by professional."
                }

            # ── PAYMENT RECEIPT CONFIRMED: GENERATE OTP FOR CUSTOMER ─────
            plain_otp = str(secrets.randbelow(900000) + 100000)
            otp_hash = bcrypt.hashpw(plain_otp.encode(), bcrypt.gensalt()).decode()
            now = datetime.datetime.now(datetime.timezone.utc)
            expires_at = now + datetime.timedelta(minutes=15)

            # Store only hash and status on job doc (never plain OTP)
            job_ref.update({
                'paymentReceiptConfirmed': True,
                'paymentStatus': f"{payment_method}_confirmed",
                'completionOtpHash': otp_hash,
                'completionOtpExpiresAt': expires_at,
                'completionOtpAttempts': 0,
                'completionOtpIssuedAt': firestore.SERVER_TIMESTAMP,
                'completionStatus': 'otp_pending',
                'status': 'completion_requested',
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })

            # Deliver plain OTP ONLY to customer private notification subcollection
            if user_id:
                try:
                    self.db.collection('users').document(user_id).collection('notifications').add({
                        'title': "Work Completion OTP",
                        'body': f"Share code {plain_otp} with your electrician only after you have confirmed your payment.",
                        'type': 'COMPLETION_OTP',
                        'jobId': job_id,
                        'otp': plain_otp,
                        'createdAt': firestore.SERVER_TIMESTAMP,
                        'read': False
                    })
                except Exception as notif_err:
                    print(f"Error saving customer OTP notification: {notif_err}")

                # Send push notification with OTP to customer
                try:
                    user_doc = self.db.collection('users').document(user_id).get()
                    if user_doc.exists:
                        u_data = user_doc.to_dict() or {}
                        fcm_token = u_data.get('fcmToken') or u_data.get('fcm_token') or u_data.get('token')
                        if fcm_token:
                            FCMService.send_to_token(
                                fcm_token,
                                {"type": "COMPLETION_OTP", "jobId": job_id, "otp": plain_otp},
                                title="Work Completion OTP",
                                body=f"Share code {plain_otp} with your electrician to verify completion.",
                                channel_id="powrsply_general_v1"
                            )
                except Exception as fcm_err:
                    print(f"FCM delivery error: {fcm_err}")

            print(f"[OTP_GENERATED_FOR_CUSTOMER] Job {job_id}, Direct payment {payment_method} confirmed.")
            # Note: Pro response does NOT contain plain_otp
            return {
                "success": True,
                "status": "completion_requested",
                "completionStatus": "otp_pending",
                "paymentReceiptConfirmed": True,
                "paymentMethod": payment_method,
                "message": "Payment receipt confirmed. Completion OTP sent to customer."
            }
        finally:
            self.lock_service.release_lock(f"payment:{job_id}", lock_token)

    def verify_otp(self, job_id: str, plain_otp: str, pro_uid: str, ip_address: str) -> dict:
        """
        Called by Pro App when electrician enters the 6-digit OTP provided by customer.
        Only valid for Cash / Direct UPI after payment receipt is confirmed.
        """
        print(f"[OTP_VALIDATION_STARTED] Job ID: {job_id}, Pro UID: {pro_uid}")
        lock_token = self.lock_service.acquire_lock(f"otp:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
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
                raise ValueError("UNAUTHORIZED_PRO: You are not assigned to this job.")

            payment_method = (job_data.get('paymentMethod') or job_data.get('paymentMode') or 'cash').lower()
            if payment_method == 'upi':
                payment_method = 'direct_upi'

            if payment_method in ['online', 'wallet']:
                raise ValueError(f"INVALID_FLOW: OTP verification is not used for {payment_method} payments.")

            # Payment receipt must have been confirmed first
            if not job_data.get('paymentReceiptConfirmed'):
                raise ValueError("PAYMENT_NOT_CONFIRMED: You must confirm payment receipt before verifying completion OTP.")

            tx = self.db.transaction()
            result = self._run_verify_tx(tx, job_id, plain_otp)

            if result == "LOCKED_OUT":
                self.security_service.log(
                    SecurityEventType.OTP_BRUTE_FORCE,
                    SecurityEventSeverity.HIGH,
                    pro_uid, ip_address,
                    metadata={"jobId": job_id}
                )
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
                raise ValueError(f"INVALID_OTP: Invalid OTP entered (attempt {attempts}). Please check with customer.")

            if result == "OTP_EXPIRED":
                raise ValueError("OTP_EXPIRED: The completion OTP has expired. Please re-confirm payment receipt.")

            if result.startswith("INVALID_STATUS") or result == "JOB_NOT_FOUND" or result == "NO_OTP_FOUND":
                raise ValueError(f"INVALID_JOB_STATE: Cannot verify OTP in current state ({result}).")

            # ── OTP IS VALID: FINALIZE JOB & RECORD COMMISSION DUE ─────
            raw_cost = float(job_data.get('estimatedCost') or job_data.get('fixedPrice') or job_data.get('finalAmount') or 0.0)
            total_amount = max(raw_cost, 0.0)
            commission_amount = round(total_amount * 0.10, 2)
            pro_earning = round(total_amount - commission_amount, 2)

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
                'otpVerified': True,
                'commission': commission_amount,
                'adminCommission': commission_amount,
                'proEarning': pro_earning,
                'settlementStatus': 'commission_due',
                'completedAt': firestore.SERVER_TIMESTAMP,
                'lastUpdated': firestore.SERVER_TIMESTAMP
            })

            user_id = job_data.get('userId') or job_data.get('userUid')
            if user_id:
                self._notify_customer(user_id, job_id, "Service Completed", "Your electrician has verified completion with your OTP. Thank you for choosing PowrSply!")

            print(f"[JOB_COMPLETION_SUCCESS] Direct payment {payment_method} OTP verified for job {job_id}")
            return {
                "success": True,
                "status": "completed",
                "completionStatus": "completed",
                "paymentMethod": payment_method,
                "paymentConfirmed": True,
                "message": "Job completed successfully."
            }
        finally:
            self.lock_service.release_lock(f"otp:{job_id}", lock_token)

    def pay_wallet(self, job_id: str, user_uid: str, ip_address: str) -> dict:
        """
        Called when customer confirms payment from wallet.
        Performs atomic transfer: Customer Wallet -> Platform Commission -> Pro Wallet.
        """
        print(f"[WALLET_PAYMENT_STARTED] Job ID: {job_id}, User UID: {user_uid}")
        lock_token = self.lock_service.acquire_lock(f"wallet:pay:{job_id}", ttl_seconds=15)
        try:
            job_ref = self.db.collection('job_requests').document(job_id)
            job_doc = job_ref.get()
            if not job_doc.exists:
                raise ValueError("JOB_NOT_FOUND: Job record not found.")

            job_data = job_doc.to_dict() or {}
            if job_data.get('status') == 'completed':
                return {
                    "success": True,
                    "status": "completed",
                    "completionStatus": "completed",
                    "paymentStatus": "paid",
                    "paymentConfirmed": True,
                    "message": "This job has already been completed and paid."
                }

            # Verify customer ownership
            owner_uid = job_data.get('userId') or job_data.get('userUid')
            if owner_uid and owner_uid != user_uid:
                raise ValueError("UNAUTHORIZED_USER: You do not own this job request.")

            payment_method = (job_data.get('paymentMethod') or job_data.get('paymentMode') or 'wallet').lower()
            if payment_method != 'wallet':
                raise ValueError(f"INVALID_PAYMENT_METHOD: Job is set to '{payment_method}', not 'wallet'.")

            pro_uid = job_data.get('electricianId') or job_data.get('proUid')
            if not pro_uid:
                raise ValueError("INVALID_JOB_STATE: No electrician assigned to this job.")

            raw_cost = float(job_data.get('estimatedCost') or job_data.get('fixedPrice') or job_data.get('finalAmount') or 0.0)
            total_amount = max(raw_cost, 0.0)
            commission_amount = round(total_amount * 0.10, 2)
            pro_earning = round(total_amount - commission_amount, 2)

            customer_wallet_ref = self.db.collection('wallets').document(user_uid)
            pro_wallet_ref = self.db.collection('wallets').document(pro_uid)
            customer_ledger_ref = self.db.collection('wallet_ledger').document(f"WALLET_DEBIT_{job_id}")
            pro_ledger_ref = self.db.collection('wallet_ledger').document(f"WALLET_CREDIT_{job_id}")
            idempotency_ref = self.db.collection('wallet_ledger').document(f"WALLET_PAY_{job_id}")

            @firestore.transactional
            def _wallet_transfer_tx(transaction):
                # Idempotency check
                idemp_snap = idempotency_ref.get(transaction=transaction)
                if idemp_snap.exists:
                    return True

                # Check customer wallet balance
                cust_snap = customer_wallet_ref.get(transaction=transaction)
                cust_data = cust_snap.to_dict() if cust_snap.exists else {}
                cust_balance = float(cust_data.get('balance', 0.0))

                if cust_balance < total_amount:
                    raise ValueError(f"INSUFFICIENT_WALLET_BALANCE: Current balance ₹{cust_balance:.2f} is insufficient for ₹{total_amount:.2f}.")

                # Pro wallet
                pro_snap = pro_wallet_ref.get(transaction=transaction)
                pro_data = pro_snap.to_dict() if pro_snap.exists else {}
                pro_balance = float(pro_data.get('balance', 0.0))

                new_cust_balance = round(cust_balance - total_amount, 2)
                new_pro_balance = round(pro_balance + pro_earning, 2)

                # 1. Debit customer wallet
                transaction.set(customer_wallet_ref, {
                    'balance': new_cust_balance,
                    'updatedAt': firestore.SERVER_TIMESTAMP
                }, merge=True)

                # 2. Credit Pro wallet
                transaction.set(pro_wallet_ref, {
                    'balance': new_pro_balance,
                    'updatedAt': firestore.SERVER_TIMESTAMP
                }, merge=True)

                # 3. Create customer debit ledger entry
                transaction.set(customer_ledger_ref, {
                    'userId': user_uid,
                    'jobId': job_id,
                    'type': 'debit',
                    'amount': total_amount,
                    'previousBalance': cust_balance,
                    'newBalance': new_cust_balance,
                    'reason': f"Payment for Job #{job_id}",
                    'referenceId': f"WALLET_DEBIT_{job_id}",
                    'timestamp': firestore.SERVER_TIMESTAMP
                })

                # 4. Create pro credit ledger entry
                transaction.set(pro_ledger_ref, {
                    'userId': pro_uid,
                    'jobId': job_id,
                    'type': 'credit',
                    'amount': pro_earning,
                    'totalJobAmount': total_amount,
                    'commissionDeducted': commission_amount,
                    'previousBalance': pro_balance,
                    'newBalance': new_pro_balance,
                    'reason': f"Job Earning (Job #{job_id})",
                    'referenceId': f"WALLET_CREDIT_{job_id}",
                    'timestamp': firestore.SERVER_TIMESTAMP
                })

                # 5. Create master idempotency record
                transaction.set(idempotency_ref, {
                    'jobId': job_id,
                    'customerUid': user_uid,
                    'proUid': pro_uid,
                    'amount': total_amount,
                    'commission': commission_amount,
                    'proEarning': pro_earning,
                    'status': 'settled',
                    'timestamp': firestore.SERVER_TIMESTAMP
                })

                # 6. Update job document to completed
                transaction.update(job_ref, {
                    'status': 'completed',
                    'completionStatus': 'completed',
                    'paymentStatus': 'paid',
                    'paymentVerified': True,
                    'settlementStatus': 'settled',
                    'commission': commission_amount,
                    'adminCommission': commission_amount,
                    'proEarning': pro_earning,
                    'completedAt': firestore.SERVER_TIMESTAMP,
                    'lastUpdated': firestore.SERVER_TIMESTAMP
                })
                return True

            tx = self.db.transaction()
            _wallet_transfer_tx(tx)

            self._notify_customer(user_uid, job_id, "Payment Successful", f"₹{total_amount:.0f} was successfully paid from your wallet. Service completed!")
            print(f"[WALLET_PAYMENT_SUCCESS] Job {job_id} wallet payment completed.")
            return {
                "success": True,
                "status": "completed",
                "completionStatus": "completed",
                "paymentStatus": "paid",
                "paymentVerified": True,
                "message": "Wallet payment processed and job completed successfully."
            }
        finally:
            self.lock_service.release_lock(f"wallet:pay:{job_id}", lock_token)

    def _settle_online_job(self, job_id: str, pro_uid: str, total_amount: float):
        """Authoritatively calculates platform commission and credits Pro wallet atomically."""
        commission_rate = 0.10  # 10% Platform commission
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
            new_balance = round(current_balance + pro_earning, 2)

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

    def _notify_customer(self, user_id: str, job_id: str, title: str, body: str):
        if not user_id:
            return
        try:
            self.db.collection('users').document(user_id).collection('notifications').add({
                'title': title,
                'body': body,
                'type': 'JOB_UPDATE',
                'jobId': job_id,
                'createdAt': firestore.SERVER_TIMESTAMP,
                'read': False
            })
        except Exception as e:
            print(f"Notification subcollection error: {e}")

        try:
            user_doc = self.db.collection('users').document(user_id).get()
            if user_doc.exists:
                u_data = user_doc.to_dict() or {}
                fcm_token = u_data.get('fcmToken') or u_data.get('fcm_token') or u_data.get('token')
                if fcm_token:
                    FCMService.send_to_token(
                        fcm_token,
                        {"type": "JOB_UPDATE", "jobId": job_id},
                        title=title,
                        body=body,
                        channel_id="powrsply_general_v1"
                    )
        except Exception as e:
            print(f"FCM delivery error: {e}")

    def _run_verify_tx(self, tx, job_id, plain_otp):
        return self.otp_repo.verify_otp_tx(tx, job_id, plain_otp)


