"""Normalize AI results, including strings retained in older UI sessions."""

import json
from typing import Any


def normalize_ai_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        if not value.strip():
            return {"status": "skipped"}
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        value = parsed if isinstance(parsed, dict) else {"summary": value}
    if not isinstance(value, dict):
        return {"status": "skipped"}
    result = dict(value)
    if "summary" in result and "status" not in result:
        result = {"status": "completed", "result": result}
    if result.get("status") == "completed":
        payload = result.get("result")
        if isinstance(payload, str):
            payload = {"summary": payload}
        elif not isinstance(payload, dict):
            payload = {"summary": "AI review returned an unsupported response."}
        payload = dict(payload)
        observations = payload.get("observations")
        payload["observations"] = (
            observations if isinstance(observations, list)
            else [observations] if isinstance(observations, str) else []
        )
        result["result"] = payload
    return result
