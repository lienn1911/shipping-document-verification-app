"""Shared dashboard metrics and inbox filtering logic."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


COMPARISON_CATEGORY = "BL_COMPARISON"
COMPARISON_STATUSES = {"OK", "MISMATCH", "NEEDS_REVIEW"}


def _status(record: Mapping[str, Any]) -> str:
    confidence = record.get("confidence_score")
    if confidence is not None:
        try:
            if float(confidence) < 0.75:
                return "Review Required"
        except (TypeError, ValueError):
            pass
    if record.get("requires_human_review") or record.get("missing_fields") or record.get("unreadable_pdf"):
        return "Review Required"
    raw_status = str(record.get("processing_status", record.get("task_status", "")))
    if raw_status.casefold() in {"review", "review required", "needs_review"}:
        return "Review Required"
    if raw_status.casefold() in {"failed", "error"}:
        return "Failed"
    if raw_status.casefold() in {"processing", "queued"}:
        return "Processing"
    return "Completed"


def _is_comparison(record: Mapping[str, Any]) -> bool:
    return str(record.get("category", "")) == COMPARISON_CATEGORY or bool(
        record.get("is_comparison")
    )


def _has_mismatch(record: Mapping[str, Any]) -> bool:
    return bool(record.get("has_defect")) or str(record.get("status", "")) == "MISMATCH"


def _document_type(record: Mapping[str, Any]) -> str:
    explicit_type = str(record.get("document_type", "")).strip().upper()
    if explicit_type in {"SI", "BL", "INVOICE"}:
        return explicit_type
    category = str(record.get("category", ""))
    if category == "SI_REQUEST":
        return "SI"
    if category == "INVOICE_QUERY":
        return "Invoice"
    if category == COMPARISON_CATEGORY:
        return "BL"
    return "Unknown"


_DETECTED_TYPE_LABELS = {"SI": "SI", "BL": "BL", "INVOICE": "Invoice", "UNKNOWN": "Unknown"}


def _document_types(record: Mapping[str, Any]) -> set[str]:
    """Types of the attachments themselves, from content detection.

    Falls back to the email-level guess when no attachment was analysed
    (e.g. an email with no attachments).
    """
    detections = (record.get("document_analysis") or {}).get("detections") or {}
    found = {
        _DETECTED_TYPE_LABELS.get(str(item.get("type", "")).upper(), "Unknown")
        for item in detections.values()
    }
    return found or {_document_type(record)}


def useDashboardStats(inboxData: Iterable[Mapping[str, Any]]) -> dict[str, int | float]:
    """Calculate the six dashboard metrics from normalized inbox records."""
    records = list(inboxData)
    comparisons = [record for record in records if _is_comparison(record)]
    human_review = sum(_status(record) == "Review Required" for record in records)
    failed = sum(_status(record) == "Failed" for record in records)
    automatically_resolved = sum(
        _is_comparison(record)
        and _status(record) == "Completed"
        and str(record.get("status", "")) in {"OK", "MISMATCH"}
        for record in records
    )
    comparison_count = len(comparisons)
    return {
        "total_emails": len(records),
        "comparison_cases": comparison_count,
        "mismatch_count": sum(_has_mismatch(record) for record in comparisons),
        "human_review_count": human_review,
        "failed_cases": failed,
        "success_rate": automatically_resolved / comparison_count if comparison_count else 0.0,
    }


def useInboxFilters(
    inboxData: Iterable[Mapping[str, Any]],
    filters: Mapping[str, Any] | None = None,
    searchQuery: str = "",
) -> list[Mapping[str, Any]]:
    """Filter normalized inbox records by search, status, document type, and mismatch."""
    active_filters = filters or {}
    query = str(searchQuery).strip().casefold()
    selected_status = str(active_filters.get("status", "All"))
    selected_type = str(active_filters.get("document_type", "All"))
    selected_mismatch = str(active_filters.get("mismatch_status", "All"))

    def matches(record: Mapping[str, Any]) -> bool:
        searchable = f'{record.get("email_id", "")} {record.get("subject", "")}'.casefold()
        if query and query not in searchable:
            return False
        if selected_status != "All" and _status(record) != selected_status:
            return False
        if selected_type != "All" and selected_type not in _document_types(record):
            return False
        if selected_mismatch == "Has Mismatches" and not _has_mismatch(record):
            return False
        if selected_mismatch == "All Fields Match" and _has_mismatch(record):
            return False
        return True

    return [record for record in inboxData if matches(record)]


def normalized_inbox_records(
    emails: Iterable[Mapping[str, Any]],
    submission: Mapping[str, Mapping[str, Any]],
    internal_results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join raw inbox emails to pipeline output for dashboard consumers."""
    records = []
    for email in emails:
        email_id = str(email.get("email_id", ""))
        result = submission.get(email_id, {})
        detail = internal_results.get(email_id, {})
        records.append(
            {
                **email,
                **detail,
                **result,
                "email_id": email_id,
                "subject": str(email.get("subject", "")),
                "processing_status": detail.get(
                    "processing_status",
                    "Review Required"
                    if result.get("status") == "NEEDS_REVIEW"
                    else "Failed"
                    if result.get("status") == "FAILED"
                    else detail.get("task_status", "Completed"),
                ),
            }
        )
    return records


use_dashboard_stats = useDashboardStats
use_inbox_filters = useInboxFilters