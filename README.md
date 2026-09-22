# CargoCheck — Shipping Document Verification

**Team FiveMinds · Averis x Monash Hackathon 2026**

**Live demo:** https://shipping-document-verification-app-soof5bsxirg5aksfwtffcw.streamlit.app/ (Streamlit Community Cloud, deployed from a synced copy of this repository; if it has gone to sleep, click the wake-up button and wait a moment).

## Written responses

**Problem-solution alignment.** Shipping teams compare a Shipping Instruction (SI) with a draft Bill of Lading
(BL) by hand across mixed formats and inconsistent field labels; a missed discrepancy causes corrections and
delays. CargoCheck classifies the inbox, reads SI/BL attachments in five formats, extracts and compares the
seven required fields, and escalates anything it cannot confidently resolve to a person with the evidence,
rather than guessing.

**AI and cloud infrastructure integration.** Google Gemini reads scanned PDFs with no text layer (vision),
with the transcription then going through the same deterministic rules as any other document; a person always
confirms a Gemini-read value before it is trusted. Gemini also gives an on-demand second-opinion risk summary
per case. Firebase Firestore stores a server-side audit record per case (outcome, extracted values, mismatches,
version tracking, human-review decisions), readable back on the Saved results page, with server-only access.
The app is deployed on Streamlit Community Cloud (link above).

**User feedback/testing.** This was built end-to-end in a single hackathon session, so there was no external
user trial. Verification instead came from: 168 automated tests covering every reader, the comparison and
escalation rules, Gemini resilience, and Firestore records; and a run of the organizers' own scoring tool
against the 520-email participant dataset (see Validation results below), which surfaced and fixed a real
over-escalation bug (91 emails were being wrongly flagged for review) before submission.

**Coding challenges.** See Key implementation challenges below: mixed-format party-name normalisation, scan
transcription reliability, Gemini quota/latency, and the over-escalation bug found by validation.

**Success metrics.** 520 emails classified, 129 comparison cases (109 automatic, 20 escalated to a person with
a reason), 46 real mismatches (checked individually for formatting-only false alarms: none found), 100%
classification and 100% comparison exact-match against the organizers' scoring tool on the development dataset,
168 passing tests, and a live, publicly reachable deployment.

**Scalability plans.** See Roadmap below: a real mailbox connector and unseen-data validation in the near
term, AI-assisted triage and explainable fuzzy matching in the medium term, and multilingual, multi-format,
enterprise-integrated regional expansion beyond that -- all while keeping a human in the loop for uncertain
cases.

## Problem and solution

Shipping teams often need to identify document-checking requests in email,
locate the relevant Shipping Instruction (SI) and draft Bill of Lading (BL),
and compare shipment details across mixed document formats. CargoCheck turns
that manual workflow into an explainable pipeline:


- Classifies requests as BL checks, SI requests, invoice queries, general mail,
  or spam.
- Reads TXT, PDF, DOCX, XLSX, and scanned PDF attachments.
- Detects document type from document content and titles, not filenames alone.
- Extracts and normalizes seven fields: shipper, consignee, notify party, port
  of loading, port of discharge, container count, and gross weight.
- Compares SI and BL values with deterministic verification rules.
- Provides source evidence for values and discrepancies, including document,
  page or sheet, line or row, excerpt, and reason.
- Escalates missing, unreadable, unsupported, or uncertain cases to human review.
- Tracks duplicates, document versions, reviewer decisions, and audit history.


> **Rules verify. AI assists. Humans handle uncertainty.**


The rule-based comparison remains authoritative. Gemini can transcribe scanned
documents and provide an on-demand second opinion, but it never overrides the
comparison result.


## Processing pipeline


```text
Email JSON
  -> request classification
  -> attachment readers
  -> document-type detection
  -> seven-field extraction
  -> normalization
  -> deterministic comparison
  -> evidence and review rules
  -> version and audit records
```


Every result preserves the original extracted values. A reviewer can confirm or
correct all seven SI and BL values; the comparison is then recalculated while
the original values, decision, timestamp, and note remain available.


## AI and cloud infrastructure


### Google Gemini


Gemini is used primarily for scanned-PDF vision:


```text
Scanned PDF -> Gemini Vision -> transcription -> rule-based extraction -> comparison
```


Vision-read values are tagged as AI-read, assigned lower confidence, pre-filled
for review, and always require human confirmation. Reliability controls include
backup models, shared cooldown handling, parallel scan reading, cached results,
and on-demand calls instead of sending the whole dataset to Gemini.


### Firebase Firestore


When configured, Firestore stores server-side audit records containing extracted
values, mismatches, processed documents, version information, Gemini verdicts,
and human-review decisions. Database access is server-only. Keep credentials in
the environment or the git-ignored `serviceAccountKey.json`; never commit keys.


### Hosting


The app is built with Streamlit and can be deployed through Streamlit Community
Cloud. Gemini and Firebase are optional for local deterministic processing.


## Validation results


On the 520-email participant dataset:


| Metric | Result |
| --- | ---: |
| Emails tested | 520 |
| Document-checking requests | 220 |
| Comparison cases | 129 |
| Automatic comparisons | 109 |
| Human-review cases | 20 |
| Mismatches detected | 46 |
| Classification exact match | 100% |
| Comparison exact match | 100% |


The 20 review cases were five missing attachments, five wrong document types,
five missing values, and five unreadable documents. These results were measured
with the organizers' scoring tool on the same development dataset used during
development, not on a held-out test set. They should therefore not be treated
as independent real-world accuracy. No formal external user-feedback study is
included yet.


## Key implementation challenges


- **Mixed formats:** dedicated readers handle TXT, PDF, DOCX, and XLSX before
  normalization and comparison.
- **Scan transcription:** Gemini output is explicitly marked for confirmation;
  punctuation differences can be handled without weakening numeric checks.
- **Quota and latency:** backup models, caching, cooldowns, and parallel scan
  processing limit cost and delay.
- **Over-escalation:** classification distinguishes requests to send a draft BL
  from cases that actually require document comparison.


## Run locally

Requires Python 3.10+ (3.12 recommended).

```bash
git clone https://github.com/lienn1911/shipping-document-verification-app.git && cd shipping-document-verification-app
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run web-app/app.py
```


Open `http://localhost:8501`. The bundled participant data is used by default.
The interface includes single-case uploads, full-dataset analysis, progress and
filters, human review, retryable error handling, submission validation, and
JSON/CSV/PDF reports.


For command-line processing from the `web-app` directory:


```bash
python run.py
python run.py --bundle /path/to/sdoc-hackathon-bundle
```


Outputs are written to `output/`: `submission.json`, `results.json`,
`summary.json`, `errors.json`, and `review_decisions.json`.


## Deploy to Streamlit Community Cloud

1. Push this repo to a **public** GitHub repository.
2. At share.streamlit.io choose **Create app**, select the repo and branch `main`, and set the main file to `web-app/app.py`.
3. In **Advanced settings -> Secrets** paste the following (all optional; omit what you do not use):

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

## Configuration and tests


Copy `web-app/.env.example` to `web-app/.env` when enabling optional services.
Set `GEMINI_AI_ENABLED=true` and `GEMINI_API_KEY` for Gemini, and configure
`FIREBASE_SERVICE_ACCOUNT` or `serviceAccountKey.json` for Firestore.


Run the automated tests from the repository root:


```bash
python -m unittest discover -s web-app/tests
```


No keys or network access are required for the test suite.


## Scalability Plans

CargoCheck is designed as a modular workflow so that individual components can be extended without replacing the entire system.

**0–6 Months — Real-World Pilot**

-Connect CargoCheck to a real operational mailbox

-Support additional document formats and languages

-Validate against unseen operational data

-Measure operational value in a real shipping workflow

**6–12 Months — Enterprise Workflow**

-AI-powered email triage

-Explainable fuzzy matching

-Automated reply drafts

-Analytics and monitoring

**12–18+ Months — Regional Expansion**

-Expand from Malaysia to Singapore and wider ASEAN workflows

-Enterprise-system integration

-Multilingual document intelligence

-Human-in-the-loop learning

-Support broader shipping-document workflows

The long-term direction is to evolve CargoCheck from a document-comparison prototype into continuous document intelligence for shipping operations, while retaining human oversight for uncertain cases.


