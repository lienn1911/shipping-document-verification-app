"""What is stored in Firestore for each processed email, and how it is saved.

A record holds the outcome, the extracted values, the mismatches, which documents were read (and whether a
scan was read by Gemini vision), version tracking, the Gemini verdict and any human-review decision.
It never holds the email body or the sender address.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COLLECTION = "verification_results"
BATCH_SIZE = 400  # Firestore allows 500 writes per batch


def _changed_fields(review: dict[str, Any]) -> list[dict[str, str]]:
    """Fields a reviewer changed: [{"document": "BL", "field": ..., "before": ..., "after": ...}]."""
    changes = []
    original, reviewed = review.get("original_extracted") or {}, review.get("reviewed_values") or {}
    for role in ("si", "bl"):
        for field, after in (reviewed.get(role) or {}).items():
            before = (original.get(role) or {}).get(field)
            if before is not None and str(before) != str(after):
                changes.append({"document": role.upper(), "field": field, "before": str(before), "after": str(after)})
    return changes


def audit_record(email_id: str, result: dict[str, Any], detail: dict[str, Any], subject: str = "") -> dict[str, Any]:
    evidence = detail.get("evidence") or {}
    record: dict[str, Any] = {
        "email_id": email_id,
        "category": result.get("category", ""),
        "status": result.get("status", ""),
        "review_reason": result.get("review_reason"),
        "defect_fields": list(result.get("defect_fields") or []),
        "subject": subject,
        "classification_confidence": detail.get("classification_confidence", 0),
        "processing_status": detail.get("processing_status", ""),
        "mismatches": detail.get("mismatches") or [],
        "extracted": detail.get("extracted") or {},
        "documents": [
            {"file": Path(path).name, "type": info.get("type"), "label": info.get("label"), "confidence": info.get("confidence")}
            for path, info in ((detail.get("document_analysis") or {}).get("detections") or {}).items()
        ],
        "punctuation_ignored": list(detail.get("ignored_punctuation") or []),
        "read_by_ai": any(
            item.get("read_method") == "gemini_vision"
            for role_items in evidence.values()
            for item in (role_items or {}).values()
        ),
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    version = detail.get("version_info") or {}
    if version.get("shipment_key"):
        record["version"] = {
            key: version.get(key)
            for key in ("shipment_key", "version", "version_count", "is_latest", "duplicate_of", "superseded_by")
        }
    ai = detail.get("ai_analysis") or {}
    if ai.get("status") == "completed":
        record["ai"] = {
            "status": "completed",
            "risk_level": (ai.get("result") or {}).get("risk_level"),
            "model": ai.get("model"),
        }
    review = detail.get("human_review")
    if review:
        record["human_review"] = {
            "decision": review.get("decision"),
            "reviewed_at": review.get("reviewed_at"),
            "note": review.get("note", ""),
            "changed_fields": _changed_fields(review),
        }
    return record


def save_cases(db: Any, artifacts: Any, email_ids: Iterable[str] | None = None) -> int:
    """Write records with stable ids (re-running overwrites, never duplicates). Returns how many were saved."""
    subject_of = {str(e.get("email_id")): str(e.get("subject", "")) for e in artifacts.emails}
    wanted = list(email_ids) if email_ids is not None else list(artifacts.submission)
    saved = 0
    for start in range(0, len(wanted), BATCH_SIZE):
        batch = db.batch()
        for email_id in wanted[start:start + BATCH_SIZE]:
            record = audit_record(
                email_id,
                artifacts.submission.get(email_id, {}),
                artifacts.internal_results.get(email_id, {}),
                subject_of.get(email_id, ""),
            )
            batch.set(db.collection(COLLECTION).document(email_id), record, merge=True)
            saved += 1
        batch.commit()
    return saved
