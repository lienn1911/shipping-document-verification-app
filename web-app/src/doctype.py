"""Content-based document type detection for shipping attachments.

Decides whether an attachment is a Shipping Instruction (SI), a draft Bill of
Lading (BL), an Invoice, or Unknown by reading the document's *title*. The
filename and the upload slot are only weak hints and never override content:
in the participant data five files named ``*_BL.txt`` are really packing lists,
certificates of origin or an invoice.

Design notes
------------
* Pure functions, standard library only. Input is plain text, so any reader
  (txt, docx, xlsx, PDF text layer, OCR) can feed it.
* Instruction titles are tested before bill-of-lading titles, because
  "BILL OF LADING INSTRUCTION" (used by some SI PDFs) contains "BILL OF LADING".
* Only *title-like* lines count: at most a few modifier words before the title
  (DRAFT, FINAL, ...), so a sentence such as "as per shipping instruction"
  never matches.
* Token matching tolerates small OCR errors on longer words (LADlNG -> LADING).
* Conflicting titles, missing text and missing titles are reported explicitly
  instead of guessed, so callers can send the case to human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable

SI = "SI"
BL = "BL"
INVOICE = "INVOICE"
UNKNOWN = "UNKNOWN"

TITLE_WINDOW = 12  # non-empty lines that count as the document header
FALLBACK_WINDOW = 30  # wider, lower-confidence search when the header has no title
MAX_TITLE_TOKENS = 8  # a title line is short; sentences are not titles
MIN_TRUSTED_CONFIDENCE = 0.75  # below this, callers should not trust the type
_FUZZY_MIN_LEN = 5  # only words this long may be fuzzy-matched (OCR noise)
_FUZZY_RATIO = 0.8

# Words that may precede a title without making the line a sentence.
_MODIFIERS = frozenset(
    {"DRAFT", "FINAL", "ORIGINAL", "COPY", "REVISED", "AMENDED", "NON", "NEGOTIABLE",
     "OCEAN", "SEA", "HOUSE", "MASTER", "COMBINED", "TRANSPORT"}
)

# (type, label, phrase tokens, only_when_line_is_short). Order matters: SI first.
_PHRASES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (SI, "Shipping Instruction", ("SHIPPING", "INSTRUCTION"), False),
    (SI, "Shipping Instruction", ("SHIPPING", "INSTRUCTIONS"), False),
    (SI, "Bill of Lading Instruction", ("BILL", "OF", "LADING", "INSTRUCTION"), False),
    (SI, "Bill of Lading Instruction", ("BILL", "OF", "LADING", "INSTRUCTIONS"), False),
    (SI, "B/L Instruction", ("B", "L", "INSTRUCTION"), False),
    (SI, "B/L Instruction", ("BL", "INSTRUCTION"), False),
    (SI, "Shipping Instruction", ("S", "I"), True),
    (SI, "Shipping Instruction", ("SI",), True),
    (BL, "Bill of Lading", ("BILL", "OF", "LADING"), False),
    (BL, "Bill of Lading", ("B", "L"), True),
    (BL, "Bill of Lading", ("BL",), True),
    (INVOICE, "Commercial Invoice", ("COMMERCIAL", "INVOICE"), False),
    (INVOICE, "Proforma Invoice", ("PROFORMA", "INVOICE"), False),
    (INVOICE, "Proforma Invoice", ("PRO", "FORMA", "INVOICE"), False),
    (INVOICE, "Tax Invoice", ("TAX", "INVOICE"), False),
    (INVOICE, "Invoice", ("INVOICE",), True),
)

# Recognised shipping documents that are neither SI, BL nor an invoice.
_OTHER_DOCUMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Packing List", ("PACKING", "LIST")),
    ("Certificate of Origin", ("CERTIFICATE", "OF", "ORIGIN")),
    ("Delivery Order", ("DELIVERY", "ORDER")),
    ("Booking Confirmation", ("BOOKING", "CONFIRMATION")),
    ("Arrival Notice", ("ARRIVAL", "NOTICE")),
    ("Debit Note", ("DEBIT", "NOTE")),
    ("Credit Note", ("CREDIT", "NOTE")),
    ("Weight Certificate", ("WEIGHT", "CERTIFICATE")),
    ("Customs Declaration", ("CUSTOMS", "DECLARATION")),
)

_FILENAME_HINT = re.compile(r"(?:^|[^A-Za-z])(SI|BL)(?:[^A-Za-z]|$)", re.IGNORECASE)


@dataclass
class DocumentType:
    """Result of detecting one document's type."""

    type: str = UNKNOWN
    label: str | None = None  # human name of what was found, e.g. "Packing List"
    confidence: float = 0.0
    reason: str = "no_title_found"
    evidence: dict[str, Any] | None = None
    filename_hint: str | None = None  # weak hint only; never decides the type
    conflicts: list[str] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return self.type in {SI, BL, INVOICE} and self.confidence >= MIN_TRUSTED_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "evidence": self.evidence,
            "filename_hint": self.filename_hint,
            "conflicts": list(self.conflicts),
        }


# --------------------------------------------------------------------------- #
# Tokenising and matching
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).upper()
    return re.findall(r"[A-Z0-9]+", folded)


def _token_matches(token: str, wanted: str) -> bool:
    if token == wanted:
        return True
    if len(wanted) < _FUZZY_MIN_LEN or len(token) < _FUZZY_MIN_LEN - 1:
        return False
    return SequenceMatcher(None, token, wanted).ratio() >= _FUZZY_RATIO


def _match_phrase(tokens: list[str], phrase: tuple[str, ...]) -> tuple[int, bool] | None:
    """Find ``phrase`` as consecutive tokens preceded only by modifier words.

    Returns (start_index, was_fuzzy) or None.
    """
    n = len(phrase)
    for start in range(0, min(len(tokens) - n, 3) + 1):
        if any(tok not in _MODIFIERS for tok in tokens[:start]):
            break
        window = tokens[start:start + n]
        if len(window) < n:
            break
        exact = window == list(phrase)
        if exact or all(_token_matches(t, w) for t, w in zip(window, phrase)):
            return start, not exact
        if _joined_match(tokens[start:], phrase):
            return start, True
    return None


def _joined_match(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    """Match when OCR merged or split words, e.g. "BILLOF LADING" for "BILL OF LADING"."""
    target = "".join(phrase)
    if len(target) < 8:  # too short to compare without spurious hits (BL, SI, INVOICE)
        return False
    for span in range(1, min(len(tokens), len(phrase) + 1) + 1):
        joined = "".join(tokens[:span])
        if joined == target or SequenceMatcher(None, joined, target).ratio() >= 0.9:
            return True
    return False


def _line_candidates(tokens: list[str]) -> list[tuple[str, str, bool]]:
    """All (type, label, fuzzy) title interpretations of one line, SI first."""
    if not tokens or len(tokens) > MAX_TITLE_TOKENS:
        return []
    found: list[tuple[str, str, bool]] = []
    for doc_type, label, phrase, short_only in _PHRASES:
        # A "short-only" phrase (bare BL, SI, INVOICE) must be the whole line
        # apart from modifiers, so "BL NO 123" or "INVOICE NO" never count.
        if short_only and len([t for t in tokens if t not in _MODIFIERS]) > len(phrase):
            continue
        hit = _match_phrase(tokens, phrase)
        if hit:
            found.append((doc_type, label, hit[1]))
            break  # first (highest priority) interpretation wins for this line
    if not found:
        for label, phrase in _OTHER_DOCUMENTS:
            hit = _match_phrase(tokens, phrase)
            if hit:
                found.append((UNKNOWN, label, hit[1]))
                break
    return found


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def filename_hint(filename: str | None) -> str | None:
    """Weak hint from a name such as ``email_004_SI.txt`` (never authoritative)."""
    if not filename:
        return None
    match = _FILENAME_HINT.search(Path(filename).stem.replace("_", " "))
    return match.group(1).upper() if match else None


def _score(line_index: int, fuzzy: bool, window: str) -> float:
    if window == "fallback":
        base = 0.7
    elif line_index < 3:
        base = 0.98
    else:
        base = 0.92
    return min(base, 0.82) if fuzzy else base


def detect_document_type(
    text: str | None,
    filename: str | None = None,
    sheet_names: Iterable[str] = (),
) -> DocumentType:
    """Detect what kind of document ``text`` is.

    ``sheet_names`` lets spreadsheet readers pass worksheet names ("S.I.", "BL"),
    which some xlsx files use instead of a printed title. The result never
    depends on ``filename``; it is only echoed back as ``filename_hint``.
    """
    hint = filename_hint(filename)
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return DocumentType(reason="no_text", filename_hint=hint)

    hits: list[dict[str, Any]] = []

    for name in sheet_names:
        for doc_type, label, fuzzy in _line_candidates(_tokens(str(name))):
            hits.append({"type": doc_type, "label": label, "line": 0, "excerpt": f"[sheet] {name}",
                         "confidence": 0.9, "sheet": True})

    for index, line in enumerate(lines[:FALLBACK_WINDOW]):
        window = "header" if index < TITLE_WINDOW else "fallback"
        for doc_type, label, fuzzy in _line_candidates(_tokens(line)):
            hits.append({"type": doc_type, "label": label, "line": index + 1, "excerpt": line[:120],
                         "confidence": _score(index, fuzzy, window), "sheet": False})

    if not hits:
        return DocumentType(reason="no_title_found", filename_hint=hint)

    # Prefer printed titles over sheet names, then the earliest, then the strongest.
    printed = [h for h in hits if not h["sheet"]]
    ordered = sorted(printed or hits, key=lambda h: (h["line"], -h["confidence"]))
    best = ordered[0]

    # Any *different* recognised type in the header is a conflict, not a guess.
    rivals = [h for h in hits if h["type"] != best["type"] and h["line"] <= TITLE_WINDOW]
    if best["type"] in {SI, BL, INVOICE} and rivals:
        # A worksheet name that disagrees with the printed title is a conflict too.
        labels = sorted({h["label"] for h in rivals})
        return DocumentType(
            type=UNKNOWN,
            label=best["label"],
            confidence=0.5,
            reason="conflicting_titles",
            evidence={"line": best["line"], "excerpt": best["excerpt"]},
            filename_hint=hint,
            conflicts=labels,
        )

    confidence = best["confidence"]
    # A worksheet name that agrees with the printed title is corroboration.
    if any(h["sheet"] and h["type"] == best["type"] for h in hits) and not best["sheet"]:
        confidence = min(0.99, confidence + 0.01)

    return DocumentType(
        type=best["type"],
        label=best["label"],
        confidence=confidence,
        reason="title_match" if best["type"] != UNKNOWN else "other_document",
        evidence={"line": best["line"], "excerpt": best["excerpt"]},
        filename_hint=hint,
    )


# --------------------------------------------------------------------------- #
# Revision markers (used to tell which of several versions is the latest)
# --------------------------------------------------------------------------- #

_REVISION_NUMBER = re.compile(r"\b(?:REV(?:ISION)?|VER(?:SION)?|AMENDMENT|V)\s*\.?\s*(\d{1,2})\b")
_REVISED_WORDS = re.compile(r"\b(?:AMENDED|REVISED|CORRECTED|SUPERSEDES?)\b")
_MARKER_LINES = 4  # revision marks live in the title area, never in body text


def _marker_haystacks(text: str | None, filename: str | None) -> list[str]:
    stems = [re.sub(r"[_\-]+", " ", Path(filename).stem)] if filename else []
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()][:_MARKER_LINES]
    return [unicodedata.normalize("NFKC", h).upper() for h in stems + lines]


def revision_marker(text: str | None, filename: str | None = None) -> int | None:
    """Explicit revision number ("REV 2", "V3", "Amendment 1") from title lines or filename.

    Deliberately ignores subjects and body text: "amend BL 057" is a shipment
    reference and "V.51NW1" is a voyage, and neither is a revision number.
    """
    for haystack in _marker_haystacks(text, filename):
        match = _REVISION_NUMBER.search(haystack)
        if match:
            return int(match.group(1))
    return None


def is_marked_revised(text: str | None, filename: str | None = None) -> bool:
    """True when the title area says AMENDED / REVISED / CORRECTED (no number needed)."""
    return any(_REVISED_WORDS.search(h) for h in _marker_haystacks(text, filename))


def latest_revision(paths: list[str], text_of: dict[str, str | None]) -> str | None:
    """Pick the newest of several same-type documents, or None if it is not clear.

    Only explicit, distinct revision numbers decide; anything vaguer stays a
    human decision rather than a guess.
    """
    markers = {path: revision_marker(text_of.get(path), path) for path in paths}
    values = list(markers.values())
    if any(v is None for v in values) or len(set(values)) != len(values):
        return None
    return max(markers, key=markers.get)


# --------------------------------------------------------------------------- #
# Deciding which attachment is the SI and which is the BL
# --------------------------------------------------------------------------- #

@dataclass
class RoleAssignment:
    roles: dict[str, str] = field(default_factory=dict)  # {"SI": path, "BL": path}
    detections: dict[str, dict[str, Any]] = field(default_factory=dict)
    role_source: dict[str, str] = field(default_factory=dict)  # role -> content | filename_fallback
    swapped: bool = False  # filenames/slots said one thing, the content said the other
    superseded: list[str] = field(default_factory=list)  # older revisions ignored in favour of a newer one
    problem: dict[str, str] | None = None  # review_reason / internal_reason / detail


def _problem(review_reason: str, internal_reason: str, detail: str) -> dict[str, str]:
    return {"review_reason": review_reason, "internal_reason": internal_reason, "detail": detail}


def assign_roles(documents: list[tuple[str, str | None]]) -> RoleAssignment:
    """Work out which attachment is the SI and which is the draft BL, by content.

    ``documents`` is a list of ``(path, text)``; ``text`` is None when the file
    could not be read. Documents whose content gives no trustworthy type fall
    back to their filename hint, so unreadable files (corrupt PDF, scan without
    OCR yet) keep flowing to the extraction step, which reports them properly.
    """
    result = RoleAssignment()
    typed: dict[str, DocumentType] = {
        path: detect_document_type(text, filename=path) for path, text in documents
    }
    result.detections = {path: det.to_dict() for path, det in typed.items()}

    conflicted = [p for p, d in typed.items() if d.reason == "conflicting_titles"]
    if conflicted:
        path = conflicted[0]
        det = typed[path]
        result.problem = _problem(
            "unreadable",
            "conflicting_document_titles",
            f"{Path(path).name} carries conflicting titles ({det.label} vs {', '.join(det.conflicts)}); "
            "cannot tell which document type it is",
        )
        return result

    si = [p for p, d in typed.items() if d.trusted and d.type == SI]
    bl = [p for p, d in typed.items() if d.trusted and d.type == BL]
    other = [p for p, d in typed.items() if d.type in {INVOICE, UNKNOWN} and d.label
             and d.reason in {"title_match", "other_document"}]
    untyped = [p for p in typed if p not in si and p not in bl and p not in other]

    # Untrusted or unreadable documents may still fill a missing role by filename.
    fallback = {"SI": [], "BL": []}
    for path in untyped:
        hint = typed[path].filename_hint
        if hint in fallback:
            fallback[hint].append(path)

    sources: dict[str, str] = {}
    chosen: dict[str, str] = {}
    for role, by_content in (("SI", si), ("BL", bl)):
        newest = latest_revision(by_content, dict(documents)) if len(by_content) > 1 else None
        if len(by_content) > 1 and newest is None:
            names = ", ".join(Path(p).name for p in by_content)
            result.problem = _problem(
                "unreadable",
                "multiple_documents_same_type",
                f"Found {len(by_content)} attachments that are each a {role} and no explicit "
                f"revision number tells which is latest: {names}",
            )
            return result
        if newest is not None:
            chosen[role] = newest
            sources[role] = "content_latest_revision"
            result.superseded.extend(p for p in by_content if p != newest)
        elif by_content:
            chosen[role] = by_content[0]
            sources[role] = "content"
        elif len(fallback[role]) == 1:
            chosen[role] = fallback[role][0]
            sources[role] = "filename_fallback"
        elif len(fallback[role]) > 1:
            result.problem = _problem(
                "unreadable",
                "ambiguous_attachment_role",
                f"Could not tell which of {[Path(p).name for p in fallback[role]]} is the {role}",
            )
            return result

    missing = [role for role in ("SI", "BL") if role not in chosen]
    if missing:
        wrong = [p for p in other if p not in chosen.values()]
        if wrong:
            found = "; ".join(f"{Path(p).name} is a {typed[p].label}" for p in wrong)
            result.problem = _problem(
                "wrong_doc_type",
                "wrong_document_type",
                f"Expected a Shipping Instruction and a draft Bill of Lading, but {found}. "
                f"Missing: {', '.join(missing)}",
            )
        else:
            result.problem = _problem(
                "missing_attachment",
                "missing_attachment",
                f"Missing required attachment role(s): {', '.join(missing)}",
            )
        return result

    result.roles = chosen
    result.role_source = sources
    result.swapped = any(
        typed[path].filename_hint not in (None, role)
        for role, path in chosen.items()
        if sources[role].startswith("content")
    )
    return result
