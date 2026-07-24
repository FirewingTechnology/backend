import os
import json
from dotenv import load_dotenv

load_dotenv()

import razorpay
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

app = Flask(__name__)
CORS(app)

# Initialize Razorpay Client
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_live_SmSxyLmsbg4zDj')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'lF2p21YCmDZHsk0XR5Fp5Czu')

client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Initialize Firebase
firebase_creds_env = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
if firebase_creds_env:
    cred_dict = json.loads(firebase_creds_env)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

import redis
from src.infrastructure.redis.lock_service import RedisLockService
from src.finance.repository.wallet_repository import WalletRepository
from src.finance.service.payment_service import PaymentService
from src.finance.api.payment_controller import payment_api

# Sprint 2 Imports
from src.finance.repository.withdrawal_repository import WithdrawalRepository
from src.finance.infrastructure.razorpayx.payout_client import PayoutClient
from src.finance.service.withdrawal_service import WithdrawalService
from src.finance.api.withdrawal_controller import withdrawal_api
from src.finance.api.payout_webhook_controller import payout_webhook_api

# Sprint 3 Imports
from src.core.logging.admin_logger import AdminLogger
from src.finance.repository.escrow_repository import EscrowRepository
from src.finance.service.escrow_service import EscrowService
from src.finance.service.reconciliation_service import ReconciliationService
from src.finance.api.cron_controller import cron_api

# Sprint 4 Imports
from src.core.security.event_repository import SecurityEventRepository
from src.core.security.event_service import SecurityEventService
from src.jobs.otp_repository import OtpRepository
from src.jobs.otp_service import OtpService
from src.jobs.otp_controller import otp_api
from src.jobs.job_controller import job_api
from src.disputes.dispute_repository import DisputeRepository
from src.disputes.dispute_service import DisputeService
from src.disputes.dispute_controller import dispute_api
from src.kyc.kyc_repository import KycRepository
from src.kyc.kyc_controller import kyc_api

# Sprint 5 Imports (Chat)
from src.chat.chat_service import ChatService
from src.chat.chat_controller import chat_api, init_chat_api

# Auth Imports
from src.auth.auth_controller import auth_api

# Voice Imports
from src.voice.voice_controller import voice_api

redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
try:
    redis_client = redis.from_url(redis_url)
    lock_service = RedisLockService(redis_client)

    # Sprint 1: Payment
    wallet_repo = WalletRepository(db)
    payment_service = PaymentService(db, lock_service, wallet_repo)

    # Sprint 2: Withdrawal
    withdrawal_repo = WithdrawalRepository(db)
    RAZORPAYX_ACCOUNT = os.environ.get('RAZORPAYX_ACCOUNT', '2323230005555555')
    payout_client = PayoutClient(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAYX_ACCOUNT)
    withdrawal_service = WithdrawalService(db, lock_service, withdrawal_repo, payout_client)

    # Sprint 3: Escrow, Reconciliation, Audit
    admin_logger = AdminLogger(db)
    escrow_repo = EscrowRepository(db)
    escrow_service = EscrowService(db, lock_service, escrow_repo)
    reconciliation_service = ReconciliationService(db)

    # Sprint 4: Security Events
    security_event_repo = SecurityEventRepository(db)
    security_service = SecurityEventService(security_event_repo)

    # Sprint 4: OTP Job Completion
    otp_repo = OtpRepository(db)
    otp_service = OtpService(db, lock_service, otp_repo, escrow_repo, security_service)

    # Sprint 4: Disputes
    dispute_repo = DisputeRepository(db)
    dispute_service = DisputeService(db, lock_service, dispute_repo, admin_logger)

    # Sprint 4: KYC
    kyc_repo = KycRepository(db)

    # Sprint 5: Chat
    chat_service = ChatService(db, redis_client)
    init_chat_api(chat_service)

    # Register all config
    app.config['FIRESTORE_DB'] = db
    app.config['LOCK_SERVICE'] = lock_service
    app.config['ADMIN_LOGGER'] = admin_logger
    app.config['PAYMENT_SERVICE'] = payment_service
    app.config['WITHDRAWAL_SERVICE'] = withdrawal_service
    app.config['ESCROW_SERVICE'] = escrow_service
    app.config['RECONCILIATION_SERVICE'] = reconciliation_service
    app.config['SECURITY_SERVICE'] = security_service
    app.config['OTP_SERVICE'] = otp_service
    app.config['DISPUTE_SERVICE'] = dispute_service
    app.config['KYC_REPO'] = kyc_repo
    app.config['REDIS_CLIENT'] = redis_client
    app.config['CHAT_SERVICE'] = chat_service

    # Register all blueprints
    app.register_blueprint(payment_api)
    app.register_blueprint(withdrawal_api)
    app.register_blueprint(payout_webhook_api)
    app.register_blueprint(cron_api)
    app.register_blueprint(otp_api)
    app.register_blueprint(job_api)
    app.register_blueprint(dispute_api)
    app.register_blueprint(kyc_api)
    app.register_blueprint(chat_api, url_prefix='/api/chat')
    app.register_blueprint(auth_api, url_prefix='/api/auth')
    app.register_blueprint(voice_api, url_prefix='/api')

except Exception as e:
    print(f"Failed to initialize V2 Finance modules: {e}")


def require_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "Unauthorized"}), 401
        token = auth_header.split('Bearer ')[1]
        try:
            decoded_token = firebase_auth.verify_id_token(token)
            request.user = decoded_token
        except Exception:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped

@app.route('/')
def home():
    return jsonify({"status": "running"}), 200

@app.route('/create-order', methods=['POST'])
@require_auth
def create_order():
    try:
        data = request.json or {}
        amount_paise = data.get('amountPaise') # amount in paise
        if amount_paise is None and data.get('amount') is not None:
            amount_paise = int(float(data.get('amount')) * 100)

        uid = request.user.get('uid')
        
        if not amount_paise:
            return jsonify({'error': 'Amount is required'}), 400

        # Razorpay expects amount in paise
        order_amount = int(amount_paise)
        
        # Create an Order via Razorpay API
        order_data = {
            'amount': order_amount,
            'currency': 'INR',
            'receipt': f'receipt_{uid}_{os.urandom(4).hex()}',
            'payment_capture': 1 # Auto capture payments
        }
        
        order = client.order.create(data=order_data)
        
        return jsonify(order), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/create-admin', methods=['POST'])
def create_admin():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'admin') # 'admin' or 'super_admin'
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        # Create user in Firebase Auth
        user_record = firebase_auth.create_user(
            email=email,
            password=password
        )

        # Save role in Firestore (Separate collections)
        collection_name = 'super_admins' if role == 'super_admin' else 'admins'
        db.collection(collection_name).document(user_record.uid).set({
            'email': email,
            'role': role,
            'createdAt': firestore.SERVER_TIMESTAMP
        })

        return jsonify({'status': 'success', 'uid': user_record.uid, 'message': f'Admin {email} created successfully!'}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/verify-signature', methods=['POST'])
@require_auth
def verify_signature():
    try:
        data = request.json or {}
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        app_type = data.get('app_type', 'user') # default to 'user'
        reason = data.get('reason', 'Payment via Razorpay')
        uid = request.user.get('uid')
        
        # The verify function will throw an error if the signature is invalid
        client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
        
        # --- Signature is VALID. We can safely update the database ---
        payment = client.payment.fetch(razorpay_payment_id)
        amount_paid_paise = int(payment.get("amount", 0))
        amount_paid_rupees = float(amount_paid_paise) / 100.0
        
        if uid and amount_paid_paise > 0:
            
            if app_type == 'user':
                wallet_ref = db.collection('wallets').document(uid)
                user_ref = db.collection('users').document(uid)
                ledger_ref = db.collection('wallet_ledger').document(razorpay_payment_id)
                tx_ref = db.collection('transactions').document()

                @firestore.transactional
                def update_user_wallet(transaction):
                    # Check Idempotency (prevent duplicate credit if already processed)
                    ledger_snap = ledger_ref.get(transaction=transaction)
                    if ledger_snap.exists:
                        return True

                    wallet_snap = wallet_ref.get(transaction=transaction)
                    wallet_data = wallet_snap.to_dict() if wallet_snap.exists else {}
                    current_balance = float(wallet_data.get('balance', 0.0))
                    new_balance = current_balance + amount_paid_rupees
                    
                    # Update top-level wallets collection
                    transaction.set(wallet_ref, {
                        'balance': new_balance,
                        'updatedAt': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    
                    # Update users collection embedded wallet balance if user doc exists
                    user_snap = user_ref.get(transaction=transaction)
                    if user_snap.exists:
                        user_data = user_snap.to_dict() or {}
                        current_wallet = user_data.get('wallet', {})
                        current_wallet['balance'] = new_balance
                        transaction.update(user_ref, {'wallet': current_wallet})

                    # Record in wallet_ledger
                    transaction.set(ledger_ref, {
                        'userId': uid,
                        'type': 'credit',
                        'amount': amount_paid_rupees,
                        'reason': reason,
                        'referenceId': razorpay_payment_id,
                        'previousBalance': current_balance,
                        'newBalance': new_balance,
                        'timestamp': firestore.SERVER_TIMESTAMP
                    })

                    # Record in top-level transactions collection (for WalletScreen UI stream)
                    transaction.set(tx_ref, {
                        'userId': uid,
                        'type': 'credit',
                        'title': reason,
                        'amount': amount_paid_rupees,
                        'createdAt': firestore.SERVER_TIMESTAMP,
                        'referenceId': razorpay_payment_id
                    })
                    return True
                
                # Execute transaction
                transaction = db.transaction()
                update_user_wallet(transaction)
                
            elif app_type == 'pro':
                pro_ref = db.collection('users').document(uid)
                pro_wallet_ref = db.collection('wallets').document(uid)
                
                pro_ref.set({
                    'wallet': {
                        'platformDueAmount': firestore.Increment(-amount_paid_rupees)
                    }
                }, merge=True)
                pro_wallet_ref.set({
                    'platformDueAmount': firestore.Increment(-amount_paid_rupees)
                }, merge=True)
                
                pro_ref.collection('ledger').add({
                    'amount': amount_paid_rupees,
                    'type': 'dues_paid',
                    'status': 'completed',
                    'description': 'Platform Dues Paid via Razorpay',
                    'referenceId': razorpay_payment_id,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                
            elif app_type == 'org':
                org_ref = db.collection('organizations').document(uid) 
                
                org_ref.update({
                    'wallet.balance': firestore.Increment(amount_paid_rupees)
                })
                
                org_ref.collection('ledger').add({
                    'amount': amount_paid_rupees,
                    'type': 'funds_added',
                    'status': 'completed',
                    'description': 'Funds added to Org Wallet via Razorpay',
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
        
        return jsonify({
            'status': 'success', 
            'message': 'Payment verified successfully'
        }), 200
        
    except razorpay.errors.SignatureVerificationError:
        return jsonify({'error': 'Invalid payment signature'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'services': {
            'firestore': 'ok',
            'redis': 'ok'
        }
    }
    try:
        if redis_client:
            redis_client.ping()
    except Exception:
        health_status['services']['redis'] = 'degraded'
        health_status['status'] = 'degraded'
        
    return jsonify(health_status), 200 if health_status['status'] == 'healthy' else 503


if __name__ == '__main__':
    # Run the server
    app.run(host='0.0.0.0', port=5000, debug=True)
