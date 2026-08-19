import os
import firebase_admin
from firebase_admin import credentials, firestore, auth, storage

script_dir = os.path.dirname(os.path.abspath(__file__))
cred_path = os.path.join(script_dir, "serviceAccountKey.json")

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'powersupply-55af5.firebasestorage.app'
    })

db = firestore.client()

ALL_COLLECTIONS = [
    'wallet_ledger',
    'jobs',
    'chats',
    'users',
    'electricians',
    'wallets',
    'organizations',
    'courses',
    'enquiries',
    'admin_logs',
    'notifications',
    'cms_content'
]

def delete_collection_recursive(col_ref):
    docs = col_ref.stream()
    count = 0
    for doc in docs:
        # Check subcollections
        subcols = doc.reference.collections()
        for subcol in subcols:
            delete_collection_recursive(subcol)
        doc.reference.delete()
        count += 1
    return count

def clean_database():
    print("=========================================")
    print(" PURGING ALL DATABASE COLLECTIONS (CLEAN)")
    print("=========================================")

    for col_name in ALL_COLLECTIONS:
        col_ref = db.collection(col_name)
        cnt = delete_collection_recursive(col_ref)
        print(f"--> Cleared '{col_name}': {cnt} document(s) deleted")

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

        db.collection('super_admins').document(uid).set({
            'email': email,
            'role': role,
            'createdAt': firestore.SERVER_TIMESTAMP
        })

        db.collection('users').document(uid).set({
            'email': email,
            'name': name,
            'role': role,
            'createdAt': firestore.SERVER_TIMESTAMP
        }, merge=True)

        print(f"    [Admin Kept] {email} ({role})")

def clean_storage():
    print("\n=========================================")
    print(" PURGING FIREBASE STORAGE BUCKET         ")
    print("=========================================")
    try:
        bucket = storage.bucket()
        blobs = list(bucket.list_blobs())
        print(f"--> Found {len(blobs)} file(s) in Storage bucket")
        for blob in blobs:
            blob.delete()
            print(f"    Deleted file: {blob.name}")
        print("--> Storage Bucket is 100% clean!")
    except Exception as e:
        print(f"--> Storage clean note: {e}")

if __name__ == "__main__":
    clean_database()
    clean_storage()
    print("\n=========================================")
    print(" *** DATABASE & STORAGE ARE NOW 100% CLEAN! *** ")
    print("=========================================")
