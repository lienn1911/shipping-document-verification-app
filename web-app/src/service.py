"""Thin adapters that expose the existing pipeline to CLI and web callers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

from .compare import compare_documents
from .extract import FIELDS, ReviewRequired
from .normalize import normalize_field
from .pipeline import process_inbox, summarize_results, validate_submission
from .versions import annotate_versions, refresh_changes


ProgressCallback = Callable[[int, int, str], None]
StageCallback = Callable[[str, str], None]

FIELD_LABELS = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight (KG)",
}


@dataclass(frozen=True)
class UploadedAttachment:
    filename: str
    data: bytes


@dataclass
class ProcessingArtifacts:
    emails: list[dict[str, Any]]
    submission: dict[str, Any]
    internal_results: dict[str, Any]
    summary: dict[str, Any]


class MemoryInbox:
    """Minimal Inbox-compatible object for one uploaded email."""

    def __init__(self, email: dict[str, Any], attachments: dict[str, bytes]):
        self._email = email
        self._attachments = attachments

    def __iter__(self):
        return iter([self._email])

    def read_bytes(self, path: str) -> bytes:
        if path not in self._attachments:
            raise OSError(f"Uploaded attachment is unavailable: {path}")
        return self._attachments[path]


class ProgressInbox:
    """Inbox proxy that reports progress without changing the core pipeline."""

    def __init__(self, inbox: Any, emails: list[dict[str, Any]], callback: ProgressCallback):
        self._inbox = inbox
        self._emails = emails
        self._callback = callback

    def __iter__(self):
        total = len(self._emails)
        for index, email in enumerate(self._emails):
            self._callback(index, total, email["email_id"])
            yield email

    def read_bytes(self, path: str) -> bytes:
        return self._inbox.read_bytes(path)


class SingleEmailInbox:
    """One-record view over a local inbox, used for safe per-case retries."""

    def __init__(self, inbox: Any, email: dict[str, Any]):
        self._inbox = inbox
        self._email = email

    def __iter__(self):
        return iter([self._email])

    def read_bytes(self, path: str) -> bytes:
        return self._inbox.read_bytes(path)


def load_inbox_class(bundle: Path) -> type[Any]:
    loader_path = bundle / "loader.py"
    if not loader_path.is_file():
        raise FileNotFoundError(f"Participant loader not found: {loader_path}")
    spec = importlib.util.spec_from_file_location("participant_loader", loader_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load participant loader: {loader_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Inbox


def open_local_inbox(bundle_path: str | Path) -> Any:
    bundle = Path(bundle_path).expanduser().resolve()
    if not (bundle / "inbox").is_dir():
        raise FileNotFoundError(f"Inbox directory not found: {bundle / 'inbox'}")
    if not (bundle / "sample_submission.json").is_file():
        raise FileNotFoundError(f"Sample submission not found: {bundle / 'sample_submission.json'}")
    Inbox = load_inbox_class(bundle)
    return Inbox(str(bundle))


def process_dataset(
    bundle_path: str | Path,
    progress_callback: ProgressCallback | None = None,
    stage_callback: StageCallback | None = None,
) -> ProcessingArtifacts:
    inbox = open_local_inbox(bundle_path)
    emails = inbox.emails()
    processing_inbox: Any = inbox
    if progress_callback is not None:
        processing_inbox = ProgressInbox(inbox, emails, progress_callback)

    try:
        from ai_service import batch_ai_enabled

        run_ai = batch_ai_enabled()
    except ImportError:
        run_ai = False
    submission, internal_results, summary = process_inbox(
        processing_inbox, stage_callback=stage_callback, run_ai=run_ai
    )
    validate_submission(submission, inbox.sample_submission())
    annotate_versions(inbox, emails, internal_results)  # annotation only; submission is untouched
    if progress_callback is not None:
        final_id = emails[-1]["email_id"] if emails else ""
        progress_callback(len(emails), len(emails), final_id)
    return ProcessingArtifacts(emails, submission, internal_results, summary)


def _uploaded_path(email_id: str, role: str, attachment: UploadedAttachment) -> str:
    suffix = Path(attachment.filename).suffix.casefold() or ".txt"
    return f"attachments/{email_id}_{role}{suffix}"


def process_single_email(
    email: dict[str, Any],
    si_attachment: UploadedAttachment | None = None,
    bl_attachment: UploadedAttachment | None = None,
    stage_callback: StageCallback | None = None,
) -> ProcessingArtifacts:
    required = ("email_id", "from", "subject", "body")
    missing = [key for key in required if key not in email]
    if missing:
        raise ValueError(f"Email JSON is missing required key(s): {', '.join(missing)}")
    email_id = str(email["email_id"]).strip()
    if not email_id:
        raise ValueError("email_id must not be blank")

    prepared = dict(email)
    prepared["email_id"] = email_id
    prepared_paths: list[str] = []
    uploaded: dict[str, bytes] = {}
    for role, attachment in (("SI", si_attachment), ("BL", bl_attachment)):
        if attachment is None:
            continue
        path = _uploaded_path(email_id, role, attachment)
        prepared_paths.append(path)
        uploaded[path] = attachment.data
    prepared["attachments"] = prepared_paths

    submission, internal_results, summary = process_inbox(MemoryInbox(prepared, uploaded), stage_callback=stage_callback)
    return ProcessingArtifacts([prepared], submission, internal_results, summary)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def retry_failed_email(
    bundle_path: str | Path,
    artifacts: ProcessingArtifacts,
    email_id: str,
) -> ProcessingArtifacts:
    """Re-run one failed email and merge the new result into the dataset."""
    if email_id not in artifacts.internal_results:
        raise KeyError(f"Unknown email ID: {email_id}")
    previous = artifacts.internal_results[email_id]
    if previous.get("processing_status", previous.get("task_status")) != "Failed":
        raise ValueError(f"{email_id} is not a failed case")

    inbox = open_local_inbox(bundle_path)
    email = inbox.get(email_id)
    submission, internal_results, _ = process_inbox(SingleEmailInbox(inbox, email))
    retried = internal_results[email_id]
    attempt = int(previous.get("processing_attempts", 1)) + 1
    retried["processing_attempts"] = attempt
    retried["retry_count"] = int(previous.get("retry_count", 0)) + 1
    retried["last_retry_at"] = _now()
    retried["error_history"] = [
        *previous.get("error_history", []),
        *retried.get("error_history", []),
    ]
    retried["status_history"] = [
        *previous.get("status_history", []),
        {"status": "Retrying", "at": retried["last_retry_at"]},
        *retried.get("status_history", [])[1:],
    ]

    updated = deepcopy(artifacts)
    updated.submission[email_id] = submission[email_id]
    updated.internal_results[email_id] = retried
    annotate_versions(inbox, updated.emails, updated.internal_results)
    updated.summary = summarize_results(updated.submission, updated.internal_results)
    return updated


def apply_human_review(
    artifacts: ProcessingArtifacts,
    email_id: str,
    si_values: dict[str, str],
    bl_values: dict[str, str],
    note: str = "",
) -> ProcessingArtifacts:
    """Confirm or correct all seven fields and resolve a manual-review case."""
    if email_id not in artifacts.submission:
        raise KeyError(f"Unknown email ID: {email_id}")
    if artifacts.submission[email_id]["category"] != "BL_COMPARISON":
        raise ValueError("Only document comparison cases can be reviewed")

    cleaned: dict[str, dict[str, str]] = {"si": {}, "bl": {}}
    missing: list[str] = []
    for role, values in (("si", si_values), ("bl", bl_values)):
        for field in FIELDS:
            value = str(values.get(field, "")).strip()
            if not value:
                missing.append(f"{role.upper()} {FIELD_LABELS[field]}")
            cleaned[role][field] = value
    if missing:
        raise ValueError("Enter a value for: " + ", ".join(missing))

    updated = deepcopy(artifacts)
    previous = updated.internal_results[email_id]
    original = deepcopy(previous.get("extracted", {}))
    try:
        comparison = compare_documents(cleaned["si"], cleaned["bl"])
    except ReviewRequired as exc:
        raise ValueError(
            "One or more reviewed values use an unsupported format: " + exc.detail
        ) from exc
    defect_fields = [item["field"] for item in comparison["mismatches"]]
    changed = any(
        str(original.get(role, {}).get(field, "")).strip() != cleaned[role][field]
        for role in ("si", "bl")
        for field in FIELDS
    )
    decision = "corrected" if changed else "confirmed"
    reviewed_at = _now()

    updated.submission[email_id] = {
        "category": "BL_COMPARISON",
        "status": comparison["status"],
        "review_reason": None,
        "defect_fields": defect_fields,
        "has_defect": bool(defect_fields),
    }
    previous.update(
        {
            **comparison,
            "review_reason": None,
            "internal_reason": None,
            "detail": None,
            "extracted": cleaned,
            "field_confidence": {field: 1.0 for field in FIELDS},
            "task_status": "Completed",
            "processing_status": "Completed",
            "retryable": False,
            "review_state": decision,
            "human_review": {
                "decision": decision,
                "reviewed_at": reviewed_at,
                "note": note.strip(),
                "original_extracted": original,
                "reviewed_values": deepcopy(cleaned),
            },
            "status_history": [
                *previous.get("status_history", []),
                {"status": "Human Review", "at": reviewed_at},
                {"status": "Completed", "at": reviewed_at},
            ],
        }
    )
    evidence = previous.setdefault("evidence", {})
    for role in ("si", "bl"):
        role_evidence = evidence.setdefault(role, {})
        for field in FIELDS:
            if str(original.get(role, {}).get(field, "")).strip() != cleaned[role][field]:
                role_evidence[field] = {
                    "source": "Human review correction",
                    "page": 1,
                    "line_start": "manual",
                    "line_end": "manual",
                    "label": FIELD_LABELS[field],
                    "excerpt": cleaned[role][field],
                    "confidence": 1.0,
                }

    updated.internal_results[email_id] = previous
    refresh_changes(updated.internal_results)
    updated.summary = summarize_results(updated.submission, updated.internal_results)
    return updated


def comparison_rows(detail: dict[str, Any]) -> list[dict[str, str]]:
    extracted = detail.get("extracted", {})
    si_fields = extracted.get("si", {})
    bl_fields = extracted.get("bl", {})
    rows: list[dict[str, str]] = []
    for field in FIELDS:
        si_value = si_fields.get(field)
        bl_value = bl_fields.get(field)
        if si_value is None or bl_value is None:
            status = "Missing"
        else:
            si_normalized = normalize_field(field, si_value)
            bl_normalized = normalize_field(field, bl_value)
            if si_normalized is None or bl_normalized is None:
                status = "Uncertain"
            elif si_normalized == bl_normalized:
                status = "Match"
            else:
                status = "Mismatch"
        rows.append(
            {
                "Field": FIELD_LABELS[field],
                "SI": si_value or "—",
                "BL": bl_value or "—",
                "Status": status,
                "Confidence": f"{detail.get('field_confidence', {}).get(field, 0):.0%}",
            }
        )
    return rows


def dataset_rows(artifacts: ProcessingArtifacts) -> list[dict[str, str]]:
    email_by_id = {email["email_id"]: email for email in artifacts.emails}
    rows: list[dict[str, str]] = []
    for email_id, result in artifacts.submission.items():
        email = email_by_id[email_id]
        rows.append(
            {
                "email_id": email_id,
                "subject": str(email.get("subject", "")),
                "category": result["category"],
                "confidence": f"{artifacts.internal_results[email_id].get('classification_confidence', 0):.0%}",
                "task status": artifacts.internal_results[email_id].get("task_status", "Completed"),
                "processing attempts": str(artifacts.internal_results[email_id].get("processing_attempts", 1)),
                "comparison status": result["status"],
                "mismatch fields": ", ".join(result["defect_fields"]) or "—",
                "review status": (
                    f"Human Review: {result['review_reason']}"
                    if result["status"] == "NEEDS_REVIEW"
                    else "—"
                ),
                "review decision": artifacts.internal_results[email_id].get("review_state", "—"),
            }
        )
    return rows


def submission_bytes(submission: dict[str, Any]) -> bytes:
    return (json.dumps(submission, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_artifacts(artifacts: ProcessingArtifacts, output_dir: str | Path) -> None:
    output = Path(output_dir).expanduser().resolve()
    write_json(output / "submission.json", artifacts.submission)
    write_json(output / "results.json", artifacts.internal_results)
    write_json(output / "summary.json", artifacts.summary)
    write_json(
        output / "errors.json",
        {
            email_id: detail.get("error_history", [])
            for email_id, detail in artifacts.internal_results.items()
            if detail.get("error_history")
        },
    )
    write_json(
        output / "review_decisions.json",
        {
            email_id: detail["human_review"]
            for email_id, detail in artifacts.internal_results.items()
            if detail.get("human_review")
        },
    )
