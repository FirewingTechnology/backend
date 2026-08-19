import firebase_admin
from firebase_admin import credentials, firestore, auth

import os
script_dir = os.path.dirname(os.path.abspath(__file__))
cred_path = os.path.join(script_dir, "serviceAccountKey.json")
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

db = firestore.client()

accounts = [
    {"email": "admin@powersply.com", "password": "Admin@1234", "role": "admin"},
    {"email": "superadmin@powersply.com", "password": "Admin@1234", "role": "super_admin"},
    {"email": "hq@powersply.com", "password": "Admin@1234", "role": "super_admin"}
]

for acc in accounts:
    email = acc["email"]
    password = acc["password"]
    try:
        user = auth.get_user_by_email(email)
        auth.update_user(user.uid, password=password)
        print(f"User {email} updated with password: {password}")
    except Exception as e:
        user = auth.create_user(email=email, password=password)
        print(f"Created new Super Admin: {email} with UID: {user.uid}")

    db.collection('super_admins').document(user.uid).set({
        'email': email,
        'role': 'super_admin',
        'createdAt': firestore.SERVER_TIMESTAMP
    })

    db.collection('users').document(user.uid).set({
        'email': email,
        'role': 'super_admin',
        'createdAt': firestore.SERVER_TIMESTAMP
    }, merge=True)

print(f"Successfully configured {email} as a super_admin!")
