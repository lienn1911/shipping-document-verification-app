"""Manual check of the Firebase setup. Not a unit test.

Run from the web-app folder:
  python scripts/check_firebase.py            # real round trip: credentials + Firestore reachable. Reads only.
  python scripts/check_firebase.py --write    # also writes ONE test record to Firestore
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firebase_config import db, integration_status  # noqa: E402

COLLECTION = "verification_results"


def explain(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "notfound" in lowered or "404" in lowered or "does not exist" in lowered:
        hint = "The Firestore database probably does not exist yet. Firebase console -> Build -> Firestore Database -> Create database."
    elif "permission" in lowered or "403" in lowered:
        hint = "This service account is not allowed to use Firestore. Generate the key from the same Firebase project, and check its role."
    elif "deadline" in lowered or "unavailable" in lowered or "timeout" in lowered:
        hint = "Could not reach Google (network or a temporary outage). Try again."
    else:
        hint = "See the message above."
    return f"{text[:300]}\n-> {hint}"


status = integration_status()
print("Firebase:", status)
if db is None:
    raise SystemExit("Client was not created (see 'error' above and .env.example).")
print("Credentials parsed. Contacting Firestore (read only)...")
try:
    sampled = list(db.collection(COLLECTION).limit(1).get(timeout=20))
except Exception as exc:  # noqa: BLE001 - a diagnostic script reports everything
    raise SystemExit("FAILED: " + explain(exc))
print(f"OK: Firestore reachable ({len(sampled)} record sampled from '{COLLECTION}').")

if "--write" in sys.argv:
    try:
        db.collection(COLLECTION).add({"email_id": "connection-check", "status": "Test", "message": "Firebase connection test"})
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("WRITE FAILED: " + explain(exc))
    print(f"Wrote one test record to '{COLLECTION}'.")
else:
    print("Nothing written. Re-run with --write to write a test record.")
