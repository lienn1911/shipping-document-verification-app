"""Field-by-field SI to BL comparison."""

from __future__ import annotations

import re
from typing import Any

from .extract import FIELDS, ReviewRequired
from .normalize import TEXT_FIELDS, normalize_field


def _same_words(a: Any, b: Any) -> bool:
    """Equal once punctuation and spacing are ignored ("PTE LTD" vs "PTE. LTD.")."""
    return re.sub(r"[\W_]+", "", str(a)) == re.sub(r"[\W_]+", "", str(b))


def compare_documents(
    si_fields: dict[str, str], bl_fields: dict[str, str], ignore_punctuation: bool = False
) -> dict[str, Any]:
    """Compare the seven fields.

    ``ignore_punctuation`` is for documents a model transcribed from a scan: dropping a small full stop is the
    commonest misreading, so names and ports that differ only in punctuation/spacing count as equal. Numbers
    (container count, weight) are never treated leniently, and the fields it applied to are reported.
    """
    mismatches: list[dict[str, str]] = []
    ignored: list[str] = []
    for field in FIELDS:
        si_normalized = normalize_field(field, si_fields[field])
        bl_normalized = normalize_field(field, bl_fields[field])
        if si_normalized is None or bl_normalized is None:
            raise ReviewRequired(
                "unreadable",
                "unreliable_field_normalization",
                f"Could not reliably normalize {field}",
            )
        if si_normalized != bl_normalized:
            if ignore_punctuation and field in TEXT_FIELDS and _same_words(si_normalized, bl_normalized):
                ignored.append(field)
                continue
            mismatches.append(
                {"field": field, "si_value": si_fields[field], "bl_value": bl_fields[field]}
            )

    if not mismatches:
        return {"status": "OK", "message": "No mismatch detected.", "mismatches": [], "ignored_punctuation": ignored}
    return {
        "status": "MISMATCH",
        "message": f"Mismatch detected in {len(mismatches)} field(s).",
        "mismatches": mismatches,
        "ignored_punctuation": ignored,
    }
