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
    elif "503" in message or "UNAVAILABLE" in message:
        message = "Gemini is temporarily overloaded (high demand). The local result is unaffected; retry in a minute."
    else:
        message = "Gemini request failed (" + type(exc).__name__ + "). Check connectivity and configuration, then retry."
    return {"enabled": True, "status": "unavailable", "error": message, "retryable": True}


DEFAULT_MODEL = "gemini-3.6-flash"  # override with GEMINI_MODEL; run scripts/check_gemini.py to list valid ids


DEFAULT_FALLBACKS = "gemini-3.1-flash-lite,gemini-3.5-flash-lite"  # tried in order if the main model fails
_ATTEMPT_TIMEOUT_MS = 20000


def fallback_models(primary: str) -> list[str]:
    """Backup models: GEMINI_FALLBACK_MODELS (comma list), blank = defaults, "none" = no backups."""
    raw = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    if raw.lower() == "none":
        return []
    names = [m.strip() for m in (raw or DEFAULT_FALLBACKS).split(",") if m.strip()]
    return [m for i, m in enumerate(names) if m != primary and m not in names[:i]]


def _outcome(exc: Exception) -> str:
    """Short label for why one attempt failed (shown in the attempts list)."""
    message = str(exc)
    if "PerDay" in message:
        return "daily_quota"
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        return "rate_limit"
    if any(t in message for t in ("401", "403", "API_KEY", "PERMISSION_DENIED")):
        return "credentials"
    if "404" in message:
        return "model_unavailable"
    if "503" in message or "UNAVAILABLE" in message:
        return "overloaded"
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "invalid_response"
    return "error"


def _stops_fallback(exc: Exception) -> bool:
    """Credential problems and exhausted daily quota will not improve on another model."""
    message = str(exc)
    return any(t in message for t in ("401", "403", "API_KEY", "PERMISSION_DENIED", "PerDay"))


def _load_local_env() -> None:
    from env_loader import load_environment

    load_environment()


# --------------------------------------------------------------------------- #
# Gemini vision: reading scanned documents (image-only PDFs)
# --------------------------------------------------------------------------- #

VISION_MAX_BYTES = 15 * 1024 * 1024
_VISION_TIMEOUT_MS = 45000  # one attempt; a page or two is quick, so a slow call is a stuck one
_VISION_MAX_MODELS = 2  # the main model plus one backup: worst case is about a minute and a half, not minutes
_vision_cache: dict[str, dict[str, Any]] = {}
_vision_failures: dict[str, tuple[float, str]] = {}
_vision_cooldown: list = [0.0, ""]  # [until, message]: a service-level failure pauses vision for every document
_SERVICE_LEVEL_OUTCOMES = {"overloaded", "rate_limit", "daily_quota", "credentials", "error"}

VISION_PROMPT = (
    "This is a scanned shipping document (a Shipping Instruction or a draft Bill of Lading). "
    "Transcribe ALL of its text exactly as printed, one printed line per output line. "
    "Write every labelled field as `Label: value` on one line, keeping the label wording exactly as printed; "
    "keep multi-line names and addresses on the lines directly below their label. "
    "Do NOT translate, correct, normalise, summarise or infer anything, and add no commentary. "
    "If a piece of text cannot be read, write [illegible] in its place. "
    "Start each page with a line `=== PAGE n ===`."
)


def vision_enabled() -> bool:
    """Gemini must be ready (opt-in, key present); GEMINI_VISION_ENABLED=false switches only this feature off."""
    if integration_status()["status"] != "ready":
        return False
    return os.getenv("GEMINI_VISION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def transcribe_scanned_pdf(raw: bytes) -> dict[str, Any]:
    """Transcribe an image-only PDF with Gemini. Returns {"text", "model"}.

    Raises RuntimeError with a short reason that never contains a key. Successful results are cached by
    document hash, and a failure is remembered for a minute so one outage is not retried by every reader.
    """
    if len(raw) > VISION_MAX_BYTES:
        raise RuntimeError("The scanned PDF is larger than the 15 MB reading limit")
    status = integration_status()
    doc_key = hashlib.sha256(raw + status["model"].encode()).hexdigest()
    if doc_key in _vision_cache:
        return {**_vision_cache[doc_key], "cached": True}
    failed_until, failed_message = _vision_failures.get(doc_key, (0.0, ""))
    if time.monotonic() < failed_until:
        raise RuntimeError(failed_message)
    if time.monotonic() < _vision_cooldown[0]:  # Gemini is down or refusing: do not make every scan wait for it
        raise RuntimeError(_vision_cooldown[1])
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Gemini support is not installed; run pip install -r requirements.txt") from exc

    last_error: Exception | None = None
    for model in [status["model"], *fallback_models(status["model"])][:_VISION_MAX_MODELS]:
        try:
            with genai.Client(
                api_key=os.environ["GEMINI_API_KEY"],
                http_options=types.HttpOptions(timeout=_VISION_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)),
            ) as client:
                response = client.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=raw, mime_type="application/pdf"), VISION_PROMPT],
                    config=types.GenerateContentConfig(temperature=0.0),
                )
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            if len(text) < 40:
                raise ValueError("Gemini returned no usable transcription")
            result = {"text": text, "model": model}
            if len(_vision_cache) >= 32:
                _vision_cache.pop(next(iter(_vision_cache)))
            _vision_cache[doc_key] = result
            return dict(result)
        except Exception as exc:
            last_error = exc
            if _stops_fallback(exc):
                break
    failure = failure_result(last_error)
    outcome = _outcome(last_error)
    pause = 3600 if outcome == "daily_quota" else 300 if outcome == "credentials" else 60
    _vision_failures[doc_key] = (time.monotonic() + pause, failure["error"])
    if outcome in _SERVICE_LEVEL_OUTCOMES:
        _vision_cooldown[0], _vision_cooldown[1] = time.monotonic() + pause, failure["error"]
    raise RuntimeError(failure["error"])


def batch_ai_enabled() -> bool:
    """Whether a full-dataset run should call Gemini for every case (default: no, use the per-case button)."""
    _load_local_env()
    return os.getenv("GEMINI_BATCH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


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
    attempts: list[dict[str, str]] = []
    last_error: Exception | None = None
    for model in [status["model"], *fallback_models(status["model"])]:
        try:
            with genai.Client(
                api_key=os.environ["GEMINI_API_KEY"],
                http_options=types.HttpOptions(timeout=_ATTEMPT_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)),
            ) as client:
                response = client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
                )
            payload = json.loads(response.text or "")
            if not isinstance(payload, dict) or not isinstance(payload.get("summary"), str):
                raise ValueError("Invalid Gemini response")
            attempts.append({"model": model, "outcome": "ok"})
            result = {
                **status, "status": "completed", "model": model,
                "fallback_used": model != status["model"], "attempts": attempts, "result": payload,
            }
            if len(_success_cache) >= 64:
                _success_cache.pop(next(iter(_success_cache)))
            _success_cache[cache_key] = result
            return result
        except Exception as exc:
            last_error = exc
            attempts.append({"model": model, "outcome": _outcome(exc)})
            if _stops_fallback(exc):
                break
    result = failure_result(last_error)
    if result["status"] == "quota_exhausted":
        _quota_until[identity] = time.monotonic() + (3600 if result.get("error_code") == "daily_quota" else 60)
    return {**status, **result, "attempts": attempts}
