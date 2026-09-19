"""Lightweight normalization used only for the seven comparison fields."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata


TEXT_FIELDS = {
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_container_count(value: str) -> int | None:
    compact = normalize_text(value)
    match = re.fullmatch(r"(\d+)", compact)
    if match:
        return int(match.group(1))
    match = re.match(r"^(\d+)\s*[x×]\s*\d+", compact)
    if match:
        return int(match.group(1))
    return None


def normalize_weight_kg(value: str) -> Decimal | None:
    compact = normalize_text(value)
    # The review field is already labeled in kilograms, so a bare number is valid.
    if not re.search(r"\bkgs?\b", compact) and not re.fullmatch(
        r"[-+]?\d[\d,\s]*(?:\.\d+)?", compact
    ):
        return None
    match = re.search(r"[-+]?\d[\d,\s]*(?:\.\d+)?", compact)
    if not match:
        return None
    number = re.sub(r"[\s,]", "", match.group(0))
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def normalize_field(field: str, value: str) -> str | int | Decimal | None:
    if field in TEXT_FIELDS:
        return normalize_text(value)
    if field == "container_count":
        return normalize_container_count(value)
    if field == "gross_weight_kg":
        return normalize_weight_kg(value)
    raise KeyError(f"Unsupported comparison field: {field}")
