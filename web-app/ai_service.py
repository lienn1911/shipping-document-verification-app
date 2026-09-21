"""Optional Gemini cross-check for the deterministic verification pipeline."""

from __future__ import annotations

import json
import os
import hashlib
import time
from pathlib import Path
from typing import Any

_success_cache: dict[str, dict[str, Any]] = {}
_quota_until: dict[str, float] = {}


def failure_result(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        daily = "PerDay" in message
        return {"enabled": True, "status": "quota_exhausted", "error_code": "daily_quota" if daily else "rate_limit",
                "error": "Gemini daily project quota is exhausted. Wait for the provider quota to reset or use a project with available quota." if daily else "Gemini request limit reached. Wait before retrying.",
                "retryable": True}
    if "403" in message or "API_KEY" in message or "401" in message:
        message = "Gemini rejected the credentials or project permissions. Check the configured API key."
    elif "404" in message:
        message = "The configured Gemini model is unavailable for this project."
    else:
        message = "Gemini request failed (" + type(exc).__name__ + "). Check connectivity and configuration, then retry."
    return {"enabled": True, "status": "unavailable", "error": message, "retryable": True}


DEFAULT_MODEL = "gemini-3.6-flash"  # override with GEMINI_MODEL; run scripts/check_gemini.py to list valid ids


def _load_local_env() -> None:
    from env_loader import load_environment

    load_environment()


def integration_status() -> dict[str, Any]:
    _load_local_env()
    enabled = os.getenv("GEMINI_AI_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    has_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return {
        "enabled": enabled,
        "configured": has_key,
        "model": os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL,
        "status": "ready" if enabled and has_key else "disabled" if not enabled else "missing_api_key",
    }


def analyze_shipping_document(extracted: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Return a Gemini second opinion; deterministic comparison stays authoritative."""
    status = integration_status()
    if not status["enabled"]:
        return status
    if not status["configured"]:
        raise RuntimeError("GEMINI_AI_ENABLED is true but GEMINI_API_KEY is missing")
    identity = hashlib.sha256((os.environ['GEMINI_API_KEY'] + status['model']).encode()).hexdigest()
    cache_key = hashlib.sha256((identity + json.dumps(extracted, sort_keys=True)).encode()).hexdigest()
    if cache_key in _success_cache:
        return {**_success_cache[cache_key], "cached": True}
    if time.monotonic() < _quota_until.get(identity, 0):
        return {**status, "status": "quota_exhausted", "error": "Automatic Gemini calls are paused after a quota error. The provider quota must recover before analysis can succeed.", "retryable": True}
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Gemini support is not installed; run pip install -r requirements.txt") from exc

    prompt = (
        "Act as a shipping-document quality reviewer. Compare the supplied Shipping "
        "Instruction (SI) and draft Bill of Lading (BL). Return JSON with keys "
        "summary (string), risk_level (low|medium|high), and observations (array of "
        "short strings). The deterministic field comparison is authoritative; identify "
        "contextual risks only.\n\n" + json.dumps(extracted, ensure_ascii=False)
    )
    try:
        with genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))) as client:
            response = client.models.generate_content(
                model=status["model"], contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
            )
        payload = json.loads(response.text or "")
        if not isinstance(payload, dict) or not isinstance(payload.get('summary'), str):
            raise ValueError("Invalid Gemini response")
        result = {**status, "status": "completed", "result": payload}
        if len(_success_cache) >= 64:
            _success_cache.pop(next(iter(_success_cache)))
        _success_cache[cache_key] = result
        return result
    except Exception as exc:
        result = failure_result(exc)
        if result['status'] == 'quota_exhausted':
            _quota_until[identity] = time.monotonic() + (3600 if result.get('error_code') == 'daily_quota' else 60)
        return {**status, **result}
