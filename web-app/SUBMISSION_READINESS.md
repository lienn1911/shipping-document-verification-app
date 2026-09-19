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

- PDF, Word, spreadsheets and scanned-document extraction are unsupported;
  affected requests are escalated. The PDF describes these as advanced work.
- Official local evaluation endpoint on port 8080 was unavailable; no score or
  accuracy claim is made. Do not consult organizer answer files to infer a score.
- Hosting, portal packaging, deadline and additional contest-specific criteria
  are not established by the supplied use-case document.

Conclusion: basic workflow and submission-format checks passed. This is not a
guarantee of competition acceptance, advanced-stage completion, or accuracy.
