"""Safe, optional Firebase Admin initialization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_error: str | None = None


def _credential_path() -> Path:
    configured = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    return Path(configured).expanduser() if configured else Path(__file__).with_name("serviceAccountKey.json")


def get_firestore_client() -> Any | None:
    global _error
    if os.getenv("FIREBASE_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        _error = "Firebase is disabled"
        return None
    key_path = _credential_path()
    if not key_path.is_file():
        _error = f"Firebase service-account file not found: {key_path}"
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(str(key_path)))
        _error = None
        return firestore.client()
    except Exception as exc:
        _error = str(exc) or type(exc).__name__
        return None


def integration_status() -> dict[str, Any]:
    return {"configured": _credential_path().is_file(), "connected": db is not None, "error": _error}


db = get_firestore_client()
