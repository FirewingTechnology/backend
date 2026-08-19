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
]

def clear_collection(col_name):
    print(f"--> Clearing collection: {col_name}")
    col_ref = db.collection(col_name)
    docs = col_ref.stream()
    count = 0
    for doc in docs:
        doc.reference.delete()
        count += 1
    print(f"    Deleted {count} document(s) from '{col_name}'")

def main():
    print("=========================================")
    print(" RESETTING AND SEEDING POWER SUPPLY DB ")
    print("=========================================")

    # 1. Clear existing collections
    for col in COLLECTIONS_TO_CLEAR:
        clear_collection(col)

    # 2. Seed Admin & Super Admin Users in Firebase Auth & Firestore
    admin_accounts = [
        {"email": "admin@powersply.com", "password": "Admin@1234", "role": "admin", "name": "System Admin"},
        {"email": "superadmin@powersply.com", "password": "Admin@1234", "role": "super_admin", "name": "Super Admin"},
        {"email": "hq@powersply.com", "password": "Admin@1234", "role": "super_admin", "name": "HQ Admin"}
    ]

    print("\n--> Seeding Admin Accounts...")
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

        # Add to super_admins and users collection
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
        print(f"    [Admin] {email} ({role}) ready with password: {password}")

    # 3. Seed Electricians / Pros
    print("\n--> Seeding Electricians & Pros...")
    electricians_data = [
        {
            "uid": "pro_rahul_sharma_001",
            "phone": "+919876543211",
            "name": "Rahul Sharma",
            "email": "rahul.sharma@powersply.com",
            "role": "electrician",
            "city": "Pune",
            "state": "Maharashtra",
            "address": "Kothrud, Pune",
            "accountStatus": "verified",
            "verificationStatus": "approved",
            "verificationBadge": True,
            "isOnline": True,
            "isAvailable": True,
            "rating": 4.9,
            "completedJobs": 42,
            "rankingTier": "gold",
            "rewardLevel": "pro_master",
            "serviceRadius": 5.0,
            "specialties": ["AC Repair", "House Wiring", "Switchboard Repair", "Inverter Setup"],
            "currentLocation": firestore.GeoPoint(18.5204, 73.8567),
            "location": firestore.GeoPoint(18.5204, 73.8567),
            "wallet": {
                "balance": 1500.0,
                "platformDueAmount": 0.0,
                "pendingPayouts": 450.0,
                "totalEarned": 12500.0,
                "lifetimeEarnings": 12500.0,
                "status": "active"
            },
            "profile": {
                "name": "Rahul Sharma",
                "phone": "+919876543211",
                "email": "rahul.sharma@powersply.com",
                "address": "Kothrud, Pune",
                "city": "Pune",
                "state": "Maharashtra",
                "skills": ["AC Repair", "House Wiring", "Switchboard", "Inverter"],
                "experience": "5 Years",
                "serviceRadius": 5.0,
                "bankName": "HDFC Bank",
                "accountNumber": "50100234567890",
                "ifsc": "HDFC0001234",
                "completedStep": 8
            }
        },
        {
            "uid": "pro_vikram_singh_002",
            "phone": "+919876543212",
            "name": "Vikram Singh",
            "email": "vikram.singh@powersply.com",
            "role": "electrician",
            "city": "Mumbai",
            "state": "Maharashtra",
            "address": "Andheri West, Mumbai",
            "accountStatus": "verified",
            "verificationStatus": "approved",
            "verificationBadge": True,
            "isOnline": True,
            "isAvailable": True,
            "rating": 4.8,
            "completedJobs": 28,
            "rankingTier": "silver",
            "rewardLevel": "pro_expert",
            "serviceRadius": 5.0,
            "specialties": ["Fan Installation", "Switchboard Repair", "Commercial Wiring"],
            "currentLocation": firestore.GeoPoint(19.1197, 72.8464),
            "location": firestore.GeoPoint(19.1197, 72.8464),
            "wallet": {
                "balance": 850.0,
                "platformDueAmount": 0.0,
                "pendingPayouts": 200.0,
                "totalEarned": 7800.0,
                "lifetimeEarnings": 7800.0,
                "status": "active"
            },
            "profile": {
                "name": "Vikram Singh",
                "phone": "+919876543212",
                "email": "vikram.singh@powersply.com",
                "address": "Andheri West, Mumbai",
                "city": "Mumbai",
                "state": "Maharashtra",
                "skills": ["Fan Installation", "Switchboard Repair", "Commercial Wiring"],
                "experience": "3 Years",
                "serviceRadius": 5.0,
                "bankName": "ICICI Bank",
                "accountNumber": "000401567890",
                "ifsc": "ICIC0000004",
                "completedStep": 8
            }
        },
        {
            "uid": "pro_trainee_amit_003",
            "phone": "+919876543213",
            "name": "Amit Kumar (Trainee)",
            "email": "amit.trainee@powersply.com",
            "role": "trainee",
            "city": "Pune",
            "state": "Maharashtra",
            "address": "Viman Nagar, Pune",
            "accountStatus": "pending_kyc",
            "verificationStatus": "draft",
            "verificationBadge": False,
            "isOnline": False,
            "isAvailable": False,
            "rating": 5.0,
            "completedJobs": 0,
            "rankingTier": "bronze",
            "rewardLevel": "starter",
            "serviceRadius": 3.0,
            "specialties": ["Basic Electrical Safety", "Apprentice"],
            "wallet": {
                "balance": 0.0,
                "platformDueAmount": 0.0,
                "pendingPayouts": 0.0,
                "totalEarned": 0.0,
                "lifetimeEarnings": 0.0,
                "status": "locked"
            }
        }
    ]

    for pro in electricians_data:
        uid = pro["uid"]
        # Seed into /electricians collection
        db.collection("electricians").document(uid).set(pro)
        # Seed into /users collection for unified auth lookups
        db.collection("users").document(uid).set(pro, merge=True)
        # Seed into /wallets collection
        db.collection("wallets").document(uid).set(pro["wallet"])
        print(f"    [Electrician/Pro] {pro['name']} ({pro['role']}) created [ID: {uid}]")

    # 4. Seed Test Customer Users
    print("\n--> Seeding Customer Users...")
    customers = [
        {
            "uid": "user_customer_test_9604434223",
            "phone": "+919604434223",
            "name": "PowrSply Customer",
            "email": "customer@powersply.com",
            "role": "customer",
            "city": "Pune",
            "state": "Maharashtra",
            "address": "Baner, Pune",
            "wallet": {"balance": 500.0, "status": "active"},
            "createdAt": firestore.SERVER_TIMESTAMP
        },
        {
            "uid": "user_customer_test_9876543210",
            "phone": "+919876543210",
            "name": "Ananya Roy",
            "email": "ananya.roy@example.com",
            "role": "customer",
            "city": "Mumbai",
            "state": "Maharashtra",
            "address": "Bandra West, Mumbai",
            "wallet": {"balance": 1200.0, "status": "active"},
            "createdAt": firestore.SERVER_TIMESTAMP
        }
    ]

    for cust in customers:
        uid = cust["uid"]
        db.collection("users").document(uid).set(cust)
        db.collection("wallets").document(uid).set(cust["wallet"])
        print(f"    [Customer] {cust['name']} ({cust['phone']}) created [ID: {uid}]")

    # 5. Seed Quick Tasks (Services) in Firestore
    print("\n--> Seeding Quick Tasks (Services)...")
    quick_tasks = [
        {"title": "AC Repair & Servicing", "price": 500, "category": "AC", "desc": "Comprehensive AC cleaning, gas check, and cooling repair"},
        {"title": "Ceiling Fan Installation", "price": 200, "category": "Fan", "desc": "Standard ceiling fan or wall fan mounting & wiring"},
        {"title": "Switchboard Repair", "price": 150, "category": "Switchboard", "desc": "Replacement or repair of burnt/faulty switches & sockets"},
        {"title": "Inverter & Battery Setup", "price": 800, "category": "Inverter", "desc": "Home inverter setup, battery terminal check & wiring"},
        {"title": "Full House Wiring Inspection", "price": 1200, "category": "Wiring", "desc": "Complete electrical safety audit & circuit testing"},
        {"title": "MCB & Fuse Replacement", "price": 250, "category": "Breakers", "desc": "Tripped or damaged MCB/ELCB breaker replacement"}
    ]

    qt_ref = db.collection("cms_content").document("booking").collection("quick_tasks")
    existing_qt = qt_ref.get()
    for doc in existing_qt:
        doc.reference.delete()

    for task in quick_tasks:
        qt_ref.add(task)
        print(f"    [Task] {task['title']} (Rs. {task['price']}) added")

    # 6. Seed Sample Organizations
    print("\n--> Seeding Organizations...")
    orgs = [
        {
            "orgId": "org_lnt_electrical",
            "name": "L&T Electrical Solutions",
            "category": "Commercial & Industrial",
            "city": "Mumbai",
            "rating": 4.9,
            "completedProjects": 140,
            "services": ["Substation Wiring", "Solar Panel Grid Setup", "High Voltage Transformer Maintenance"],
            "contactEmail": "contact@lntelectrical.com",
            "contactPhone": "+912228570000"
        },
        {
            "orgId": "org_schneider_pro",
            "name": "Schneider Power Infra India",
            "category": "Industrial Automation",
            "city": "Pune",
            "rating": 4.8,
            "completedProjects": 95,
            "services": ["Building Management Systems", "Smart Grid Installation", "Industrial Panel Wiring"],
            "contactEmail": "info@schneider-infra.in",
            "contactPhone": "+912066800000"
        }
    ]

    for org in orgs:
        db.collection("organizations").document(org["orgId"]).set(org)
        print(f"    [Organization] {org['name']} ({org['city']}) created")

    # 7. Seed Sample Courses for Trainees
    print("\n--> Seeding Training Courses...")
    courses = [
        {
            "courseId": "course_elec_101",
            "title": "Basic Electrical Safety & Wiring 101",
            "category": "Fundamentals",
            "totalLessons": 5,
            "duration": "2 Hours",
            "description": "Learn essential safety guidelines, earthing principles, and wire color codes.",
            "isPublished": True
        },
        {
            "courseId": "course_inverter_202",
            "title": "Home Inverter & UPS Installation Masterclass",
            "category": "Advanced",
            "totalLessons": 8,
            "duration": "3.5 Hours",
            "description": "Step-by-step masterclass on sizing batteries, inverter load calculations, and troubleshooting.",
            "isPublished": True
        }
    ]

    for course in courses:
        db.collection("courses").document(course["courseId"]).set(course)
        print(f"    [Course] {course['title']} created")

    print("\n=========================================")
    print(" *** DATABASE CLEARED & SEEDED SUCCESSFULLY! *** ")
    print("=========================================")

if __name__ == "__main__":
    main()
