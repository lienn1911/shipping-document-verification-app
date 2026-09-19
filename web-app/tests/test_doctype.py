"""Tests for content-based document type detection. Run: python -m unittest discover -s tests"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.doctype import BL, INVOICE, SI, UNKNOWN, assign_roles, detect_document_type  # noqa: E402

SI_TXT = "SHIPPING INSTRUCTION\nShipper: ACME\n"
BL_TXT = "BILL OF LADING (DRAFT)\nShipper: ACME\n"


class DetectTypeTests(unittest.TestCase):
    def test_plain_titles(self):
        self.assertEqual(detect_document_type(SI_TXT).type, SI)
        self.assertEqual(detect_document_type(BL_TXT).type, BL)
        self.assertEqual(detect_document_type("COMMERCIAL INVOICE\nTotal: 10\n").type, INVOICE)

    def test_instruction_wins_over_bill_of_lading(self):
        # Some SI PDFs are titled "BILL OF LADING INSTRUCTION".
        det = detect_document_type("BILL OF LADING INSTRUCTION\nB/L NUMBER: X1\n")
        self.assertEqual(det.type, SI)

    def test_filename_never_decides(self):
        det = detect_document_type("PACKING LIST\nItem 1\n", filename="email_502_BL.txt")
        self.assertEqual(det.type, UNKNOWN)
        self.assertEqual(det.label, "Packing List")
        self.assertEqual(det.filename_hint, "BL")
        self.assertEqual(detect_document_type(SI_TXT, filename="whatever_BL.txt").type, SI)

    def test_other_known_documents_are_named(self):
        det = detect_document_type("CERTIFICATE OF ORIGIN\nExporter: X\n")
        self.assertEqual((det.type, det.label, det.reason), (UNKNOWN, "Certificate of Origin", "other_document"))

    def test_sentence_is_not_a_title(self):
        det = detect_document_type("Please see the attached shipping instruction for the booking.\n")
        self.assertEqual(det.type, UNKNOWN)
        self.assertEqual(det.reason, "no_title_found")

    def test_bare_invoice_needs_short_line(self):
        self.assertEqual(detect_document_type("INVOICE\nNo 1\n").type, INVOICE)
        self.assertEqual(detect_document_type("INVOICE NO: 555\nBILL OF LADING\n").type, BL)

    def test_modifiers_and_letterhead(self):
        self.assertEqual(detect_document_type("DRAFT BILL OF LADING\n").type, BL)
        det = detect_document_type("ACME PAPER TRADING PTE LTD\nBL INSTRUCTION | 3154\nSHIPPER | X\n")
        self.assertEqual(det.type, SI)

    def test_sheet_names(self):
        det = detect_document_type("ACME LTD\nSHIPPER | X\n", sheet_names=["S.I."])
        self.assertEqual(det.type, SI)
        det = detect_document_type("ACME LTD\nBILL OF LADING | 1\n", sheet_names=["BL"])
        self.assertEqual((det.type, det.confidence >= 0.98), (BL, True))

    def test_ocr_noise(self):
        merged = detect_document_type("BILLOF LADING (DRAFT)\nShipper: X\n")
        self.assertEqual(merged.type, BL)
        self.assertLess(merged.confidence, 0.98)  # noisy read is less certain
        self.assertEqual(detect_document_type("SHlPPING INSTRUCTlON\n").type, SI)

    def test_no_text_and_no_title(self):
        self.assertEqual(detect_document_type("").reason, "no_text")
        self.assertEqual(detect_document_type(None).reason, "no_text")
        self.assertEqual(detect_document_type("Shipper: X\nConsignee: Y\n").reason, "no_title_found")

    def test_conflicting_titles_are_not_guessed(self):
        det = detect_document_type("SHIPPING INSTRUCTION\nBILL OF LADING\nShipper: X\n")
        self.assertEqual(det.type, UNKNOWN)
        self.assertEqual(det.reason, "conflicting_titles")

    def test_worksheet_name_disagreeing_with_title_conflicts(self):
        det = detect_document_type("BILL OF LADING | 1\n", sheet_names=["S.I."])
        self.assertEqual(det.reason, "conflicting_titles")


class AssignRolesTests(unittest.TestCase):
    def test_normal_pair(self):
        res = assign_roles([("a_SI.txt", SI_TXT), ("a_BL.txt", BL_TXT)])
        self.assertIsNone(res.problem)
        self.assertEqual(res.roles, {"SI": "a_SI.txt", "BL": "a_BL.txt"})
        self.assertFalse(res.swapped)

    def test_swapped_uploads_are_corrected(self):
        res = assign_roles([("x_SI.txt", BL_TXT), ("x_BL.txt", SI_TXT)])
        self.assertIsNone(res.problem)
        self.assertEqual(res.roles, {"SI": "x_BL.txt", "BL": "x_SI.txt"})
        self.assertTrue(res.swapped)

    def test_arbitrary_filenames_work(self):
        res = assign_roles([("Final instruction v2.pdf", SI_TXT), ("MV Star draft.pdf", BL_TXT)])
        self.assertIsNone(res.problem)
        self.assertEqual(res.role_source, {"SI": "content", "BL": "content"})

    def test_invoice_in_bl_slot_is_named(self):
        res = assign_roles([("a_SI.txt", SI_TXT), ("a_BL.txt", "COMMERCIAL INVOICE\nTotal 5\n")])
        self.assertEqual(res.problem["review_reason"], "wrong_doc_type")
        self.assertIn("Commercial Invoice", res.problem["detail"])

    def test_extra_document_is_ignored_when_pair_is_complete(self):
        res = assign_roles([("a.txt", SI_TXT), ("b.txt", BL_TXT), ("c.txt", "PACKING LIST\nx\n")])
        self.assertIsNone(res.problem)
        self.assertEqual(res.detections["c.txt"]["label"], "Packing List")

    def test_missing_attachment(self):
        res = assign_roles([("a_SI.txt", SI_TXT)])
        self.assertEqual(res.problem["review_reason"], "missing_attachment")
        self.assertEqual(assign_roles([]).problem["review_reason"], "missing_attachment")

    def test_unreadable_file_falls_back_to_filename(self):
        res = assign_roles([("a_SI.txt", SI_TXT), ("a_BL.pdf", None)])
        self.assertIsNone(res.problem)
        self.assertEqual(res.role_source["BL"], "filename_fallback")

    def test_two_documents_of_same_type_need_review(self):
        res = assign_roles([("a.txt", BL_TXT), ("b.txt", BL_TXT), ("c.txt", SI_TXT)])
        self.assertEqual(res.problem["internal_reason"], "multiple_documents_same_type")

    def test_conflicting_titles_need_review(self):
        both = "SHIPPING INSTRUCTION\nBILL OF LADING\n"
        res = assign_roles([("a_SI.txt", both), ("a_BL.txt", BL_TXT)])
        self.assertEqual(res.problem["internal_reason"], "conflicting_document_titles")


if __name__ == "__main__":
    unittest.main()
