"""End-to-end checks of duplicate/version tracking on the generated demo bundle."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))

from src.service import apply_human_review, process_dataset  # noqa: E402

DEMO = WEB_APP / "demo" / "versions-bundle"


@unittest.skipUnless((DEMO / "inbox").is_dir(), "run tools/make_version_demo.py first")
class VersionDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.art = process_dataset(DEMO)
        cls.info = {k: v["version_info"] for k, v in cls.art.internal_results.items()}

    def status(self, eid):
        r = self.art.submission[eid]
        return r["status"], r["defect_fields"]

    def test_shipment_a_chain(self):
        self.assertEqual(self.info["email_901"]["chain"], ["email_901", "email_903", "email_904"])
        self.assertEqual(self.status("email_901"), ("MISMATCH", ["consignee", "notify_party"]))
        self.assertEqual(self.status("email_903"), ("OK", []))
        self.assertEqual(self.status("email_904"), ("MISMATCH", ["gross_weight_kg"]))
        self.assertTrue(self.info["email_904"]["is_latest"])
        self.assertEqual(self.info["email_901"]["superseded_by"], "email_903")
        self.assertEqual(self.info["email_904"]["order_basis"], "arrival_date")

    def test_exact_resend_is_a_repeat_not_a_version(self):
        self.assertEqual(self.info["email_902"]["duplicate_of"], "email_901")
        self.assertEqual(self.info["email_901"]["duplicates"], ["email_902"])
        self.assertEqual(self.info["email_901"]["version_count"], 3)

    def test_same_subject_other_shipment_is_not_linked(self):
        self.assertEqual(self.info["email_905"]["chain"], ["email_905"])

    def test_amended_si_is_tracked_on_the_si_side(self):
        changes = self.info["email_907"]["changes_from_previous"]
        self.assertEqual([(c["document"], c["field"]) for c in changes], [("SI", "port_of_discharge")])

    def test_two_bl_revisions_in_one_email_uses_the_newer(self):
        analysis = self.art.internal_results["email_908"]["document_analysis"]
        self.assertEqual(analysis["superseded"], ["attachments/email_908_BL_v1.txt"])
        self.assertIn("gross_weight_kg", self.art.submission["email_908"]["defect_fields"])

    def test_repeated_spam(self):
        self.assertEqual(self.info["email_910"]["duplicate_of"], "email_909")

    def test_human_correction_refreshes_changes(self):
        ex = deepcopy(self.art.internal_results["email_904"]["extracted"])
        ex["bl"]["gross_weight_kg"] = ex["si"]["gross_weight_kg"]
        updated = apply_human_review(self.art, "email_904", ex["si"], ex["bl"])
        self.assertEqual(updated.internal_results["email_904"]["version_info"]["changes_from_previous"], [])
        # the original artifacts are untouched
        self.assertTrue(self.art.internal_results["email_904"]["version_info"]["changes_from_previous"])


if __name__ == "__main__":
    unittest.main()
