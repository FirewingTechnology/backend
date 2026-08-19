import os
import firebase_admin
from firebase_admin import credentials, firestore, auth

script_dir = os.path.dirname(os.path.abspath(__file__))
cred_path = os.path.join(script_dir, "serviceAccountKey.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

COLLECTIONS_TO_CLEAR = [
    'users',
    'electricians',
    'jobs',
    'wallets',
    'chats',
    'organizations',
    'courses',
    'enquiries',
    'notifications'
]

ADMIN_EMAILS = [
    "admin@powersply.com",
    "superadmin@powersply.com",
    "hq@powersply.com"
]

def wipe_all_collections():
    print("=========================================")
    print(" WIKING ALL DUMMY DATA FROM FIRESTORE    ")
    print("=========================================")

    # 1. Clear all collections
    for col_name in COLLECTIONS_TO_CLEAR:
        print(f"--> Wiping collection: {col_name}")
        docs = db.collection(col_name).stream()
        count = 0
        for doc in docs:
            doc.reference.delete()
            count += 1
        print(f"    Deleted {count} document(s) from '{col_name}'")

    # Clear cms_content nested documents
    print("--> Wiping cms_content...")
    qt_docs = db.collection("cms_content").document("booking").collection("quick_tasks").stream()
    for doc in qt_docs:
        doc.reference.delete()

    # 2. Re-create ONLY authentic Admin Accounts
    print("\n--> Re-creating authentic Admin Accounts...")
    admin_accounts = [
        {"email": "admin@powersply.com", "password": "Admin@1234", "role": "admin", "name": "System Admin"},
        {"email": "superadmin@powersply.com", "password": "Admin@1234", "role": "super_admin", "name": "Super Admin"},
        {"email": "hq@powersply.com", "password": "Admin@1234", "role": "super_admin", "name": "HQ Admin"}
    ]

    for acc in admin_accounts:
        email = acc["email"]
        password = acc["password"]
        role = acc["role"]
        name = acc["name"]

        try:
            user = auth.get_user_by_email(email)
            auth.update_user(user.uid, password=password)
            uid = user.uid
        except Exception:
            user = auth.create_user(email=email, password=password)
            uid = user.uid

        # Set super_admins collection
        db.collection('super_admins').document(uid).set({
            'email': email,
            'role': role,
            'createdAt': firestore.SERVER_TIMESTAMP
        })

        # Set users collection entry for admin portal access
        db.collection('users').document(uid).set({
            'email': email,
            'name': name,
            'role': role,
            'createdAt': firestore.SERVER_TIMESTAMP
        }, merge=True)

        print(f"    [Admin Account Kept] {email} ({role})")

    print("\n=========================================")
    print(" *** ALL DUMMY DATA PURGED CLEANLY! *** ")
    print("=========================================")

if __name__ == "__main__":
    wipe_all_collections()
