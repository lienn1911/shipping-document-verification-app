# Workspace structure

The navigation follows the workflow in Shipping Document Verification Use Case.pdf.

- Workspace
  - Overview: current-run totals, exceptions, and entry points.
- Verification
  - Inbox & work queue: classify all emails, compare relevant SI/BL pairs, filter and page results.
  - Single request: upload an email and its attachments; inspect a single result.
- Resolution
  - Review & resolution
    - Manual verification: evidence, seven-field correction, recalculation.
    - Processing errors: error context and retry.
    - Completed reviews: recorded decisions.
- Reporting
  - Reports & submission
    - Operational exports: JSON, CSV, PDF.
    - Evaluation submission: validate against the active dataset and download.

## Requirements alignment

Only BL_COMPARISON emails proceed to document comparison. Other categories are
classified, not labelled as having passed document verification. SI remains the
reference for all seven shipment fields. Unreliable extraction is escalated with
context; human corrections update the result. Submission validation uses the
dataset actually processed, including the version-tracking demo.

PDF/Word/scanned-document extraction remains an advanced capability gap; the UI
redesign does not claim to implement OCR or additional file readers.

## UI conventions

Parent labels organize the sidebar; indented child actions select pages. The
breadcrumb identifies the current group. Shared styles live in styles.css, with
compact headings, consistent cards, a teal primary action, and separate status
colours. Inbox lists paginate rather than silently truncating after 20 requests.

## Verification

56 existing and compatibility unit tests passed. Streamlit AppTest exercised
overview, empty review/export states, inbox navigation and processing of the
10-email demo with external integrations disabled. Desktop browser visual checks
covered overview and inbox entry layouts.
