"""Manual check of the Gemini setup. Not a unit test: it makes a real API call.

Run from the web-app folder:   python scripts/check_gemini.py
Lists the models your key can use, then runs one tiny analysis.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_service import analyze_shipping_document, integration_status  # noqa: E402

status = integration_status()
print("Status:", {k: v for k, v in status.items() if k != "model"}, "| model:", status["model"])
if status["status"] != "ready":
    raise SystemExit("Set GEMINI_AI_ENABLED=true and GEMINI_API_KEY in web-app/.env (see .env.example).")

try:
    from google import genai

    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        names = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if "generateContent" in (getattr(m, "supported_actions", None) or ["generateContent"])
        ]
    print(f"\nModels available to this key ({len(names)}). Set GEMINI_MODEL to one of these ids:")
    print("  " + "\n  ".join(names[:25]))
    if status["model"] not in names:
        print(f"\nWARNING: the configured model '{status['model']}' is not in that list.")
except Exception as exc:
    print("\nCould not list models:", type(exc).__name__)

print("\nSample analysis:")
print(analyze_shipping_document({
    "si": {"shipper": "ABC Trading", "gross_weight_kg": "20000 KG"},
    "bl": {"shipper": "ABC Trading", "gross_weight_kg": "21000 KG"},
}))
