"""End-to-end inbox classification and document comparison."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import traceback
from typing import Any, Callable

from .classify import BL_COMPARISON, classify_email
from .compare import compare_documents
from .extract import ReviewRequired, extract_attachment_with_evidence, identify_attachments_by_content


CONFIDENCE_REVIEW_THRESHOLD = 0.75
StageCallback = Callable[[str, str], None]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _history(final_status: str) -> list[dict[str, str]]:
    now = _timestamp()
    return [
        {"status": "Received", "at": now},
        {"status": "Processing", "at": now},
        {"status": final_status, "at": now},
    ]


def _error_entry(exc: Exception) -> dict[str, str]:
    return {
        "at": _timestamp(),
        "type": type(exc).__name__,
        "message": str(exc) or repr(exc),
        "traceback": traceback.format_exc(),
    }


def _ai_cross_check(extracted: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Run optional Gemini enrichment without making the core workflow depend on it."""
    try:
        from ai_service import analyze_shipping_document

        return analyze_shipping_document(extracted)
    except Exception as exc:
        return {
            "enabled": True,
            "status": "unavailable",
            "error": str(exc) or type(exc).__name__,
        }


DEFAULT_RESULT = {
    "status": "OK",
    "review_reason": None,
    "defect_fields": [],
    "has_defect": False,
}


def _process_comparison(
    inbox: Any, email: dict[str, Any], stage_callback: StageCallback | None = None, run_ai: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    paths: dict[str, str] | None = None
    extracted: dict[str, dict[str, str]] = {}
    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    document_analysis: dict[str, Any] = {}
    try:
        paths, document_analysis = identify_attachments_by_content(
            inbox, list(email.get("attachments", []))
        )
        extracted["si"], evidence["si"] = extract_attachment_with_evidence(inbox, paths["SI"], "SI")
        extracted["bl"], evidence["bl"] = extract_attachment_with_evidence(inbox, paths["BL"], "BL")
        lowest_confidence = min(
            item["confidence"] for role_evidence in evidence.values() for item in role_evidence.values()
        )
        if lowest_confidence < CONFIDENCE_REVIEW_THRESHOLD:
            raise ReviewRequired(
                "unreadable",
                "low_extraction_confidence",
                f"Extraction confidence {lowest_confidence:.0%} is below the {CONFIDENCE_REVIEW_THRESHOLD:.0%} review threshold",
            )
        if stage_callback:
            stage_callback(email["email_id"], "Comparing seven shipment fields")
        comparison = compare_documents(extracted["si"], extracted["bl"])
    except ReviewRequired as exc:
        document_analysis = exc.document_analysis or document_analysis
        submission = {
            "status": "NEEDS_REVIEW",
            "review_reason": exc.review_reason,
            "defect_fields": [],
            "has_defect": False,
        }
        detail = {
            "status": "NEEDS_REVIEW",
            "message": "Manual review required.",
            "review_reason": exc.review_reason,
            "internal_reason": exc.internal_reason,
            "detail": exc.detail,
            "attachments": paths or list(email.get("attachments", [])),
            "document_analysis": document_analysis,
            "extracted": extracted,
            "evidence": evidence,
            "field_confidence": {},
            "mismatches": [],
            "ai_analysis": {"enabled": False, "status": "skipped"},
            "task_status": "Review",
            "processing_status": "Review Required",
            "processing_attempts": 1,
            "retry_count": 0,
            "retryable": False,
            "error_history": [],
            "status_history": _history("Review"),
        }
        return submission, detail
    except Exception as exc:
        submission = {
            "status": "NEEDS_REVIEW",
            "review_reason": "unreadable",
            "defect_fields": [],
            "has_defect": False,
        }
        detail = {
            "status": "NEEDS_REVIEW",
            "message": "Processing failed. Retry or send the case to a human reviewer.",
            "review_reason": "unreadable",
            "internal_reason": "processing_error",
            "detail": str(exc) or repr(exc),
            "attachments": paths or list(email.get("attachments", [])),
            "document_analysis": document_analysis,
            "extracted": extracted,
            "evidence": evidence,
            "field_confidence": {},
            "mismatches": [],
            "ai_analysis": {"enabled": False, "status": "skipped"},
            "task_status": "Failed",
            "processing_status": "Failed",
            "processing_attempts": 1,
            "retry_count": 0,
            "retryable": True,
            "error_history": [_error_entry(exc)],
            "status_history": _history("Failed"),
        }
        return submission, detail

    defect_fields = [item["field"] for item in comparison["mismatches"]]
    submission = {
        "status": comparison["status"],
        "review_reason": None,
        "defect_fields": defect_fields,
        "has_defect": bool(defect_fields),
    }
    if run_ai:
        if stage_callback:
            stage_callback(email["email_id"], "Gemini cross-check · checking availability")
        ai_analysis = _ai_cross_check(extracted)
        if stage_callback:
            stage_callback(email["email_id"], f"Gemini cross-check · {ai_analysis.get('status', 'unknown')}")
    else:
        # Batch runs do not call Gemini: it would send every case to Google and exhaust the quota.
        ai_analysis = {"enabled": True, "status": "skipped", "reason": "batch"}
    detail = {
        **comparison,
        "review_reason": None,
        "internal_reason": None,
        "attachments": paths,
        "document_analysis": document_analysis,
        "extracted": extracted,
        "evidence": evidence,
        "field_confidence": {
            field: min(evidence["si"][field]["confidence"], evidence["bl"][field]["confidence"])
            for field in extracted["si"]
        },
        "ai_analysis": ai_analysis,
        "task_status": "Completed",
        "processing_status": "Completed",
        "processing_attempts": 1,
        "retry_count": 0,
        "retryable": False,
        "error_history": [],
        "status_history": _history("Completed"),
    }
    return submission, detail


def summarize_results(
    submission: dict[str, Any], internal_results: dict[str, Any]
) -> dict[str, Any]:
    """Rebuild summary counts after retries or human review edits."""
    category_counts = Counter(record["category"] for record in submission.values())
    status_counts = Counter(record["status"] for record in submission.values())
    review_counts = Counter(
        record["review_reason"] for record in submission.values() if record["review_reason"] is not None
    )
    comparisons = [record for record in submission.values() if record["category"] == BL_COMPARISON]
    failed = sum(
        detail.get("processing_status", detail.get("task_status")) == "Failed"
        for detail in internal_results.values()
    )
    resolved_reviews = sum(
        detail.get("review_state") in {"confirmed", "corrected"}
        for detail in internal_results.values()
    )
    return {
        "emails_processed": len(submission),
        "category_counts": dict(sorted(category_counts.items())),
        "comparison_requests": len(comparisons),
        "comparisons_completed": sum(r["status"] in {"OK", "MISMATCH"} for r in comparisons),
        "no_mismatch": sum(r["status"] == "OK" for r in comparisons),
        "mismatch": sum(r["status"] == "MISMATCH" for r in comparisons),
        "manual_review": sum(r["status"] == "NEEDS_REVIEW" for r in comparisons),
        "failed": failed,
        "resolved_reviews": resolved_reviews,
        "review_reason_counts": dict(sorted(review_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
    }


def process_inbox(
    inbox: Any, stage_callback: StageCallback | None = None, run_ai: bool = True
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    submission: dict[str, Any] = {}
    internal_results: dict[str, Any] = {}
    for email in inbox:
        email_id = email["email_id"]
        if stage_callback:
            stage_callback(email_id, "Classifying email")
        classification = classify_email(email)
        category = classification["category"]
        if category == BL_COMPARISON:
            if stage_callback:
                stage_callback(email_id, "Reading SI and draft BL")
            outcome, detail = _process_comparison(inbox, email, stage_callback, run_ai)
        else:
            outcome = dict(DEFAULT_RESULT)
            detail = {
                "status": "OK",
                "message": "Classification completed; no document comparison required.",
                "review_reason": None,
                "internal_reason": None,
                "attachments": list(email.get("attachments", [])),
                "extracted": {},
                "evidence": {},
                "field_confidence": {},
                "mismatches": [],
                "ai_analysis": {"enabled": False, "status": "skipped"},
                "task_status": "Completed",
                "processing_status": "Completed",
                "processing_attempts": 1,
                "retry_count": 0,
                "retryable": False,
                "error_history": [],
                "status_history": _history("Completed"),
            }
        submission[email_id] = {"category": category, **outcome}
        internal_results[email_id] = {
            "email_id": email_id,
            "category": category,
            "classification_rule": classification["rule"],
            "classification_confidence": classification["confidence"],
            "confidence_threshold": CONFIDENCE_REVIEW_THRESHOLD,
            **detail,
        }
        if stage_callback:
            final_stage = (
                "Processing failed" if detail["task_status"] == "Failed"
                else "Queued for human review" if detail["task_status"] == "Review"
                else "Completed"
            )
            stage_callback(email_id, final_stage)
    return submission, internal_results, summarize_results(submission, internal_results)


def validate_submission(submission: dict[str, Any], sample: dict[str, Any]) -> None:
    if set(submission) != set(sample):
        missing = sorted(set(sample) - set(submission))
        extra = sorted(set(submission) - set(sample))
        raise ValueError(f"Submission email IDs do not match sample; missing={missing}, extra={extra}")
    expected_keys = {"category", "status", "review_reason", "defect_fields", "has_defect"}
    allowed_categories = {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"}
    allowed_statuses = {"OK", "MISMATCH", "NEEDS_REVIEW"}
    allowed_reasons = {None, "wrong_doc_type", "missing_attachment", "unreadable", "missing_value"}
    for email_id, record in submission.items():
        if set(record) != expected_keys:
            raise ValueError(f"{email_id} has incorrect keys: {sorted(record)}")
        if record["category"] not in allowed_categories:
            raise ValueError(f"{email_id} has invalid category: {record['category']}")
        if record["status"] not in allowed_statuses:
            raise ValueError(f"{email_id} has invalid status: {record['status']}")
        if record["review_reason"] not in allowed_reasons:
            raise ValueError(f"{email_id} has invalid review_reason: {record['review_reason']}")
        if not isinstance(record["defect_fields"], list):
            raise ValueError(f"{email_id} defect_fields must be a list")
        if not isinstance(record["has_defect"], bool):
            raise ValueError(f"{email_id} has_defect must be a boolean")
        if record["has_defect"] != bool(record["defect_fields"]):
            raise ValueError(f"{email_id} has_defect and defect_fields disagree")
