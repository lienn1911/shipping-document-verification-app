# Submission readiness audit

## Update — 2026-09-21 (supersedes the numbers and limits below)

Measured on the full 520-email participant bundle with Gemini and Firebase disabled (74 automated tests pass):

- Categories: BL_COMPARISON 220; SI_REQUEST 125; INVOICE_QUERY 75; GENERAL 60; SPAM 40.
- Comparison requests: **109 compared automatically** (63 no mismatch, 46 mismatch); 111 sent to human review:
  96 missing attachment, 5 wrong document type, 5 unreadable (3 scanned PDFs, 2 corrupt PDFs), 5 missing value.
- Every reported mismatch was checked for formatting-only differences (punctuation, spacing, address layout); none was.
- Since the 2026-09-19 audit: text-layer PDF, DOCX and XLSX readers, content-based document-type detection,
  duplicate/version tracking, normalisation fixes.
- Version tracking: 15 repeated emails and 123 tracked shipments; the dataset has no revisions, so revisions are shown with
  the synthetic demo bundle in `web-app/demo/versions-bundle`.
- Hosting: repository root `requirements.txt`, `.streamlit/config.toml` and secrets support (`GEMINI_*`,
  `FIREBASE_SERVICE_ACCOUNT_JSON`) so the app can be deployed to Streamlit Community Cloud.
- Still unsupported: **scanned PDFs** (3 emails, OCR or a vision model needed). They are escalated to human review with an
  explicit reason, not guessed.
- Still no accuracy claim: the official scoring endpoint was unavailable and no answer key was used.

The sections below are the original 2026-09-19 audit, kept for history.

---

# Submission readiness audit — 2026-09-19

Scope: supplied Shipping Document Verification Use Case.pdf and the supplied
AI integration / cloud infrastructure requirement image. These describe the
known requirements, not a complete competition rubric or portal checklist.

## Verified locally

- Full participant bundle: 520 emails, all present in the validated submission.
- Categories: BL_COMPARISON 220; SI_REQUEST 125; INVOICE_QUERY 75; GENERAL 60; SPAM 40.
- Comparison requests: 84 completed (51 no mismatch, 33 mismatch), 136 review.
- Review: 96 missing attachment (94 with zero attachments, 2 with one),
  30 unsupported formats, 5 missing values, 5 wrong document types.
- Every attachment path listed by the dataset exists on disk.
- No processing failures in this run. This does not establish classification or
  discrepancy accuracy against the private reference answers.
- 56 automated tests passed; installed dependencies passed pip check.
- Generated artifacts: output/readiness-check/submission.json, results.json,
  summary.json, errors.json, review_decisions.json.
- This full-bundle audit disabled Gemini; it is not an AI-enabled benchmark.

## Basic use-case coverage

Implemented: five-category classification; plain-text SI/BL extraction; seven
fields; normalization of supported aliases; mismatch values side by side;
review reasons; evidence; manual corrections and recalculation; visible failures
and retry; operational reports; submission schema and full email-ID coverage.

Existing differentiators: document-role detection, duplicate/version tracking,
audit history, Gemini second opinion, simulated incoming-mail workflow, and
fact-based reply drafts after human review.

## AI and cloud evidence

Normal configuration currently enables Gemini with an API key. Firebase client
initialization succeeds. Earlier session checks successfully called Gemini with
synthetic data and read Firestore. Client initialization alone does not prove a
new write succeeded. The UI saves batch audit records to Firestore; current
coverage does not include every single-case/review update or cloud restoration.
Gemini is a supplementary cross-check; primary classification/extraction and
comparison remain rule-based. Its contribution should be demonstrated with a
real completed response rather than described as AI-driven extraction.

The localhost:8512 preview disables AI and Firebase. Do not use it as evidence
that either external integration was exercised. Mail connection is simulated;
reply drafts are templates and are not sent automatically.

## Remaining limits

- (2026-09-19) PDF, Word, spreadsheets and scanned-document extraction were unsupported; PDF and Word are now
  supported, see the update above. The PDF describes these as advanced work.
- Official local evaluation endpoint on port 8080 was unavailable; no score or
  accuracy claim is made. Do not consult organizer answer files to infer a score.
- Hosting, portal packaging, deadline and additional contest-specific criteria
  are not established by the supplied use-case document.

Conclusion: basic workflow and submission-format checks passed. This is not a
guarantee of competition acceptance, advanced-stage completion, or accuracy.
