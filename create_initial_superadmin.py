import firebase_admin
from firebase_admin import credentials, firestore, auth

# Path to your downloaded service account key
cred = credentials.Certificate(r"c:\Users\Lenovo\Downloads\powersupply-55af5-firebase-adminsdk-fbsvc-c4568ad7e2.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

email = "superadmin@powersply.com"
password = "SuperAdmin123!"

try:
    # Try to get the user if they already exist
    user = auth.get_user_by_email(email)
    print(f"User {email} already exists with UID: {user.uid}")
except Exception as e:
    # Otherwise create the new super admin
    user = auth.create_user(
        email=email,
        password=password
    )
    print(f"Created new Super Admin: {email} with UID: {user.uid}")

# Force add them to the super_admins collection
db.collection('super_admins').document(user.uid).set({
    'email': email,
    'role': 'super_admin',
    'createdAt': firestore.SERVER_TIMESTAMP
})

print(f"Successfully configured {email} as a super_admin in Firestore!")
