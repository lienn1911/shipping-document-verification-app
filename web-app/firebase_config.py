"""Safe, optional Firebase Admin initialization.

Credentials can come from a local file (development) or from the
FIREBASE_SERVICE_ACCOUNT_JSON setting (hosting), which holds the service-account
JSON itself or its base64 encoding. Error text never includes credential contents.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

_error: str | None = None


def _load_local_env() -> None:
    from env_loader import load_environment

    load_environment()


def _credential_path() -> Path:
    """Service-account file. A relative path is resolved next to this module (web-app/), never
    against whichever folder the app happened to be started from."""
    configured = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not configured:
        return Path(__file__).resolve().parent / "serviceAccountKey.json"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


def _inline_credentials() -> dict[str, Any] | None:
    """Parse FIREBASE_SERVICE_ACCOUNT_JSON (raw JSON or base64). None if not set."""
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        raw = base64.b64decode(raw, validate=True).decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("not a JSON object")
    return parsed


def _credentials_available() -> bool:
    return bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()) or _credential_path().is_file()


def get_firestore_client() -> Any | None:
    global _error
    _load_local_env()
    if os.getenv("FIREBASE_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        _error = "Firebase is disabled"
        return None
    try:
        inline = _inline_credentials()
    except Exception:
        _error = "FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON or base64 JSON"
        return None
    key_path = _credential_path()
    if inline is None and not key_path.is_file():
        _error = f"Firebase service-account file not found: {key_path}"
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(inline if inline is not None else str(key_path)))
        _error = None
        return firestore.client()
    except Exception as exc:
        # Certificate errors can echo parts of the credential; report only the type.
        _error = "Firebase rejected the credentials" if inline is not None else (str(exc) or type(exc).__name__)
        return None


def integration_status() -> dict[str, Any]:
    return {"configured": _credentials_available(), "connected": db is not None, "error": _error}


db = get_firestore_client()
