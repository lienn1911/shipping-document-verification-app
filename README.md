# CargoCheck — Shipping Document Verification

**Team FiveMinds · Averis x Monash Hackathon 2026**

**Live demo:** https://shipping-document-verification-app-soof5bsxirg5aksfwtffcw.streamlit.app/ (Streamlit Community Cloud, deployed from a synced copy of this repository; if it has gone to sleep, click the wake-up button and wait a moment).

# CargoCheck Web App

CargoCheck is a shipping-document verification workflow for operations teams.
The full repository overview is in the [root README](../README.md); this page
focuses on the web application, its processing pipeline, and how to run it.


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


From the repository root:


```bash
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


