from firebase_config import db


data = {
    "email_id": "test001",
    "status": "Mismatch",
    "message": "Firebase connection test"
}


db.collection("verification_results").add(data)


print("Firebase connected!")