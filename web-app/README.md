# Shipping Document Verification App

A minimal, explainable shipping document verification pipeline for the SDOC
hackathon. It implements only the required basic workflow:

- read every participant-bundle email JSON record;
- classify emails into `BL_COMPARISON`, `SI_REQUEST`, `INVOICE_QUERY`,
  `GENERAL`, or `SPAM`;
- process only `BL_COMPARISON` emails;
- read plain-text SI and BL attachments;
- extract and compare the seven required shipment fields;
- preserve the SI and BL values for every mismatch;
- send missing, unsupported, or unreliable cases to manual review;
- generate a submission matching `sample_submission.json`.

PDF, DOCX, XLSX, scanned documents, and OCR are outside this basic version.
Those attachments are marked `NEEDS_REVIEW` instead of being guessed.

## Required fields

1. `shipper`
2. `consignee`
3. `notify_party`
4. `port_of_loading`
5. `port_of_discharge`
6. `container_count`
7. `gross_weight_kg`

The parser uses a fixed alias table for labels such as `Load Port`, `POL`, and
`Port of Loading`. Normalization is limited to whitespace, letter case,
Unicode, container count, and KG number formatting. It does not use fuzzy
matching.

## Local-only design

All classification, extraction, normalization, and comparison logic runs on
the local machine. The application does not call an external API, use an LLM,
or require an API key. Network access is only needed when you choose to submit
the generated JSON to an official evaluation endpoint.

## Run the local web interface

Create a virtual environment and install Streamlit:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Start the site:

```bash
streamlit run app.py
```

If Windows reports `No module named 'reportlab'`, run these commands from the
`web-app` folder using the same Python installation that starts Streamlit:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The application can still open without ReportLab; JSON and CSV exports remain
available, and the PDF export explains how to install the missing dependency.

Then open `http://localhost:8501`. The interface provides:

- **Single Case** for uploaded email JSON plus SI/BL attachments;
- **Full Dataset** with progress, summary, results, and filters;
- **Submission** generation, schema validation, and JSON download.
- **Error handling** with per-case error history, attempt counts, and retry;
- **Human Review** for confirming or correcting all seven SI/BL fields;
- **Report & Export** with complete JSON, CSV, and paginated PDF reports.

The Human Review page recalculates the comparison after a reviewer saves all
seven SI and BL values. The original extracted values, reviewed values,
decision, time, and reviewer note are retained in the internal result. Failed
processing stays isolated to the affected email and does not stop the inbox.

Each processed case also includes:

- a `Received -> Processing -> Completed / Review / Failed` task trail;
- rule-based classification and field confidence with a human-review threshold;
- source evidence for each extracted value (document, page, line, label, and excerpt);
- a plain-language reason for every detected mismatch.

Confidence values describe the strength of the deterministic rule that produced
the result. They are transparent operational indicators, not probabilities from
a statistically calibrated model.

## Run from the command line

Python 3.10 or newer is sufficient; there are no third-party dependencies.

```bash
python3 run.py --bundle /path/to/sdoc-hackathon-bundle
```

The repository's bundled sample data at `../local-data/participant-bundle` is
used automatically, so the shorter command works after cloning:

```bash
python3 run.py
```

The command writes:

- `output/submission.json`: the official submission shape;
- `output/results.json`: extracted values, mismatch evidence, and manual-review details;
- `output/summary.json`: run counts.
- `output/errors.json`: recorded per-case processing errors and retry history;
- `output/review_decisions.json`: confirmed or corrected human-review decisions.

The web interface can additionally download:

- `verification-results.json`: complete operational results;
- `verification-results.csv`: email and field-level comparison rows;
- `verification-report.pdf`: summary, all cases, and action-required details.

Generated output and hackathon datasets are excluded from Git. If an official
local evaluation server is already running, submit through its public endpoint:

```bash
python3 run.py --bundle /path/to/sdoc-hackathon-bundle \
  --submit-url http://localhost:8080
```

The application reads only `local-data/participant-bundle`. It does not read
organizer evaluation files or a private answer key.
