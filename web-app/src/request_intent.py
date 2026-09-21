"""Tells an email that asks to COMPARE documents apart from one that asks for a document to be SENT.

Both are document-check requests with no attachments, but only the first has anything missing:
  "Please compare the SI and draft BL for X and confirm (attachments appear to have been dropped)"  -> missing attachment
  "Please assist to send the draft BL for X for checking asap"                                       -> nothing to compare

The rule only ever says "nothing to compare" on POSITIVE evidence (an explicit request to send or provide the draft BL)
and never when the text mentions comparing or missing/dropped attachments. Anything unclear stays an escalation.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# The security banner that mail gateways prepend ("...exercise caution with E-Mail content and any links or attachments.")
_BANNER = re.compile(r"WARNING:.*?(?:links or attachments\.?|\n\s*\n)", re.IGNORECASE | re.DOTALL)

_SEND_REQUEST = re.compile(
    r"\b(?:send|provide|share|forward|issue|revert with)\b[^.\n]{0,40}?\b(?:draft\s+)?(?:bl|b/l|bill of lading)\b", re.IGNORECASE
)
_COMPARE_REQUEST = re.compile(
    r"\bcompare\b|\bcheck\b[^.\n]{0,30}\b(?:si|shipping instruction)\b[^.\n]{0,20}\b(?:bl|b/l|bill of lading)\b", re.IGNORECASE
)
_ATTACHMENT_LANGUAGE = re.compile(
    r"\battachments?\b|\battached\b|\benclosed\b|\bstill missing\b|\bdropped\b|\bnot attached\b|\bmissing\b|\bforgot\b",
    re.IGNORECASE,
)


def message_text(email: Mapping[str, Any]) -> str:
    """Subject and body with the gateway security banner removed."""
    body = _BANNER.sub(" ", str(email.get("body", "")))
    return re.sub(r"\s+", " ", f"{email.get('subject', '')}. {body}").strip()


def asks_for_document_to_be_sent(email: Mapping[str, Any]) -> bool:
    """True only for an explicit "send/provide the draft BL" request with no comparison or attachment language."""
    text = message_text(email)
    if not _SEND_REQUEST.search(text):
        return False
    return not (_COMPARE_REQUEST.search(text) or _ATTACHMENT_LANGUAGE.search(text))
