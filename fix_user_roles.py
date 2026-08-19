import os
import firebase_admin
from firebase_admin import credentials, firestore

script_dir = os.path.dirname(os.path.abspath(__file__))
cred_path = os.path.join(script_dir, "serviceAccountKey.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def fix_pro_user(uid="ZTQdLhKfGHNtYYpq2IEgHeashHK2"):
    print(f"--> Updating role for UID: {uid} to 'electrician' / 'Pro'...")
    
    user_ref = db.collection('users').document(uid)
    user_snap = user_ref.get()
    
    user_data = {
        'role': 'electrician',
        'userType': 'pro',
        'accountStatus': 'under_review',
        'verificationStatus': 'submitted'
    }
    
    if user_snap.exists:
        user_ref.update(user_data)
        existing = user_snap.to_dict()
        user_data = {**existing, **user_data}
    else:
        user_ref.set(user_data)
        
    # Also ensure entry in electricians collection
    elec_ref = db.collection('electricians').document(uid)
    elec_ref.set(user_data, merge=True)
    
    print(f" Successfully updated {uid} to Pro / Electrician!")

if __name__ == "__main__":
    fix_pro_user()
