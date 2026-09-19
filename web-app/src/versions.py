"""Duplicate-email and document-revision tracking across an inbox.

Three questions, answered without changing any comparison result:

1. Is this email a *repeat*? Same normalised subject, same message text and
   byte-identical attachments -> ``duplicate_of`` the first copy.
2. Which shipment does it belong to? The shipment key is the Booking No. (or,
   failing that, the OC No., or a fingerprint of the SI's seven fields).
   Subjects are never used to link emails: in the participant data the same
   "amend BL 041" text appears on four unrelated shipments.
3. Among emails for the same shipment, which is the *latest version*, and what
   changed since the previous one? Order comes from the email date when every
   email has one, otherwise arrival order; explicit revision numbers in the
   document title ("REV 2") override it when they disagree.

Everything here only *annotates* results (``detail["version_info"]``). The
submission that is scored is never touched.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
import unicodedata
from typing import Any, Iterable

from .doctype import is_marked_revised, revision_marker
from .extract import FIELDS, TEXT_READERS
from .normalize import normalize_field, normalize_text

FIELD_LABELS = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight (KG)",
}

COMPARISON = "BL_COMPARISON"

# --------------------------------------------------------------------------- #
# Normalising emails for duplicate detection
# --------------------------------------------------------------------------- #

_REPLY_PREFIX = re.compile(r"^\s*(?:re|fw|fwd)\s*[:_\-]\s*", re.IGNORECASE)
_EXTERNAL_BANNER = re.compile(
    r"^\s*WARNING:?\s*This email originated outside.*?(?:\n\s*\n|$)", re.IGNORECASE | re.DOTALL
)


def normalize_subject(subject: str) -> str:
    """Casefold and strip any stack of RE_/RE:/FW: prefixes."""
    text = unicodedata.normalize("NFKC", str(subject or ""))
    previous = None
    while previous != text:
        previous, text = text, _REPLY_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_body(body: str) -> str:
    """Drop the security banner, casefold and collapse whitespace."""
    text = unicodedata.normalize("NFKC", str(body or ""))
    text = _EXTERNAL_BANNER.sub("", text, count=1)
    return re.sub(r"\s+", " ", text).strip().casefold()


def email_fingerprint(record: dict[str, Any]) -> str:
    parts = [
        normalize_subject(record.get("subject", "")),
        normalize_body(record.get("body", "")),
        *sorted(att.get("sha256") or "unreadable" for att in record.get("attachments", [])),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Shipment identifiers
# --------------------------------------------------------------------------- #

_ID_LABELS = {
    "booking_no": r"booking\s*(?:no\.?|ref(?:erence)?\.?|number)",
    "oc_no": r"oc\s*no\.?",
    "bl_no": r"(?:b/l|bl|bill\s+of\s+lading)\s*(?:no\.?|number)",
}
# At least six characters and at least one digit, so labels never capture words.
_ID_VALUE = r"((?=[A-Z0-9\-/]*\d)[A-Z0-9][A-Z0-9\-/]{5,})"
_ID_PATTERNS = {
    name: re.compile(rf"(?<![A-Za-z]){label}\s*[:|]?\s*{_ID_VALUE}", re.IGNORECASE)
    for name, label in _ID_LABELS.items()
}


def extract_identifiers(text: str | None) -> dict[str, str]:
    """Find Booking / OC / B/L numbers in txt lines, spreadsheet rows or PDF layout text."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        for name, pattern in _ID_PATTERNS.items():
            match = pattern.search(line)
            if match and name not in found:
                found[name] = match.group(1).upper()
    return found


def _fields_fingerprint(fields: dict[str, str] | None) -> str | None:
    if not fields or any(not str(fields.get(f, "")).strip() for f in FIELDS):
        return None
    parts = [normalize_text(str(fields[f])) for f in FIELDS]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def shipment_key(
    identifiers: list[dict[str, str]], si_fields: dict[str, str] | None
) -> tuple[str | None, str | None]:
    """Return (key, source). Prefer Booking No., then OC No., then an SI fingerprint."""
    for name, source in (("booking_no", "booking_no"), ("oc_no", "oc_no")):
        values = [ids[name] for ids in identifiers if name in ids]
        if values:
            return f"{name.split('_')[0]}:{Counter(values).most_common(1)[0][0]}", source
    fingerprint = _fields_fingerprint(si_fields)
    if fingerprint:
        return f"fields:{fingerprint}", "si_fingerprint"
    return None, None


# --------------------------------------------------------------------------- #
# Collecting the raw material (the only part that reads attachments)
# --------------------------------------------------------------------------- #

def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def collect_records(inbox: Any, emails: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read each email's attachments once: content hash plus text (when a reader exists)."""
    records = []
    for index, email in enumerate(emails):
        attachments = []
        for path in email.get("attachments", []):
            entry: dict[str, Any] = {"path": path, "sha256": None, "text": None}
            try:
                raw = inbox.read_bytes(path)
            except Exception:  # an unreadable attachment is reported by the pipeline, not here
                raw = None
            if raw:
                entry["sha256"] = hashlib.sha256(raw).hexdigest()
                reader = TEXT_READERS.get(("." + path.rsplit(".", 1)[-1]).casefold())
                if reader:
                    try:
                        entry["text"] = reader(raw)
                    except Exception:
                        entry["text"] = None
            attachments.append(entry)
        records.append(
            {
                "email_id": str(email.get("email_id", "")),
                "index": index,
                "date": _parse_date(email.get("date") or email.get("Date")),
                "subject": email.get("subject", ""),
                "body": email.get("body", ""),
                "attachments": attachments,
            }
        )
    return records


# --------------------------------------------------------------------------- #
# Comparing versions of the same shipment
# --------------------------------------------------------------------------- #

def _same_value(field: str, a: str, b: str) -> bool:
    na, nb = normalize_field(field, a), normalize_field(field, b)
    if na is None or nb is None:  # e.g. a weight without a unit: fall back to text
        return normalize_text(str(a)) == normalize_text(str(b))
    return na == nb


def diff_versions(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> list[dict[str, str]] | None:
    """Field changes between two ``extracted`` dicts, or None if they are not comparable."""
    if not previous or not current:
        return None
    changes: list[dict[str, str]] = []
    comparable = False
    for role in ("si", "bl"):
        before, after = previous.get(role) or {}, current.get(role) or {}
        if not before or not after:
            continue
        comparable = True
        for field in FIELDS:
            if field in before and field in after and not _same_value(field, before[field], after[field]):
                changes.append(
                    {
                        "document": role.upper(),
                        "field": field,
                        "label": FIELD_LABELS[field],
                        "previous": str(before[field]),
                        "current": str(after[field]),
                    }
                )
    return changes if comparable else None


def _email_marker(record: dict[str, Any]) -> tuple[int | None, bool]:
    """Highest explicit revision number in an email's attachments, and whether any says REVISED."""
    numbers, revised = [], False
    for att in record["attachments"]:
        marker = revision_marker(att.get("text"), att["path"])
        if marker is not None:
            numbers.append(marker)
        revised = revised or is_marked_revised(att.get("text"), att["path"])
    return (max(numbers) if numbers else None), revised


def _order_chain(members: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, bool]:
    """Order oldest -> newest. Returns (ordered, basis, marker_disagrees_with_arrival)."""
    if all(m["date"] for m in members):
        arrival = sorted(members, key=lambda m: (m["date"], m["index"]))
        basis = "arrival_date"
    else:
        arrival = sorted(members, key=lambda m: m["index"])
        basis = "arrival_order"
    markers = [m["marker"] for m in members]
    if all(v is not None for v in markers) and len(set(markers)) == len(markers):
        by_marker = sorted(members, key=lambda m: m["marker"])
        return by_marker, "revision_marker", [m["email_id"] for m in by_marker] != [m["email_id"] for m in arrival]
    return arrival, basis, False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _blank_info() -> dict[str, Any]:
    return {
        "duplicate_of": None,
        "duplicates": [],
        "shipment_key": None,
        "key_source": None,
        "identifiers": {},
        "version": None,
        "version_count": 0,
        "chain": [],
        "is_latest": None,
        "latest_email_id": None,
        "supersedes": None,
        "superseded_by": None,
        "revision_marker": None,
        "marked_revised": False,
        "order_basis": None,
        "order_conflict": False,
        "changes_from_previous": None,
    }


def build_version_index(records: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pure function: records + pipeline results in, per-email version info out."""
    index = {r["email_id"]: _blank_info() for r in records}

    # 1. exact repeats (any category: spam and bot mail repeat too)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[email_fingerprint(record)].append(record)
    repeated_ids: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        first = min(members, key=lambda m: m["index"])
        index[first["email_id"]]["duplicates"] = [m["email_id"] for m in members if m is not first]
        for member in members:
            if member is not first:
                index[member["email_id"]]["duplicate_of"] = first["email_id"]
                repeated_ids.add(member["email_id"])

    # 2. shipment keys for document-check emails (repeats follow their first copy)
    chains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        eid = record["email_id"]
        result = results.get(eid, {})
        if result.get("category") != COMPARISON or eid in repeated_ids:
            continue
        identifiers = [extract_identifiers(att.get("text")) for att in record["attachments"]]
        si_fields = (result.get("extracted") or {}).get("si")
        key, source = shipment_key(identifiers, si_fields)
        if not key:
            continue
        marker, revised = _email_marker(record)
        info = index[eid]
        info.update(
            shipment_key=key,
            key_source=source,
            identifiers={k: v for ids in reversed(identifiers) for k, v in ids.items()},
            revision_marker=marker,
            marked_revised=revised,
        )
        chains[key].append({**record, "marker": marker})

    # 3. order each shipment's emails and diff consecutive versions
    for key, members in chains.items():
        ordered, basis, conflict = _order_chain(members)
        ids = [m["email_id"] for m in ordered]
        for position, member in enumerate(ordered):
            info = index[member["email_id"]]
            previous_id = ids[position - 1] if position else None
            info.update(
                version=position + 1,
                version_count=len(ids),
                chain=ids,
                is_latest=position == len(ids) - 1,
                latest_email_id=ids[-1],
                supersedes=previous_id,
                superseded_by=ids[position + 1] if position + 1 < len(ids) else None,
                order_basis=basis if len(ids) > 1 else None,
                order_conflict=conflict,
            )
        refresh_changes(results, index, ids)
    return index


def refresh_changes(results: dict[str, dict[str, Any]], index: dict[str, dict[str, Any]] | None = None,
                    chain_ids: list[str] | None = None) -> None:
    """Recompute ``changes_from_previous``; call again after a human corrects extracted values.

    With ``index`` (during build) writes into it; without, works on ``version_info``
    already stored in ``results``.
    """
    def info_of(eid: str) -> dict[str, Any] | None:
        return index[eid] if index is not None else results.get(eid, {}).get("version_info")

    if chain_ids is None:
        seen: dict[str, list[str]] = defaultdict(list)
        for eid, detail in results.items():
            info = detail.get("version_info") or {}
            if info.get("version"):
                seen[info["shipment_key"]].append(eid)
        chains = [sorted(v, key=lambda e: results[e]["version_info"]["version"]) for v in seen.values()]
    else:
        chains = [chain_ids]
    for ids in chains:
        for position, eid in enumerate(ids):
            info = info_of(eid)
            if info is None:
                continue
            if position == 0:
                info["changes_from_previous"] = None
                continue
            info["changes_from_previous"] = diff_versions(
                results.get(ids[position - 1], {}).get("extracted"),
                results.get(eid, {}).get("extracted"),
            )


def annotate_versions(inbox: Any, emails: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Read attachments, build the index and store ``version_info`` on every result."""
    index = build_version_index(collect_records(inbox, emails), results)
    for eid, info in index.items():
        if eid in results:
            results[eid]["version_info"] = info
    return version_summary(results)


def version_summary(results: dict[str, dict[str, Any]]) -> dict[str, int]:
    infos = [d.get("version_info") for d in results.values() if d.get("version_info")]
    chains = {i["shipment_key"]: i["version_count"] for i in infos if i.get("version")}
    return {
        "repeat_groups": sum(1 for i in infos if i["duplicates"]),
        "repeated_emails": sum(1 for i in infos if i["duplicate_of"]),
        "shipments_tracked": len(chains),
        "shipments_with_revisions": sum(1 for n in chains.values() if n > 1),
        "superseded_documents": sum(1 for i in infos if i.get("superseded_by")),
    }
