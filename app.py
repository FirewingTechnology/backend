import os
import razorpay
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import json

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

@app.route('/')
def home():
    return jsonify({"status": "running"}), 200

@app.route('/create-order', methods=['POST'])
def create_order():
    try:
        data = request.json
        amount = data.get('amount') # amount in rupees
        uid = data.get('uid', 'anonymous')
        
        if not amount:
            return jsonify({'error': 'Amount is required'}), 400

        # Razorpay expects amount in paise (1 INR = 100 paise)
        order_amount = int(float(amount) * 100)
        
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

@app.route('/verify-signature', methods=['POST'])
def verify_signature():
    try:
        data = request.json
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        uid = data.get('uid') 
        amount_paid = data.get('amount') # amount in rupees
        app_type = data.get('app_type', 'pro') # default to 'pro' for backwards compatibility
        reason = data.get('reason', 'Payment via Razorpay')
        
        # The verify function will throw an error if the signature is invalid
        client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
        
        # --- Signature is VALID. We can safely update the database ---
        
        if uid and amount_paid:
            user_ref = db.collection('users').document(uid)
            
            if app_type == 'user':
                @firestore.transactional
                def update_user_wallet(transaction, user_ref):
                    user_snapshot = user_ref.get(transaction=transaction)
                    if not user_snapshot.exists:
                        return False
                    
                    user_data = user_snapshot.to_dict()
                    current_wallet = user_data.get('wallet', {'balance': 0.0, 'totalSpent': 0.0})
                    current_balance = float(current_wallet.get('balance', 0.0))
                    
                    new_balance = current_balance + float(amount_paid)
                    current_wallet['balance'] = new_balance
                    
                    # Update the user doc
                    transaction.update(user_ref, {'wallet': current_wallet})
                    
                    # Add to wallet_ledger
                    ledger_ref = db.collection('wallet_ledger').document()
                    transaction.set(ledger_ref, {
                        'userId': uid,
                        'type': 'credit',
                        'amount': float(amount_paid),
                        'reason': reason,
                        'referenceId': razorpay_payment_id,
                        'previousBalance': current_balance,
                        'newBalance': new_balance,
                        'timestamp': firestore.SERVER_TIMESTAMP
                    })
                    
                    # Also add a transaction for UI history compatibility if needed
                    tx_ref = user_ref.collection('transactions').document()
                    transaction.set(tx_ref, {
                        'type': 'credit',
                        'title': reason,
                        'amount': float(amount_paid),
                        'createdAt': firestore.SERVER_TIMESTAMP
                    })
                    return True
                
                # Execute transaction
                transaction = db.transaction()
                update_user_wallet(transaction, user_ref)
                
            else:
                # Example Firebase update for PRO app:
                user_ref.update({
                    'wallet.platformDueAmount': firestore.Increment(-float(amount_paid))
                })
                
                # Also log the transaction in the user's ledger subcollection
                user_ref.collection('ledger').add({
                    'amount': float(amount_paid),
                    'type': 'dues_paid',
                    'status': 'completed',
                    'description': 'Platform Dues Paid via Razorpay',
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

if __name__ == '__main__':
    # Run the server
    app.run(host='0.0.0.0', port=5000, debug=True)
