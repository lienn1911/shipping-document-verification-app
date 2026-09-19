import os
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore


key_path = os.path.join(
    os.path.dirname(__file__),
    "serviceAccountKey.json"
)


cred = credentials.Certificate(key_path)


firebase_admin.initialize_app(cred)


db = firestore.client()