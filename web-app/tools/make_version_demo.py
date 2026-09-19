#!/usr/bin/env python3
"""Build a small demo inbox that exercises duplicate and version tracking.

The participant dataset contains no revisions or resends of document-check
emails, so there is nothing to demonstrate on it. This script derives a
realistic scenario set from *real* dataset documents and writes it as an
Inbox-compatible bundle (same layout and loader as the participant bundle):

    web-app/demo/versions-bundle/{inbox,attachments,sample_submission.json,loader.py}

Run from anywhere:  python3 web-app/tools/make_version_demo.py
Deterministic: running it twice produces identical files.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
WEB_APP = HERE.parent
REPO = WEB_APP.parent
SOURCE = REPO / "local-data" / "participant-bundle"
TARGET = WEB_APP / "demo" / "versions-bundle"

sys.path.insert(0, str(WEB_APP))
from src.classify import BL_COMPARISON, classify_email  # noqa: E402
from src.extract import parse_fields  # noqa: E402


def read_email(n: int) -> dict:
    return json.loads((SOURCE / "inbox" / f"email_{n:03d}.json").read_text(encoding="utf-8"))


def read_att(n: int, role: str) -> str:
    return (SOURCE / "attachments" / f"email_{n:03d}_{role}.txt").read_text(encoding="utf-8")


def usable_txt_pair(n: int) -> bool:
    try:
        email = read_email(n)
        if classify_email(email)["category"] != BL_COMPARISON or len(email["attachments"]) != 2:
            return False
        parse_fields(read_att(n, "SI"), "SI", "si")
        parse_fields(read_att(n, "BL"), "BL", "bl")
        return True
    except Exception:
        return False


def retitle(text: str, revision: int) -> str:
    """Add ' REV n' to the first (title) line."""
    first, _, rest = text.partition("\n")
    return f"{first.rstrip()} REV {revision}\n{rest}"


def bump_weight(text: str, old: str, new: str) -> str:
    assert old in text, f"expected {old!r} in document"
    return text.replace(old, new)


def main() -> None:
    if not (SOURCE / "inbox").is_dir():
        raise SystemExit(f"Participant bundle not found at {SOURCE}")
    shutil.rmtree(TARGET, ignore_errors=True)
    (TARGET / "inbox").mkdir(parents=True)
    (TARGET / "attachments").mkdir()
    shutil.copy(SOURCE / "loader.py", TARGET / "loader.py")

    emails: list[dict] = []

    def add(eid: int, base: dict, *, subject=None, body=None, date: str, attachments: dict[str, str]) -> str:
        """Write one email; ``attachments`` maps a filename suffix (e.g. 'SI') to text."""
        email_id = f"email_{eid}"
        paths = []
        for suffix, text in attachments.items():
            name = f"{email_id}_{suffix}.txt"
            (TARGET / "attachments" / name).write_text(text, encoding="utf-8")
            paths.append(f"attachments/{name}")
        record = {
            "email_id": email_id,
            "from": base["from"],
            "subject": subject if subject is not None else base["subject"],
            "body": body if body is not None else base["body"],
            "date": date,
            "attachments": paths,
        }
        emails.append(record)
        return email_id

    revised_body = "Hi,\n\nPlease find the revised draft BL ({label}) attached. Kindly re-check it against the SI.\n\nBest Regards"

    # ---- Shipment A: original with errors -> resend -> REV 2 fixes -> REV 3 breaks the weight
    a = read_email(4)
    a_si, a_bl = read_att(4, "SI"), read_att(4, "BL")
    add(901, a, date="2026-09-14T09:00:00+08:00", attachments={"SI": a_si, "BL": a_bl})
    add(902, a, subject="RE_ " + a["subject"], date="2026-09-14T09:30:00+08:00", attachments={"SI": a_si, "BL": a_bl})  # exact resend
    a_bl2 = retitle(a_bl.replace("UAB NOVAKOPA", "EAST BRIGHT FZ-LLC"), 2)  # fixes consignee + notify
    add(903, a, subject="RE_ " + a["subject"], body=revised_body.format(label="REV 2"),
        date="2026-09-15T10:00:00+08:00", attachments={"SI": a_si, "BL": a_bl2})
    a_bl3 = retitle(bump_weight(a_bl.replace("UAB NOVAKOPA", "EAST BRIGHT FZ-LLC"), "131,058 KG", "133,058 KG"), 3)
    add(904, a, subject="RE_ " + a["subject"], body=revised_body.format(label="REV 3"),
        date="2026-09-16T11:00:00+08:00", attachments={"SI": a_si, "BL": a_bl3})

    # ---- Other real shipments used for the remaining scenarios
    bases = [n for n in range(1, 200) if usable_txt_pair(n) and n not in (4,)]
    b_n, c_n, d_n = bases[0], bases[1], bases[2]

    # ---- Shipment B: a DIFFERENT shipment that shares shipment A's subject line (must not be linked)
    b = read_email(b_n)
    add(905, b, subject=a["subject"], date="2026-09-14T12:00:00+08:00",
        attachments={"SI": read_att(b_n, "SI"), "BL": read_att(b_n, "BL")})

    # ---- Shipment C: the SI itself is amended (discharge port changes), BL is unchanged
    c = read_email(c_n)
    c_si, c_bl = read_att(c_n, "SI"), read_att(c_n, "BL")
    add(906, c, date="2026-09-14T13:00:00+08:00", attachments={"SI": c_si, "BL": c_bl})
    fields = parse_fields(c_si, "SI", "si")
    changed_pod = "ROTTERDAM, NETHERLANDS (NLRTM)"
    c_si2 = retitle(c_si.replace(fields["port_of_discharge"], changed_pod), 2)
    assert c_si2 != retitle(c_si, 2), "SI amendment did not change the discharge port"
    add(907, c, subject="RE_ " + c["subject"], body=revised_body.format(label="SI REV 2").replace("draft BL", "SI"),
        date="2026-09-15T14:00:00+08:00", attachments={"SI": c_si2, "BL": c_bl})

    # ---- One email carrying TWO revisions of the BL: the newer one must be the one compared
    d = read_email(d_n)
    d_si, d_bl = read_att(d_n, "SI"), read_att(d_n, "BL")
    d_weight = parse_fields(d_bl, "BL", "bl")["gross_weight_kg"]
    d_bl2 = retitle(bump_weight(d_bl, d_weight, "99,999 KG"), 2)
    add(908, d, date="2026-09-15T15:00:00+08:00",
        attachments={"SI": d_si, "BL_v1": retitle(d_bl, 1), "BL_v2": d_bl2})

    # ---- The same spam email received twice
    spam = next(read_email(n) for n in range(1, 200) if classify_email(read_email(n))["category"] == "SPAM")
    add(909, spam, date="2026-09-16T08:00:00+08:00", attachments={})
    add(910, spam, date="2026-09-16T08:05:00+08:00", attachments={})

    for record in emails:
        (TARGET / "inbox" / f"{record['email_id']}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    placeholder = {"category": "GENERAL", "status": "OK", "review_reason": None, "defect_fields": [], "has_defect": False}
    (TARGET / "sample_submission.json").write_text(
        json.dumps({r["email_id"]: placeholder for r in emails}, indent=2) + "\n", encoding="utf-8"
    )
    (TARGET / "README.md").write_text(
        "# Version-tracking demo bundle\n\nGenerated by `web-app/tools/make_version_demo.py` from real participant "
        "documents. Not part of the scored dataset.\n\n"
        "| Emails | Scenario |\n|---|---|\n"
        "| email_901-904 | Shipment A: original (2 defects) -> exact resend -> REV 2 fixes both -> REV 3 changes the weight |\n"
        "| email_905 | A different shipment that reuses shipment A's subject line (must NOT be linked) |\n"
        "| email_906-907 | Shipment C: the SI is amended (discharge port changes) |\n"
        "| email_908 | One email with two BL revisions; the newer one is compared |\n"
        "| email_909-910 | The same spam email received twice |\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(emails)} emails to {TARGET.relative_to(REPO)} (bases: A=004, B={b_n}, C={c_n}, D={d_n})")


if __name__ == "__main__":
    main()
