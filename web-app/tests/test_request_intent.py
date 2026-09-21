"""Emails that ask for a document to be SENT are not escalated; genuinely missing attachments still are. No network."""

from pathlib import Path
import json
import sys
import unittest

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))

from src import dashboard  # noqa: E402
from src.request_intent import asks_for_document_to_be_sent, message_text  # noqa: E402
from src.service import process_dataset  # noqa: E402

BUNDLE = WEB_APP.parent / "local-data" / "participant-bundle"
BANNER = ("WARNING: This email originated outside of our organisation. Please exercise caution with E-Mail content "
          "and any links or attachments.\n\n")


def mail(body, subject="Draft BL X"):
    return {"subject": subject, "body": body}


class IntentTests(unittest.TestCase):
    def test_a_request_to_send_the_draft_bl_has_nothing_to_compare(self):
        self.assertTrue(asks_for_document_to_be_sent(mail("Dear Syed,\nPlease assist to send the draft BL for MCL123 for checking asap. Thank you.")))
        self.assertTrue(asks_for_document_to_be_sent(mail("Kindly provide the draft bill of lading for booking 5RVN-06271.")))

    def test_the_gateway_banner_never_counts_as_attachment_language(self):
        email = mail(BANNER + "Dear team,\nPlease assist to send the draft BL for MCL123 for checking asap.")
        self.assertNotIn("attachments", message_text(email).lower())
        self.assertTrue(asks_for_document_to_be_sent(email))

    def test_a_comparison_request_with_missing_attachments_is_still_an_escalation(self):
        for body in (
            "Please compare the SI and draft BL for 070500263211 and confirm (attachments appear to have been dropped).",
            "Please compare the SI and draft BL for I756178688 and confirm (the draft BL is still missing).",
            "Please send the draft BL, I forgot the attachment.",
            "Please send the draft BL and compare it with the SI.",
        ):
            self.assertFalse(asks_for_document_to_be_sent(mail(body)), body)

    def test_anything_unclear_stays_an_escalation(self):
        for body in ("", "Hello", "Any update on this shipment?", "Please confirm the vessel schedule."):
            self.assertFalse(asks_for_document_to_be_sent(mail(body)), body)


@unittest.skipUnless((BUNDLE / "inbox").is_dir(), "participant bundle not present")
class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.art = process_dataset(BUNDLE)

    def test_only_genuinely_problematic_emails_are_escalated(self):
        reasons = [r["review_reason"] for r in self.art.submission.values() if r["status"] == "NEEDS_REVIEW"]
        self.assertEqual(sorted(reasons), ["missing_attachment"] * 5 + ["missing_value"] * 5 + ["unreadable"] * 5 + ["wrong_doc_type"] * 5)

    def test_requests_to_send_a_document_are_ok_and_not_counted_as_verified(self):
        no_docs = [k for k, d in self.art.internal_results.items() if d.get("internal_reason") == "no_documents_expected"]
        self.assertEqual(len(no_docs), 91)
        for email_id in no_docs:
            record = self.art.submission[email_id]
            self.assertEqual((record["category"], record["status"], record["review_reason"], record["defect_fields"]), ("BL_COMPARISON", "OK", None, []))
        summary = self.art.summary
        self.assertEqual(summary["comparison_requests"], 129)  # 220 document-check emails minus the 91 with nothing to compare
        self.assertEqual((summary["comparisons_completed"], summary["no_mismatch"], summary["mismatch"]), (109, 63, 46))

    def test_emails_that_say_an_attachment_is_missing_are_still_escalated(self):
        for email_id in ("email_506", "email_507", "email_508", "email_509", "email_510"):
            self.assertEqual(self.art.submission[email_id]["review_reason"], "missing_attachment", email_id)

    def test_the_dashboard_does_not_count_them_as_comparisons(self):
        records = dashboard.normalized_inbox_records(self.art.emails, self.art.submission, self.art.internal_results)
        stats = dashboard.useDashboardStats(records)
        self.assertEqual(stats["comparison_cases"], 129)
        no_doc = next(r for r in records if r.get("internal_reason") == "no_documents_expected")
        self.assertFalse(dashboard._is_comparison(no_doc))


if __name__ == "__main__":
    unittest.main()
