import os
import firebase_admin
from firebase_admin import credentials, firestore, auth

def migrate_custom_claims():
    """
    Migrates role and permissions from the users collection to Firebase Auth Custom Claims.
    This enables Security Rules to evaluate roles without triggering expensive document reads.
    """
    # Initialize app if not already initialized
    if not firebase_admin._apps:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
        
    db = firestore.client()
    print("Starting custom claims migration...")
    
    # We only need to migrate users who are not standard customers (e.g. admins, orgs, pros if applicable)
    # If pros also need specific claims, add them here.
    roles_to_migrate = ['admin', 'super_admin', 'org', 'pro']
    
    migrated_count = 0
    users_ref = db.collection('users')
    
    # In a very large production database, we would paginate.
    # For a city pilot (~1k-5k users), pulling all is acceptable for a one-time script.
    docs = users_ref.where('role', 'in', roles_to_migrate).stream()
    
    for doc in docs:
        user_data = doc.to_dict()
        uid = doc.id
        role = user_data.get('role')
        permissions = user_data.get('permissions', {})
        
        claims = {
            'role': role,
            'permissions': permissions
        }
        
        try:
            auth.set_custom_user_claims(uid, claims)
            migrated_count += 1
            print(f"Set claims for {uid} -> Role: {role}")
        except Exception as e:
            print(f"Failed to set claims for {uid}: {e}")
            
    print(f"Migration complete. Updated {migrated_count} users.")

if __name__ == '__main__':
    migrate_custom_claims()
