"""Manual live check: does Gemini vision read the scanned PDFs, and what does the pipeline make of them?

Run from the web-app folder:   python scripts/check_vision.py
Needs GEMINI_AI_ENABLED=true and a working GEMINI_API_KEY in web-app/.env. It makes about 6 Gemini calls
(one per scanned PDF in the participant dataset) and prints extracted values, never keys.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_service  # noqa: E402
from src.compare import compare_documents  # noqa: E402
from src.extract import ReviewRequired, extract_attachment_with_evidence  # noqa: E402

BUNDLE = Path(__file__).resolve().parents[2] / "local-data" / "participant-bundle"
PAIRS = ("email_512", "email_513", "email_514")


class BundleInbox:
    def read_bytes(self, path):
        return (BUNDLE / path).read_bytes()


print("Gemini:", {k: v for k, v in ai_service.integration_status().items() if k != "model"}, "| vision enabled:", ai_service.vision_enabled())
if not ai_service.vision_enabled():
    raise SystemExit("Vision is off. Set GEMINI_AI_ENABLED=true and GEMINI_API_KEY in web-app/.env (GEMINI_VISION_ENABLED must not be false).")

for email_id in PAIRS:
    print(f"\n=== {email_id}")
    fields = {}
    for role in ("SI", "BL"):
        path = f"attachments/{email_id}_{role}.pdf"
        started = time.monotonic()
        try:
            values, evidence = extract_attachment_with_evidence(BundleInbox(), path, role)
        except ReviewRequired as exc:
            print(f"  {role}: NEEDS REVIEW ({exc.internal_reason}): {exc.detail[:160]}")
            break
        fields[role] = values
        method = {e.get("read_method") for e in evidence.values()}
        print(f"  {role}: read by {method}, model {next(iter(evidence.values())).get('model')}, {time.monotonic() - started:.1f}s", flush=True)
        for name, value in values.items():
            print(f"     {name:18s} {value}")
    if len(fields) == 2:
        # the same rule the app applies to scans: names/ports differing only in punctuation are not defects
        result = compare_documents(fields["SI"], fields["BL"], ignore_punctuation=True)
        note = f" (punctuation ignored for: {result['ignored_punctuation']})" if result["ignored_punctuation"] else ""
        print("  ->", result["message"], [m["field"] for m in result["mismatches"]], note)
