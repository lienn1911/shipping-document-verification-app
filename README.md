# CargoCheck — Shipping Document Verification

**Team FiveMinds · Averis x Monash Hackathon 2026**

Shipping teams compare a Shipping Instruction (SI) with a draft Bill of Lading (BL) by hand, and a missed
discrepancy means corrections and delays. CargoCheck reads an inbox, finds the document-check requests,
compares the seven shipment fields, and explains every mismatch with the source line it came from. When it
cannot decide, it escalates to a person with the evidence instead of guessing.

## What it does

| Area | Capability |
|---|---|
| Classify | Sorts emails into BL check, SI request, invoice query, general and spam (rule-based, with a confidence score) |
| Read | `.txt`, text-layer **PDF**, **DOCX** (tables included) and **XLSX**, with page/sheet and line source evidence. **Scanned PDFs** are read with Gemini vision when Gemini is configured and pre-filled in the review form for a person to confirm; otherwise they go to human review with the reason |
| Detect | Decides whether an attachment is an **SI, BL, Invoice or Unknown from its title**, never its filename; corrects swapped uploads |
| Extract and normalise | Seven fields (shipper, consignee, notify party, ports, container count, gross weight in kg) with label aliases and unit/number normalisation |
| Compare | SI vs BL side by side, with a plain-language reason per mismatch |
| Human review | Low confidence, missing or unreadable input goes to a queue; a reviewer confirms or corrects, and the result is recalculated with the original kept |
| Reliability | Task trail (Received → Processing → Completed / Review / Failed), per-case error history and retry |
| Versions | Detects **repeated emails and document revisions**, tracks the latest version per shipment and shows what changed |
| Reports | Search and filters, statistics dashboard, JSON / CSV / PDF export, validated submission file |
| Optional AI | **Gemini** (opt-in): reads scanned documents (vision) and adds a second-opinion risk summary per case |
| Optional cloud | **Firebase Firestore** audit records (outcome, extracted values, mismatches, version tracking and every human-review decision; never email bodies or senders); deployable to Streamlit Community Cloud |
| Extras | Simulated incoming email flow and editable reply drafts (nothing is sent) |

The scored comparison is deterministic and explainable. Gemini transcribes scans and adds a second opinion; the transcription then goes through the same rules as any other document, and Gemini never overrides a comparison.

## Results on the participant dataset (520 emails)

Run with AI and Firebase off:

- Categories: 220 document checks · 125 SI requests · 75 invoice queries · 60 general · 40 spam.
- Of the 220 document-check emails, 91 only ask for a draft BL to be sent and carry no documents (nothing to compare, so they are not
  escalated and not counted as verified). Of the other 129, **109 were compared automatically** (63 no mismatch, 46 mismatch) and 20 went to
  human review: 5 missing attachment, 5 wrong document type, 5 unreadable (3 scans, which Gemini reads and pre-fills for confirmation, and 2
  corrupt PDFs), 5 missing value.
- We checked every reported mismatch for formatting-only differences (punctuation, spacing, address layout): none was, so the mismatch rate reflects real differences.
- Duplicate and version tracking: 15 repeated emails, 123 shipments tracked, 0 revisions (the dataset contains none; use the demo bundle below).
- These numbers are the same with Gemini on or off: the 3 scanned pairs stay in human review either way, and with Gemini configured the reviewer sees the AI's reading pre-filled, a preview of the comparison, and a warning.
- **Scoring.** We ran the organisers' scoring tool (from the info-pack Docker package, which the organisers said is for checking our own work) against a reference regenerated with their generator, first verified identical to the participant dataset. On the 520 emails: email classification 100% (macro-F1 1.00), comparison exact-match 100%, all 46 defect emails caught end to end, and review escalation 20 of 20 with the right reason and no extra escalations.
  Read this with its limits: it is the same data we developed on, not a held-out set, and two behaviours were adjusted after the tool's escalation diagnostic showed we were over-escalating (emails that only ask for a draft BL to be sent are no longer escalated; scans are always confirmed by a person). Both changes are rules based on the wording of the emails and the behaviour of scans, not on individual emails, and are covered by tests.

## Architecture

```
email JSON ─► classify ─► read attachments (txt / pdf / docx / xlsx) ─► detect document type ─► extract 7 fields
   ─► normalise ─► compare ─► confidence & review rules ─► version/duplicate annotation ─► results
                                                                    │
                                     Streamlit UI · exports · (optional) Gemini · (optional) Firestore
```

Readers are plugged in through `TEXT_READERS` in `web-app/src/extract.py`; adding a format means registering one function.

## Quick start

Requires Python 3.10+ (3.12 recommended).

```bash
git clone <this-repo-url> && cd <repo>
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m streamlit run web-app/app.py
```

Open http://localhost:8501. The participant dataset ships in `local-data/participant-bundle`, so **Inbox operations →
Analyze inbox** works immediately, with no keys. In that page's **Data source** menu, choose
*Version-tracking demo* to see repeats, revisions and change tables (synthetic emails built from real documents).

Command line, with no UI: `python web-app/run.py` writes `output/submission.json`, `results.json` and `summary.json`.

## Configuration (optional AI and cloud)

Copy `web-app/.env.example` to `web-app/.env` and fill it in. Nothing here is required to run the app.

- **Gemini:** create a key in Google AI Studio, set `GEMINI_AI_ENABLED=true` and `GEMINI_API_KEY`.
  Run `python web-app/scripts/check_gemini.py` to list the model ids your key can use and set `GEMINI_MODEL`.
  `python web-app/scripts/check_vision.py` tests scanned-PDF reading with your key (about 6 calls); `GEMINI_VISION_ENABLED=false` turns that feature off.
  Gemini runs automatically for single requests and on demand per case (button); full-dataset runs skip it unless `GEMINI_BATCH_ENABLED=true`.
  If the model is overloaded (HTTP 503) the app automatically tries backup models (`GEMINI_FALLBACK_MODELS`; blank = built-in defaults, `none` = off).
- **Firebase:** put the service-account JSON at `web-app/serviceAccountKey.json` (or point `FIREBASE_SERVICE_ACCOUNT` to it).
  Run `python web-app/scripts/check_firebase.py` to test the connection; add `--write` to write one test record.

`.env` and service-account files are git-ignored. **Never commit keys.**

## Tests

```bash
cd web-app
python -m unittest discover -s tests
```

No keys or network needed.

## Deploy to Streamlit Community Cloud

1. Push this repo to a **public** GitHub repository.
2. At share.streamlit.io choose **Create app**, select the repo and branch `main`, and set the main file to `web-app/app.py`.
3. In **Advanced settings → Secrets** paste the following (all optional; omit what you do not use):

```toml
GEMINI_AI_ENABLED = "true"
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "a-model-id-from-check_gemini"
FIREBASE_ENABLED = "true"
# Paste the service-account JSON as-is, or its base64 (base64 -i serviceAccountKey.json | tr -d '\n')
FIREBASE_SERVICE_ACCOUNT_JSON = '''{ ...service account json... }'''
```

The app reads secrets through the same settings as `.env`, so no code changes are needed.

To build the Secrets text from your local `.env` and key file **without ever showing your keys on screen**, run
`python web-app/scripts/make_streamlit_secrets.py --copy` (macOS: copies to the clipboard; paste, then `pbcopy < /dev/null`).
`--write` instead writes a git-ignored `.streamlit/secrets.toml`, which lets you rehearse the hosted setup locally.

## Repository layout

```
web-app/            the application
  app.py            Streamlit UI
  src/              pipeline: classify, readers, doctype, extract, normalize, compare, versions, reporting
  tests/            unit and workflow tests
  demo/             version-tracking demo bundle (regenerate with tools/make_version_demo.py)
  scripts/          manual checks for Gemini and Firebase
local-data/participant-bundle/   the participant dataset (emails and attachments)
sample-upload/      files for the single-request demo
```

## Limitations and next steps

- **Scanned PDFs** depend on Gemini (the first full run with Gemini configured reads them in parallel, adding roughly the time of one Gemini call; later runs reuse the result while the app stays up): a model transcription can misread a value, so vision-read documents are flagged, carry lower confidence, and are always confirmed by a person (the reading is pre-filled). Without Gemini (or if it is unavailable) they go to human review with the reason.
- **XLSX** sheets are read as label/value rows; a bare number in a weight row is taken to be kilograms, because the sheets carry no unit.
- Classification uses the subject and rules; reading the email body with an LLM would handle misleading subjects.
- Party names are compared after normalisation; address-only differences are not yet treated as a warning.
- The mailbox is simulated; a real inbox connector is future work.
