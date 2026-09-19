# hackathon completeness enhancements: mismatch summary, review context, classification visibility, retry-ready states
# Requirement enhanced build: dashboard + inbox review flow + verification reporting improvements
"""Local Streamlit interface for the shipping document checker."""

from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

import streamlit as st

# Firebase Cloud Infrastructure
try:
    from firebase_config import db, integration_status as firebase_status
except Exception:
    db = None

    def firebase_status() -> dict[str, Any]:
        return {"configured": False, "connected": False, "error": "Firebase import failed"}

from src.pipeline import validate_submission
from src.ai_results import normalize_ai_result
from src.reply_draft import build_reply
from src.service import open_local_inbox
from src.extract import FIELDS
from src.reporting import (
    PdfExportUnavailable,
    results_csv_bytes,
    results_json_bytes,
    results_pdf_bytes,
)
from src.service import (
    FIELD_LABELS,
    ProcessingArtifacts,
    UploadedAttachment,
    apply_human_review,
    comparison_rows,
    dataset_rows,
    process_dataset,
    process_single_email,
    retry_failed_email,
    submission_bytes,
    write_artifacts,
)
from src.dashboard import normalized_inbox_records, useDashboardStats, useInboxFilters
from src.versions import version_summary


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "output"


def find_default_bundle() -> Path:
    candidates = (
        PROJECT_ROOT.parent / "local-data" / "participant-bundle",
        PROJECT_ROOT.parent / "sdoc-hackathon-bundle",
        PROJECT_ROOT / "data",
    )
    return next((path for path in candidates if (path / "inbox").is_dir()), candidates[1])


DEFAULT_BUNDLE = find_default_bundle()
DEMO_BUNDLE = PROJECT_ROOT / "demo" / "versions-bundle"

CATEGORY_LABELS = {
    "BL_COMPARISON": "Document Comparison Request",
    "SI_REQUEST": "New SI Request",
    "INVOICE_QUERY": "Invoice Query",
    "GENERAL": "General Message",
    "SPAM": "Spam",
}


st.set_page_config(
    page_title="Shipping Document Verification",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"<style>{(PROJECT_ROOT / 'styles.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def uploaded_attachment(uploaded_file: Any) -> UploadedAttachment | None:
    if uploaded_file is None:
        return None
    return UploadedAttachment(uploaded_file.name, uploaded_file.getvalue())


def load_email_json(uploaded_file: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(uploaded_file.getvalue().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Email file must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Email JSON must contain one object")
    return parsed


def section_label(text: str) -> None:
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


def render_status_history(detail: dict[str, Any]) -> None:
    history = detail.get("status_history", [])

    # Create a default lifecycle when backend history is unavailable
    if not history:
        final_status = detail.get("task_status", "Completed")

        if final_status == "Failed":
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Failed"},
            ]
        elif final_status == "Review":
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Review"},
            ]
        else:
            history = [
                {"status": "Received"},
                {"status": "Processing"},
                {"status": "Completed"},
            ]

    parts = []

    for index, item in enumerate(history):
        status = item.get("status", "Processing")

        css = "done"
        if status == "Review":
            css = "review"
        elif status == "Failed":
            css = "failed"

        parts.append(
            f'<span class="status-step {css}">{status}</span>'
        )

        if index < len(history) - 1:
            parts.append('<span class="status-arrow">→</span>')

    st.markdown(
        f'<div class="status-track">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def render_confidence(detail: dict[str, Any]) -> None:
    classification = float(detail.get("classification_confidence", 0))
    threshold = float(detail.get("confidence_threshold", .75))
    label = "High confidence" if classification >= .9 else "Moderate confidence" if classification >= threshold else "Human review"
    section_label("Confidence & uncertainty")
    left, right = st.columns([1, 2])
    left.metric("Classification confidence", f"{classification:.0%}")
    right.markdown(
        f'<div class="confidence-wrap"><b>{label}</b><br><span style="color:#60778a">{detail.get("classification_rule", "No rule recorded")}</span><br><small>Human review threshold: {threshold:.0%}</small></div>',
        unsafe_allow_html=True,
    )


def render_evidence(detail: dict[str, Any]) -> None:
    evidence = detail.get("evidence", {})
    extracted = detail.get("extracted", {})
    if not evidence:
        return
    section_label("Source evidence")
    st.caption("Every extracted value remains traceable to its source document and line.")
    for row in comparison_rows(detail):
        field_key = next((key for key, label in FIELD_LABELS.items() if label == row["Field"]), None)
        if field_key is None:
            continue
        with st.expander(f'{row["Field"]}  ·  {row["Status"]}  ·  {row["Confidence"]}'):
            columns = st.columns(2)
            for column, role, label in ((columns[0], "si", "Shipping Instruction"), (columns[1], "bl", "Draft Bill of Lading")):
                item = evidence.get(role, {}).get(field_key, {})
                value = extracted.get(role, {}).get(field_key, "—")
                location = (f'Page {item["page"]} · Line ' if item.get("page") else 'Paragraph/table row ') + str(item.get("line_start", "—"))
                if item.get("line_end") != item.get("line_start"):
                    location += f'–{item.get("line_end")}'
                column.markdown(
                    f'<div class="evidence-card"><b>{label}</b><div class="evidence-meta">{html.escape(str(item.get("source", "Unknown source")))} · {location} · {item.get("confidence", 0):.0%}</div><div class="evidence-value">{html.escape(str(value))}</div><div class="evidence-quote">{html.escape(str(item.get("excerpt", "No excerpt available")))}</div></div>',
                    unsafe_allow_html=True,
                )


def render_document_types(detail: dict[str, Any]) -> None:
    """Show what each attachment was detected as, judged by content and not by filename."""
    analysis = detail.get("document_analysis") or {}
    detections = analysis.get("detections") or {}
    if not detections:
        return
    superseded = set(analysis.get("superseded") or [])
    rows = []
    for path, det in detections.items():
        if path in superseded:
            note = "Older revision: a newer " + str(det.get("type", "")) + " in this email was used instead"
        elif det.get("reason") == "no_text":
            note = "No readable text (corrupt file or scanned image)"
        elif det.get("reason") == "conflicting_titles":
            note = "Conflicting titles: " + ", ".join(det.get("conflicts") or [])
        elif det.get("reason") == "no_title_found":
            note = "No document title found"
        elif det.get("type") not in ("SI", "BL"):
            note = "Not a Shipping Instruction or Bill of Lading"
        elif det.get("filename_hint") not in (None, det["type"]):
            note = f"Filename suggests {det['filename_hint']}, content says {det['type']}"
        else:
            note = "As expected"
        rows.append(
            {
                "File": Path(path).name,
                "Detected as": det.get("label") or "Unknown",
                "Confidence": f"{det.get('confidence', 0):.0%}" if det.get("confidence") else "—",
                "Note": note,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    if analysis.get("swapped"):
        st.info("The SI and BL were in the wrong places. Roles were assigned from the document titles, not the filenames.")


def version_badge(detail: dict[str, Any]) -> str:
    """One-line repeat/version status for the inbox list."""
    info = detail.get("version_info") or {}
    if info.get("duplicate_of"):
        return f"Repeat of {info['duplicate_of']}"
    if info.get("duplicates"):
        return f"Received {len(info['duplicates']) + 1} times"
    if info.get("version_count", 0) > 1:
        if info.get("is_latest"):
            return f"Latest of {info['version_count']} versions"
        return f"Version {info['version']} of {info['version_count']} · superseded by {info['superseded_by']}"
    return ""


def render_version_info(detail: dict[str, Any]) -> None:
    """Explain repeats, the shipment's version chain and what changed between versions."""
    info = detail.get("version_info") or {}
    if not info:
        return
    section_label("Repeats and versions")
    if info.get("duplicate_of"):
        st.warning(f"Repeated email: identical to {info['duplicate_of']} (same subject, message and attachments).")
    elif info.get("duplicates"):
        st.info(f"This email was received {len(info['duplicates']) + 1} times. Repeats: {', '.join(info['duplicates'])}.")
    if not info.get("version"):
        if not info.get("duplicate_of"):
            st.caption("No shipment identifier could be read from this email's documents, so it is not tracked for versions.")
        return
    source = {"booking_no": "Booking No.", "oc_no": "OC No.", "si_fingerprint": "the SI's seven fields"}.get(
        info.get("key_source"), str(info.get("key_source"))
    )
    st.caption(f"Shipment identified by {source}: {str(info['shipment_key']).split(':', 1)[1]}")
    count = info["version_count"]
    if count == 1:
        st.caption("Only one version has been received for this shipment.")
        return
    if info.get("is_latest"):
        st.success(f"Version {info['version']} of {count}: this is the latest version.")
    else:
        st.warning(
            f"Version {info['version']} of {count}: superseded by {info['superseded_by']}. "
            f"The latest version is {info['latest_email_id']}."
        )
    st.caption("Chain (oldest to newest): " + " → ".join(info["chain"]))
    st.caption(
        {
            "arrival_date": "Ordered by email date.",
            "arrival_order": "Ordered by arrival (these emails carry no date).",
            "revision_marker": "Ordered by the revision number printed in the document.",
        }.get(info.get("order_basis"), "")
    )
    if info.get("order_conflict"):
        st.warning("Printed revision numbers disagree with arrival order; the revision numbers were used.")
    if info.get("supersedes"):
        changes = info.get("changes_from_previous")
        if changes is None:
            st.caption(f"Field changes since {info['supersedes']} could not be compared (a document was unreadable or incomplete).")
        elif not changes:
            st.caption(f"No field changes since {info['supersedes']}: it was resent unchanged.")
        else:
            st.markdown(f"**Changes since {info['supersedes']}**")
            st.dataframe(
                [{"Document": c["document"], "Field": c["label"], "Previous": c["previous"], "Current": c["current"]}
                 for c in changes],
                hide_index=True,
                width="stretch",
            )


def render_version_summary(artifacts: ProcessingArtifacts) -> None:
    stats = version_summary(artifacts.internal_results)
    section_label("Repeats and versions")
    cols = st.columns(4)
    items = (
        ("Repeated emails", stats["repeated_emails"], f"Exact repeats (same subject, message and attachments), in {stats['repeat_groups']} groups."),
        ("Shipments tracked", stats["shipments_tracked"], "Document-check emails linked to a shipment by Booking No. or OC No."),
        ("With revisions", stats["shipments_with_revisions"], "Shipments that have more than one version."),
        ("Superseded", stats["superseded_documents"], "Older versions replaced by a newer one."),
    )
    for col, (label, value, help_text) in zip(cols, items):
        col.metric(label, f"{value:,}", help=help_text)
    repeats = [
        (eid, detail["version_info"]["duplicate_of"])
        for eid, detail in artifacts.internal_results.items()
        if (detail.get("version_info") or {}).get("duplicate_of")
    ]
    if repeats:
        subject_of = {str(e.get("email_id")): str(e.get("subject", "")) for e in artifacts.emails}
        with st.expander(f"View the {len(repeats)} repeated emails"):
            st.dataframe(
                [
                    {
                        "Email": eid,
                        "Repeat of": original,
                        "Category": CATEGORY_LABELS.get(artifacts.submission[eid]["category"], artifacts.submission[eid]["category"]),
                        "Subject": subject_of.get(eid, ""),
                    }
                    for eid, original in repeats
                ],
                hide_index=True,
                width="stretch",
            )


def render_single_result(artifacts: ProcessingArtifacts) -> None:
    email = artifacts.emails[0]
    email_id = email["email_id"]
    result = artifacts.submission[email_id]
    detail = artifacts.internal_results[email_id]
    st.subheader(email.get("subject", "Untitled request"))
    st.caption(f"{email_id} · {email.get('from', 'Unknown sender')}")
    if result["category"] != "BL_COMPARISON":
        st.info(f"Classified: {CATEGORY_LABELS[result['category']]}. No document comparison required.")
    elif result["status"] == "MISMATCH":
        st.error(f"{len(detail.get('mismatches', []))} field differences · Request a revised draft BL.")
    elif result["status"] == "NEEDS_REVIEW":
        st.warning(detail.get("detail") or "Manual verification required.")
    else:
        st.success("No mismatch detected across the seven shipment fields.")
    comparison, source, analysis, history = st.tabs(["Comparison", "Documents & evidence", "AI cross-check", "Activity"])
    with comparison:
        if result["category"] == "BL_COMPARISON":
            st.caption("Shipping Instruction is the reference. Compare the original values side by side.")
            st.dataframe(comparison_rows(detail), hide_index=True, width="stretch")
        else:
            st.write(email.get("body", ""))
    with source:
        render_document_types(detail)
        render_evidence(detail)
        with st.expander("Original email"):
            st.write(email.get("body", ""))
    with analysis:
        render_ai_panel(detail)
    with history:
        render_status_history(detail)
        render_version_info(detail)
        render_error_history(detail) if detail.get("error_history") else None

def render_ai_panel(detail: dict[str, Any]) -> None:
    ai = normalize_ai_result(detail.get("ai_analysis"))
    with st.expander("Analysis activity · Rules + Gemini", expanded=True):
        cols = st.columns(3)
        cols[0].caption("01 · EMAIL TRIAGE")
        cols[0].write(CATEGORY_LABELS.get(detail.get("category"), "Pending"))
        cols[1].caption("02 · FIELD COMPARISON")
        cols[1].write(detail.get("status", "Pending"))
        cols[2].caption("03 · GEMINI SECOND OPINION")
        cols[2].write(str(ai.get("status", "skipped")).replace("_", " ").title())
        if ai.get("status") == "completed":
            result = ai.get("result", {})
            st.info(result.get("summary", "Analysis completed"))
            for observation in result.get("observations", []):
                st.write(f"• {observation}")
        elif ai.get("status") in {"unavailable", "quota_exhausted"}:
            from ai_service import failure_result
            error = ai.get("error", "")
            if "429" in error or "RESOURCE_EXHAUSTED" in error:
                error = failure_result(RuntimeError(error))["error"]
            st.warning(error or "Gemini is unavailable. The field comparison remains available.")
            st.caption("AI quota is separate from document verification. Retrying does not increase the provider quota.")
            if st.button("Retry AI cross-check only", key=f"retry_ai_{detail.get('email_id', 'single')}"):
                from src.pipeline import _ai_cross_check
                with st.spinner("Checking Gemini availability..."):
                    detail["ai_analysis"] = _ai_cross_check(detail.get("extracted", {}))
                st.rerun()
        else:
            st.caption("No Gemini analysis was performed for this result. Classification and field comparison use deterministic rules.")
        if detail.get("human_review"):
            st.caption("Any Gemini result above predates the human review. The reviewed comparison is the current result.")


def render_mail_demo() -> None:
    connected = st.session_state.get("demo_mail_connected", False)
    st.markdown(f'<div class="mail-connection"><strong>{"● Demo mailbox connected" if connected else "○ Demo mailbox disconnected"}</strong><span>Simulation · no live email account</span></div>', unsafe_allow_html=True)
    with st.popover("Mailbox controls & received requests"):
        st.toggle("Connect demo mailbox", key="demo_mail_connected")
        st.caption("Receive one bundled sample email on demand. This does not poll or send real mail.")
        auto = st.toggle("Verify automatically on simulated arrival", key="demo_mail_auto")
        if st.button("Simulate incoming email", disabled=not st.session_state.get("demo_mail_connected")):
            inbox = open_local_inbox(DEMO_BUNDLE)
            emails = inbox.emails()
            index = st.session_state.get("demo_mail_index", 0)
            email = emails[index % len(emails)]
            st.session_state.demo_mail_pending = email
            st.session_state.demo_mail_index = index + 1
            st.toast(f"New demo email: {email['subject']}")
        pending = st.session_state.get("demo_mail_pending")
        if pending:
            st.write(f"Received: {pending.get('subject', 'Untitled')}")
            run = st.button("Verify received request", disabled=not st.session_state.get("demo_mail_connected"))
            if st.session_state.get("demo_mail_connected") and (auto or run):
                from src.pipeline import process_inbox
                from src.service import SingleEmailInbox
                with st.status("Analyzing received request", expanded=True) as activity:
                    events = []
                    def stage(email_id, label):
                        events.append(label)
                        activity.write(label)
                    inbox = open_local_inbox(DEMO_BUNDLE)
                    submission, details, summary = process_inbox(SingleEmailInbox(inbox, pending), stage_callback=stage)
                    st.session_state.single_artifacts = ProcessingArtifacts([pending], submission, details, summary)
                    activity.update(label="Request processed", state="complete")
                st.session_state.pop("demo_mail_pending", None)
                st.session_state.active_page = "Check one case"
                st.rerun()


def save_results_to_firebase(artifacts: ProcessingArtifacts) -> dict[str, Any]:
    """Store verification results in Firebase Firestore."""
    if db is None:
        return {"saved": 0, **firebase_status()}

    try:
        saved = 0
        for email_id, result in artifacts.submission.items():
            detail = artifacts.internal_results.get(email_id, {})

            record = {
                "email_id": email_id,
                "status": result.get("status", ""),
                "category": result.get("category", ""),
                "subject": next(
                    (
                        email.get("subject", "")
                        for email in artifacts.emails
                        if email.get("email_id") == email_id
                    ),
                    "",
                ),
                "mismatches": detail.get("mismatches", []),
                "classification_confidence": detail.get(
                    "classification_confidence", 0
                ),
            }

            # Stable IDs make reruns idempotent instead of creating duplicates.
            db.collection("verification_results").document(email_id).set(record, merge=True)
            saved += 1

        return {"saved": saved, **firebase_status()}

    except Exception as exc:
        print(f"Firebase save failed: {exc}")
        return {"saved": 0, "configured": True, "connected": False, "error": str(exc)}


def run_dataset_with_progress(bundle_path: str) -> ProcessingArtifacts:
    progress = st.progress(0.0)
    status_text = st.empty()
    live_stage = st.status("Opening inbox...", expanded=True)

    def update(completed: int, total: int, email_id: str) -> None:
        ratio = completed / total if total else 1.0
        progress.progress(min(ratio, 1.0))
        if completed < total:
            status_text.caption(f"Processing {email_id} · {completed}/{total} completed")
        else:
            status_text.caption(f"Completed {completed}/{total} emails")

    def update_stage(email_id: str, stage: str) -> None:
        live_stage.update(label=f"{stage} · {email_id}", state="running")
        status_text.caption(f"{email_id} · {stage}")

    try:
        artifacts = process_dataset(bundle_path, update, update_stage)
    except Exception:
        live_stage.update(label="Inbox processing failed", state="error", expanded=True)
        raise
    live_stage.update(label="Inbox analysis completed", state="complete", expanded=False)

    # Save cloud audit records
    cloud_result = save_results_to_firebase(artifacts)
    if cloud_result.get("saved"):
        st.toast(f"Saved {cloud_result['saved']} audit records to Firestore")
    elif cloud_result.get("error"):
        st.warning(f"Local processing completed, but Firebase was unavailable: {cloud_result['error']}")

    return artifacts


def render_summary(artifacts: ProcessingArtifacts) -> None:
    records = normalized_inbox_records(
        artifacts.emails, artifacts.submission, artifacts.internal_results
    )
    stats = useDashboardStats(records)
    cards = (
        ("Total emails processed", f'{stats["total_emails"]:,}', "Inbox records"),
        ("Comparison cases", f'{stats["comparison_cases"]:,}', "SI and BL checks"),
        ("Mismatch count", f'{stats["mismatch_count"]:,}', "Fields requiring correction"),
        ("Human review", f'{stats["human_review_count"]:,}', "Low confidence or incomplete"),
        ("Failed cases", f'{stats["failed_cases"]:,}', "Processing or attachment errors"),
        ("Success rate", f'{stats["success_rate"]:.0%}', "Automatically resolved comparisons"),
    )
    columns = st.columns(3)
    for index, (label, value, caption) in enumerate(cards):
        with columns[index % 3]:
            st.markdown(
                f'<div class="dashboard-card"><div class="dashboard-label">{label}</div><div class="dashboard-number">{value}</div><div style="opacity:.65">{caption}</div></div>',
                unsafe_allow_html=True,
            )


def render_dataset_table(artifacts: ProcessingArtifacts) -> None:
    rows = dataset_rows(artifacts)
    st.subheader("Results")
    left, right = st.columns(2)
    categories = sorted({row["category"] for row in rows})
    selected_categories = left.multiselect("Category", categories, default=categories)
    outcome = right.selectbox(
        "Outcome",
        ["All", "Mismatch", "No Mismatch", "Human Review"],
    )

    filtered = [row for row in rows if row["category"] in selected_categories]
    if outcome == "Mismatch":
        filtered = [row for row in filtered if row["comparison status"] == "MISMATCH"]
    elif outcome == "No Mismatch":
        filtered = [
            row
            for row in filtered
            if row["category"] == "BL_COMPARISON" and row["comparison status"] == "OK"
        ]
    elif outcome == "Human Review":
        filtered = [row for row in filtered if row["comparison status"] == "NEEDS_REVIEW"]
    st.caption(f"Showing {len(filtered)} of {len(rows)} emails")
    st.dataframe(filtered, hide_index=True, width="stretch", height=520)


def single_case_artifacts(artifacts: ProcessingArtifacts, email_id: str) -> ProcessingArtifacts:
    email = next(email for email in artifacts.emails if email["email_id"] == email_id)
    return ProcessingArtifacts(
        [email],
        {email_id: artifacts.submission[email_id]},
        {email_id: artifacts.internal_results[email_id]},
        artifacts.summary,
    )


def render_action_center(artifacts: ProcessingArtifacts) -> None:
    mismatches = [email_id for email_id, row in artifacts.submission.items() if row["status"] == "MISMATCH"]
    reviews = [email_id for email_id, row in artifacts.submission.items() if row["status"] == "NEEDS_REVIEW"]
    cleared = [
        email_id
        for email_id, row in artifacts.submission.items()
        if row["category"] == "BL_COMPARISON" and row["status"] == "OK"
    ]
    email_by_id = {email["email_id"]: email for email in artifacts.emails}

    section_label("Work queue")
    st.markdown("### Start with the cases that need attention")
    st.caption("Mismatch cases should be corrected. Review cases need a person to inspect the source documents. Cleared cases require no action.")
    queue_data = (
        ("Mismatch — correct BL", len(mismatches), "Document values differ from the approved SI."),
        ("Human review", len(reviews), "Missing, unreadable, or uncertain information."),
        ("Cleared", len(cleared), "All seven fields agree; no action required."),
    )
    queue_html = "".join(
        f'<div class="queue-row"><div><div class="queue-row-title">{label}</div><div class="queue-row-copy">{copy}</div></div><div class="queue-row-count">{count}</div></div>'
        for label, count, copy in queue_data
    )
    st.markdown(f'<div class="queue-list">{queue_html}</div>', unsafe_allow_html=True)

    attention = mismatches + reviews
    if attention:
        section_label("Review a case")
        filter_col, case_col = st.columns([1, 2])
        queue_filter = filter_col.selectbox("Show", ["All action required", "Mismatch only", "Human review only"])
        choices = attention
        if queue_filter == "Mismatch only":
            choices = mismatches
        elif queue_filter == "Human review only":
            choices = reviews

        def case_label(email_id: str) -> str:
            row = artifacts.submission[email_id]
            subject = str(email_by_id[email_id].get("subject", "Untitled"))
            action = "Correct BL" if row["status"] == "MISMATCH" else "Review"
            return f"{action} · {email_id} · {subject}"

        selected = case_col.selectbox("Choose a case", choices, format_func=case_label)
        with st.expander("Open selected case", expanded=True):
            render_single_result(single_case_artifacts(artifacts, selected))

    with st.expander("View all email records"):
        st.caption("This detailed table is for searching, filtering, and audit. Daily work should begin in the action queue above.")
        render_dataset_table(artifacts)


def render_inbox_workspace(artifacts: ProcessingArtifacts) -> None:
    st.subheader("Work queue")
    email_by_id = {email["email_id"]: email for email in artifacts.emails}
    left, right = st.columns([2, 1])
    search = left.text_input("Search requests", placeholder="Email ID or subject", key="queue_search")
    bucket = right.selectbox("Show", ["Needs attention", "All requests", "Mismatches", "Human review", "Passed", "Classification only"], key="queue_filter")
    matches = []
    for eid, result in artifacts.submission.items():
        category, status = result["category"], result["status"]
        if search.casefold() not in (eid + " " + email_by_id[eid].get("subject", "")).casefold():
            continue
        if bucket == "Needs attention" and status not in {"MISMATCH", "NEEDS_REVIEW"}: continue
        if bucket == "Mismatches" and status != "MISMATCH": continue
        if bucket == "Human review" and status != "NEEDS_REVIEW": continue
        if bucket == "Passed" and (status != "OK" or category != "BL_COMPARISON"): continue
        if bucket == "Classification only" and category == "BL_COMPARISON": continue
        matches.append(eid)
    st.caption(f"{len(matches)} requests match your filters")
    if not matches:
        st.info("No requests in this view. Change the filter to see other results.")
        return
    pages = max(1, (len(matches) + 19) // 20)
    page = st.selectbox("Queue page", range(1, pages + 1), format_func=lambda n: f"{n} / {pages}", key="queue_page")
    visible = matches[(page - 1) * 20:page * 20]
    st.dataframe([{"Request": eid, "Subject": email_by_id[eid].get("subject", ""),
                   "Category": CATEGORY_LABELS[artifacts.submission[eid]["category"]],
                   "Outcome": artifacts.submission[eid]["status"] if artifacts.submission[eid]["category"] == "BL_COMPARISON" else "Classified"}
                  for eid in visible], hide_index=True, width="stretch")
    selected = st.selectbox("Open request", visible, format_func=lambda eid: f"{eid} · {email_by_id[eid].get('subject', '')}", key="queue_open")
    with st.container(border=True):
        render_single_result(single_case_artifacts(artifacts, selected))
        if artifacts.submission[selected]["status"] == "NEEDS_REVIEW":
            st.button("Review this request", key="queue_review",
                      on_click=lambda: st.session_state.update(active_page="Human review", human_review_case=selected))


def render_error_history(detail: dict[str, Any]) -> None:
    errors = detail.get("error_history", [])
    if not errors:
        st.info("No processing errors were recorded for this case.")
        return
    for index, item in enumerate(reversed(errors), start=1):
        with st.expander(
            f"Error {len(errors) - index + 1} · {item.get('type', 'Error')} · {item.get('at', 'Unknown time')}",
            expanded=index == 1,
        ):
            st.code(item.get("message", "Unknown processing error"), language=None)
            if item.get("traceback"):
                st.code(item["traceback"], language="text")


def render_human_review_dashboard(
    artifacts: ProcessingArtifacts, dataset_bundle: str
) -> None:
    email_by_id = {email["email_id"]: email for email in artifacts.emails}
    failed = [
        email_id
        for email_id, detail in artifacts.internal_results.items()
        if detail.get("processing_status", detail.get("task_status")) == "Failed"
    ]
    pending = [
        email_id
        for email_id, result in artifacts.submission.items()
        if result["status"] == "NEEDS_REVIEW" and email_id not in failed
    ]
    resolved = [
        email_id
        for email_id, detail in artifacts.internal_results.items()
        if detail.get("review_state") in {"confirmed", "corrected"}
    ]

    if st.session_state.pop("review_notice", None):
        st.success("Human review saved and result recalculated.")
    if st.session_state.pop("retry_notice", None):
        st.success("Retry completed. The case status and error history were updated.")

    metrics = st.columns(3)
    metrics[0].metric("Pending review", len(pending))
    metrics[1].metric("Failed processing", len(failed))
    metrics[2].metric("Reviewed", len(resolved))

    review_tab, failed_tab, resolved_tab = st.tabs(
        ["Manual verification", "Processing errors", "Completed reviews"]
    )

    with review_tab:
        if not pending:
            st.success("No cases are waiting for manual verification.")
        else:
            selected = st.selectbox(
                "Choose a case",
                pending,
                format_func=lambda email_id: (
                    f"{email_id} · {email_by_id[email_id].get('subject', 'Untitled')}"
                ),
                key="human_review_case",
            )
            result = artifacts.submission[selected]
            detail = artifacts.internal_results[selected]
            email = email_by_id[selected]
            st.caption(
                f"Reason: {result.get('review_reason') or detail.get('internal_reason') or 'Verification required'}"
            )
            st.write(email.get("body", ""))
            st.dataframe(comparison_rows(detail), hide_index=True, width="stretch")
            st.markdown("### Confirm or correct extracted values")
            st.caption(
                "SI is the reference. Enter all seven SI and BL values, then save the review. "
                "The comparison result will be recalculated without changing the official schema."
            )
            extracted = detail.get("extracted", {})
            with st.form(f"review_form_{selected}"):
                header = st.columns([1.2, 2, 2])
                header[0].markdown("**Field**")
                header[1].markdown("**SI value**")
                header[2].markdown("**BL value**")
                si_values: dict[str, str] = {}
                bl_values: dict[str, str] = {}
                for field in FIELDS:
                    columns = st.columns([1.2, 2, 2])
                    columns[0].write(FIELD_LABELS[field])
                    si_values[field] = columns[1].text_input(
                        f"SI {FIELD_LABELS[field]}",
                        value=str(extracted.get("si", {}).get(field, "") or ""),
                        label_visibility="collapsed",
                        key=f"review_{selected}_si_{field}",
                    )
                    bl_values[field] = columns[2].text_input(
                        f"BL {FIELD_LABELS[field]}",
                        value=str(extracted.get("bl", {}).get(field, "") or ""),
                        label_visibility="collapsed",
                        key=f"review_{selected}_bl_{field}",
                    )
                note = st.text_area(
                    "Reviewer note",
                    placeholder="Optional note explaining the confirmation or correction",
                    key=f"review_note_{selected}",
                )
                save_review = st.form_submit_button(
                    "Save review and recalculate", type="primary", width="stretch"
                )
            if save_review:
                try:
                    updated = apply_human_review(
                        artifacts, selected, si_values, bl_values, note
                    )
                    st.session_state.dataset_artifacts = updated
                    st.session_state.submission_ready = False
                    st.session_state.pop("export_files", None)
                    write_artifacts(updated, DEFAULT_OUTPUT)
                    st.session_state.review_notice = True
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with failed_tab:
        if not failed:
            st.success("No failed processing cases.")
        else:
            selected_failed = st.selectbox(
                "Choose a failed case",
                failed,
                format_func=lambda email_id: (
                    f"{email_id} · {email_by_id[email_id].get('subject', 'Untitled')}"
                ),
                key="failed_review_case",
            )
            failed_detail = artifacts.internal_results[selected_failed]
            st.caption(
                f"Attempts: {failed_detail.get('processing_attempts', 1)} · "
                f"Retries: {failed_detail.get('retry_count', 0)}"
            )
            render_error_history(failed_detail)
            if st.button(
                "Retry failed processing",
                type="primary",
                width="stretch",
                key=f"retry_{selected_failed}",
            ):
                try:
                    updated = retry_failed_email(
                        dataset_bundle, artifacts, selected_failed
                    )
                    st.session_state.dataset_artifacts = updated
                    st.session_state.submission_ready = False
                    st.session_state.pop("export_files", None)
                    write_artifacts(updated, DEFAULT_OUTPUT)
                    st.session_state.retry_notice = True
                    st.rerun()
                except (OSError, KeyError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))

    with resolved_tab:
        if not resolved:
            st.info("No human review decisions have been saved yet.")
        else:
            rows = []
            for email_id in resolved:
                review = artifacts.internal_results[email_id]["human_review"]
                rows.append(
                    {
                        "email_id": email_id,
                        "subject": email_by_id[email_id].get("subject", ""),
                        "decision": review.get("decision", ""),
                        "reviewed_at": review.get("reviewed_at", ""),
                        "result": artifacts.submission[email_id]["status"],
                        "note": review.get("note", ""),
                    }
                )
            st.dataframe(rows, hide_index=True, width="stretch")
            reply_id = st.selectbox("Prepare reply for reviewed request", resolved, key="reply_case")
            draft = build_reply(email_by_id[reply_id], artifacts.internal_results[reply_id])
            st.markdown("### Reply draft · not sent")
            st.caption("Prepared from the saved review facts. Review and copy it into your mail client; no email is sent here.")
            st.text_input("To", value=draft["to"], disabled=True, key=f"reply_to_{reply_id}")
            st.text_input("Subject", value=draft["subject"], key=f"reply_subject_{reply_id}")
            body = st.text_area("Reply message", value=draft["body"], height=320,
                                key=f"reply_body_{reply_id}_{artifacts.internal_results[reply_id]['human_review']['reviewed_at']}")
            st.download_button("Download reply draft", data=body, file_name=f"reply-{reply_id}.txt", mime="text/plain")


with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand"><div class="brand-mark">C</div><div><div class="brand-name">CargoCheck</div><div class="brand-sub">Shipping operations</div></div></div>',
        unsafe_allow_html=True,
    )
    if "active_page" not in st.session_state:
        st.session_state.active_page = "Home"
    nav_groups = {
        "Workspace": [("Home", "Overview")],
        "01  Verification": [("Analyze inbox", "Inbox & work queue"), ("Check one case", "Single request")],
        "02  Resolution": [("Human review", "Review & resolution")],
        "03  Reporting": [("Export results", "Reports & submission")],
    }
    for group, children in nav_groups.items():
        st.markdown(f'<div class="nav-parent">{group}</div>', unsafe_allow_html=True)
        for destination, label in children:
            st.button(label, key=f"nav_{destination}",
                      type="primary" if st.session_state.active_page == destination else "secondary",
                      width="stretch", on_click=lambda target=destination: st.session_state.update(active_page=target))
    st.divider()
    active_artifacts = st.session_state.get("dataset_artifacts")
    st.caption(f"{len(active_artifacts.emails)} emails in workspace" if active_artifacts else "No inbox loaded yet")
    st.caption("SI is the reference · 7 fields checked")
    with st.expander("Demo mailbox", expanded=False):
        render_mail_demo()
    page = st.session_state.active_page

page_titles = {
    "Home": ("Operations Workspace", "Monitor document checks and start your next task."),
    "Check one case": ("Verify One Request", "Check an email, Shipping Instruction and draft Bill of Lading."),
    "Analyze inbox": ("Inbox Operations", "Turn incoming shipping emails into a prioritized action queue."),
    "Human review": ("Human Review", "Confirm uncertain values, correct extraction results, and retry failed cases."),
    "Export results": ("Submission Center", "Validate and download the final verification results."),
}
banner_title, banner_copy = page_titles[page]
parent = next(group for group, children in nav_groups.items() if any(key == page for key, _ in children))
st.markdown(
    f'<header class="workspace-header"><div class="breadcrumb">CargoCheck / {parent} / {banner_title}</div><h1>{banner_title}</h1><p>{banner_copy}</p></header>',
    unsafe_allow_html=True,
)
bundle_path = str(DEFAULT_BUNDLE)


def go_to(destination: str) -> None:
    st.session_state.active_page = destination


def reset_single_case() -> None:
    for key in ("single_artifacts", "single_email", "single_si", "single_bl"):
        st.session_state.pop(key, None)


if page == "Home":
    st.markdown(
        """
        <div class="page-intro">
            <div class="eyebrow">Dashboard</div>
            <div class="page-title">Shipping verification workspace</div>
            <div class="page-copy">Review document exceptions, verify shipment records, and resolve issues.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "dataset_artifacts" in st.session_state:
        summary = st.session_state.dataset_artifacts.summary
        mismatch = summary.get("mismatch",0)
        review = summary.get("manual_review",0)
        passed = summary.get("no_mismatch",0)
        emails = summary.get("emails_processed",0)
        checks = summary.get("comparison_requests",0)
    else:
        mismatch = review = passed = emails = checks = 0

    st.markdown("### Cases requiring attention")
    cols=st.columns(3)
    cards=[("Mismatch",mismatch,"Correct BL values"),("Human review",review,"Needs verification"),("Completed",passed,"No action required")]
    for c,(t,n,d) in zip(cols,cards):
        with c:
            st.markdown(f"""<div class="alert-card"><div class="dashboard-label">{t}</div><div class="dashboard-number">{n}</div><div>{d}</div></div>""",unsafe_allow_html=True)

    st.markdown("### Quick actions")
    a,b=st.columns(2)
    with a: st.button("Verify one request",type="primary",width="stretch",on_click=go_to,args=("Check one case",))
    with b: st.button("Review inbox",width="stretch",on_click=go_to,args=("Analyze inbox",))

    st.markdown("### Current workspace")
    c=st.columns(3)
    for col,(l,v) in zip(c,[("Emails processed",emails),("Document checks",checks),("Passed automatically",passed)]):
        with col: st.metric(l,v)


elif page == "Check one case":
    if "single_artifacts" not in st.session_state:
        st.markdown(
            '<div class="page-intro"><div class="page-title">Check one shipping request</div><div class="page-copy">Add the three files below. After verification, this form will be replaced by a clear analysis result.</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">1</div><div class="step-title">Add the email request</div></div><div class="step-help">Choose the email JSON from the participant dataset.</div></div>', unsafe_allow_html=True)
        email: dict[str, Any] | None = None
        email_file = st.file_uploader("Choose email JSON", type=["json"], key="single_email", help="This file contains the sender, subject, message, and email ID.")
        if email_file is not None:
            try:
                email = load_email_json(email_file)
                st.success(f"Email loaded: {email.get('subject', 'Untitled email')}")
            except ValueError as exc:
                st.error(str(exc))

        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">2</div><div class="step-title">Add the two shipping documents</div></div><div class="step-help">SI is the approved instruction. BL is the carrier draft that must be checked against it.</div></div>', unsafe_allow_html=True)
        upload_cols = st.columns(2)
        st.caption("Supported: TXT, text-based PDF and Word (.docx). Scans need manual review; old .doc and spreadsheets are not supported.")
        si_file = upload_cols[0].file_uploader("Shipping Instruction (SI)", type=["txt", "pdf", "docx"], key="single_si", help="The reference document containing the intended shipment details.")
        bl_file = upload_cols[1].file_uploader("Draft Bill of Lading (BL)", type=["txt", "pdf", "docx"], key="single_bl", help="The draft document CargoCheck will verify against the SI.")

        st.markdown('<div class="step-card"><div class="step-head"><div class="step-badge">3</div><div class="step-title">Run the verification</div></div><div class="step-help">You will receive a field-by-field result, confidence level, and source evidence.</div></div>', unsafe_allow_html=True)
        if st.button("Verify documents", type="primary", width="stretch", key="process_single"):
            if email is None:
                st.error("Complete step 1 by uploading an email JSON.")
            elif not str(email.get("subject", "")).strip():
                st.error("Enter an email subject so CargoCheck can classify the request.")
            else:
                try:
                    with st.status("Analyzing request · Rules + Gemini", expanded=True) as activity:
                        st.session_state.single_artifacts = process_single_email(email, uploaded_attachment(si_file), uploaded_attachment(bl_file), stage_callback=lambda eid, label: activity.write(label))
                        activity.update(label="Analysis complete", state="complete")
                    st.rerun()
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
    else:
        st.markdown('<div class="result-mode-head"><div><div class="eyebrow">Analysis complete</div><div class="result-mode-title">Verification result</div><div class="result-mode-copy">The upload form is hidden while you review this result.</div></div></div>', unsafe_allow_html=True)
        render_single_result(st.session_state.single_artifacts)
        st.button("Analyze another case", type="primary", width="stretch", on_click=reset_single_case, key="reset_single")

elif page == "Analyze inbox":
    st.markdown('<div class="page-intro"><div class="page-title">Inbox operations</div><div class="page-copy">Turn a mixed inbox into a prioritized work queue. CargoCheck separates routine messages, finds document discrepancies, and brings uncertain cases to the top.</div></div>', unsafe_allow_html=True)
    if "dataset_artifacts" not in st.session_state:
        st.markdown('<div class="journey"><div class="journey-step"><div class="journey-dot">1</div><div class="journey-title">Sort the inbox</div><div class="journey-copy">CargoCheck identifies which emails actually need document checking.</div></div><div class="journey-step"><div class="journey-dot">2</div><div class="journey-title">Check SI against BL</div><div class="journey-copy">The seven shipment fields are compared only for the relevant requests.</div></div><div class="journey-step"><div class="journey-dot">3</div><div class="journey-title">See your next tasks</div><div class="journey-copy">Corrections and uncertain cases appear first in one simple queue.</div></div></div>', unsafe_allow_html=True)
        st.write("")
    data_sources = {"Participant dataset": str(DEFAULT_BUNDLE)}
    if (DEMO_BUNDLE / "inbox").is_dir():
        data_sources["Version-tracking demo (10 emails)"] = str(DEMO_BUNDLE)
    if len(data_sources) > 1:
        bundle_path = data_sources[st.selectbox("Data source", list(data_sources), key="data_source")]
    if st.button("Analyze inbox and build work queue", type="primary", width="stretch", key="run_dataset"):
        try:
            artifacts = run_dataset_with_progress(bundle_path)
            st.session_state.dataset_artifacts = artifacts
            st.session_state.dataset_bundle = str(Path(bundle_path).expanduser().resolve())
            st.session_state.submission_ready = False
            st.session_state.pop("export_files", None)
            write_artifacts(artifacts, DEFAULT_OUTPUT)
            st.success("Inbox analysis completed successfully.")
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if "dataset_artifacts" in st.session_state:
        with st.expander("Batch overview & version tracking", expanded=False):
            render_summary(st.session_state.dataset_artifacts)
            render_version_summary(st.session_state.dataset_artifacts)
        render_inbox_workspace(st.session_state.dataset_artifacts)

elif page == "Human review":
    st.markdown(
        '<div class="page-intro"><div class="page-title">Human review dashboard</div><div class="page-copy">Resolve uncertain cases by confirming or correcting all seven extracted fields. Processing failures keep their complete error history and can be retried individually.</div></div>',
        unsafe_allow_html=True,
    )
    artifacts = st.session_state.get("dataset_artifacts")
    if artifacts is None:
        st.warning(
            "No inbox results yet. Open **Inbox operations** and run the analysis first."
        )
        st.button("Go to inbox", type="primary", on_click=go_to, args=("Analyze inbox",))
    else:
        render_human_review_dashboard(
            artifacts,
            st.session_state.get("dataset_bundle", bundle_path),
        )

else:
    st.markdown('<div class="page-intro"><div class="page-title">Report and export</div><div class="page-copy">Review the comparison results, download operational reports in JSON, CSV, or PDF, and validate the official submission file.</div></div>', unsafe_allow_html=True)
    artifacts = st.session_state.get("dataset_artifacts")
    if artifacts is None:
        st.warning("No inbox results yet. Open **Analyze inbox** from the left menu and run the analysis first.")
        st.button("Go to inbox", type="primary", on_click=go_to, args=("Analyze inbox",))
    else:
        render_summary(artifacts)
        section_label("Comparison results")
        render_dataset_table(artifacts)

        section_label("Operational report exports")
        st.caption(
            "These reports include comparison values, processing errors, retries, and human review decisions."
        )
        if st.button(
            "Prepare JSON, CSV and PDF reports",
            type="primary",
            width="stretch",
            key="prepare_reports",
        ):
            try:
                with st.spinner("Building report files..."):
                    exports = {
                        "json": results_json_bytes(artifacts),
                        "csv": results_csv_bytes(artifacts),
                    }
                    try:
                        exports["pdf"] = results_pdf_bytes(artifacts)
                    except PdfExportUnavailable as exc:
                        st.warning(str(exc))
                    st.session_state.export_files = exports
                if "pdf" in exports:
                    st.success("JSON, CSV, and PDF reports are ready to download.")
                else:
                    st.success("JSON and CSV reports are ready to download.")
            except Exception as exc:
                st.session_state.pop("export_files", None)
                st.error(f"Report generation failed: {exc}")
        if st.session_state.get("export_files"):
            exports = st.session_state.export_files
            download_columns = st.columns(3 if "pdf" in exports else 2)
            download_columns[0].download_button(
                "Download results.json",
                data=exports["json"],
                file_name="verification-results.json",
                mime="application/json",
                width="stretch",
            )
            download_columns[1].download_button(
                "Download results.csv",
                data=exports["csv"],
                file_name="verification-results.csv",
                mime="text/csv",
                width="stretch",
            )
            if "pdf" in exports:
                download_columns[2].download_button(
                    "Download report.pdf",
                    data=exports["pdf"],
                    file_name="verification-report.pdf",
                    mime="application/pdf",
                    width="stretch",
                )

        section_label("Official submission")
        if st.button("Validate submission", type="primary", width="stretch", key="generate_submission"):
            try:
                resolved_bundle = str(Path(st.session_state.get("dataset_bundle", bundle_path)).expanduser().resolve())
                inbox_sample = json.loads((Path(resolved_bundle) / "sample_submission.json").read_text())
                validate_submission(artifacts.submission, inbox_sample)
                write_artifacts(artifacts, DEFAULT_OUTPUT)
                st.session_state.submission_ready = True
                st.success("Ready to submit — the schema is valid and every email is present.")
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                st.session_state.submission_ready = False
                st.error(str(exc))
        if st.session_state.get("submission_ready"):
            st.download_button("Download submission.json", data=submission_bytes(artifacts.submission), file_name="submission.json", mime="application/json", type="primary", width="stretch")
