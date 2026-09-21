"""Manual check of the Firebase setup. Not a unit test.

Run from the web-app folder:
  python scripts/check_firebase.py            # connection check only, writes nothing
  python scripts/check_firebase.py --write    # also writes ONE test record to Firestore
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firebase_config import db, integration_status  # noqa: E402

print("Firebase:", integration_status())
if db is None:
    raise SystemExit("Not connected. See the error above and .env.example.")
print("Connected to Firestore.")
if "--write" in sys.argv:
    db.collection("verification_results").add(
        {"email_id": "connection-check", "status": "Test", "message": "Firebase connection test"}
    )
    print("Wrote one test record to 'verification_results'.")
else:
    print("Nothing written. Re-run with --write to write a test record.")
