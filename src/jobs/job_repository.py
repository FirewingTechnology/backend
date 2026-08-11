from firebase_admin import firestore
from typing import Dict, Any

class JobRepository:
    def __init__(self):
        self.db = firestore.client()
        self.collection = self.db.collection('job_requests')

    def create_job(self, job_data: Dict[str, Any]) -> str:
        # Create a new document with an auto-generated ID
        _, doc_ref = self.collection.add(job_data)
        return doc_ref.id

    def update_job(self, job_id: str, updates: Dict[str, Any]):
        self.collection.document(job_id).update(updates)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        doc = self.collection.document(job_id).get()
        if not doc.exists:
            raise ValueError("Job not found")
        return doc.to_dict()
