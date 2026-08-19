import firebase_admin
from firebase_admin import credentials, firestore
import json

if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("c:/Users/Lenovo/Desktop/amol/powersupply/powersupply/backend/serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    except Exception:
        firebase_admin.initialize_app()

db = firestore.client()

print("=== USERS COLLECTION PROS ===")
users = db.collection('users').get()
for u in users:
    d = u.to_dict()
    role = d.get('role')
    user_type = d.get('userType')
    is_pro = d.get('isPro') or d.get('isElectrician') or role in ['electrician', 'pro', 'Pro', 'Electrician', 'professional', 'trainee', 'electrician_pro', 'pro_user'] or user_type == 'pro'
    print(f"UID: {u.id}")
    print(f"  Name: {d.get('name')} | Phone: {d.get('phone')} | Role: {role} | UserType: {user_type}")
    print(f"  isOnline: {d.get('isOnline')} | isAvailable: {d.get('isAvailable')} | status: {d.get('status')}")
    print(f"  verificationStatus: {d.get('verificationStatus')} | accountStatus: {d.get('accountStatus')}")
    print(f"  location: {d.get('location')} | currentLocation: {d.get('currentLocation')} | lat: {d.get('latitude')}, lng: {d.get('longitude')}")
    print(f"  activeJobId: {d.get('activeJobId')} | hasActiveJob: {d.get('hasActiveJob')}")
    print("-" * 50)

print("\n=== ELECTRICIANS COLLECTION ===")
elecs = db.collection('electricians').get()
for e in elecs:
    d = e.to_dict()
    print(f"ID: {e.id} | Name: {d.get('name')} | isOnline: {d.get('isOnline')} | location: {d.get('location')}")
