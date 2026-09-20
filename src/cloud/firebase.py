import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

if not firebase_admin._apps:
    cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        db = None
else:
    db = firestore.client()

def save_processing_result(email_id: str, data: dict):
    """Save to Firestore — persists across restarts"""
    if not db: return
    db.collection("processing_runs").document(email_id).set({
        **data,
        "timestamp": firestore.SERVER_TIMESTAMP
    })