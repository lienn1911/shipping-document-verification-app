"""Tests for duplicate and version tracking. Run: python -m unittest discover -s tests"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.doctype import assign_roles, is_marked_revised, revision_marker  # noqa: E402
from src.versions import (  # noqa: E402
    build_version_index,
    diff_versions,
    email_fingerprint,
    extract_identifiers,
    normalize_body,
    normalize_subject,
    refresh_changes,
    shipment_key,
    version_summary,
)

FIELDS7 = {
    "shipper": "ACME LTD", "consignee": "BUYER LTD", "notify_party": "BUYER LTD",
    "port_of_loading": "SINGAPORE", "port_of_discharge": "KOPER",
    "container_count": "3 x 20'GP", "gross_weight_kg": "22,000 KG",
}


def record(eid, index, subject="Draft BL X", body="hello", atts=(), date=None):
    return {"email_id": eid, "index": index, "date": date, "subject": subject, "body": body,
            "attachments": [{"path": p, "sha256": h, "text": t} for p, h, t in atts]}


def result(category="BL_COMPARISON", si=None, bl=None):
    return {"category": category, "extracted": {"si": si or FIELDS7, "bl": bl or FIELDS7}}


def doc(title, booking="BK1234567", extra=""):
    return f"{title}\nBooking No.: {booking}\nShipper: ACME\n{extra}"


class NormalisationTests(unittest.TestCase):
    def test_subject_prefixes_stripped(self):
        self.assertEqual(normalize_subject("RE_ RE: FW: Draft BL  X"), "draft bl x")

    def test_banner_removed_from_body(self):
        banner = "WARNING: This email originated outside of our organisation. Be careful.\n\nDear team, hi"
        self.assertEqual(normalize_body(banner), normalize_body("Dear team, hi"))

    def test_fingerprint_ignores_prefix_and_banner_but_not_attachments(self):
        a = record("a", 0, subject="Draft BL X", atts=[("x_SI.txt", "h1", "t")])
        b = record("b", 1, subject="RE_ Draft BL X", atts=[("y_SI.txt", "h1", "t")])
        c = record("c", 2, subject="Draft BL X", atts=[("z_SI.txt", "h2", "t")])
        self.assertEqual(email_fingerprint(a), email_fingerprint(b))
        self.assertNotEqual(email_fingerprint(a), email_fingerprint(c))


class IdentifierTests(unittest.TestCase):
    def test_txt_xlsx_and_pdf_layouts(self):
        self.assertEqual(extract_identifiers("Booking No.: MSDUL0942518196\nOC No.: 5RSG-00133")["booking_no"], "MSDUL0942518196")
        self.assertEqual(extract_identifiers("BOOKING NO. | PSGSE8148932")["booking_no"], "PSGSE8148932")
        ids = extract_identifiers("B/L NUMBER: OOLU3584143842        BOOKING NO. PSGSE4981829")
        self.assertEqual((ids["bl_no"], ids["booking_no"]), ("OOLU3584143842", "PSGSE4981829"))
        self.assertEqual(extract_identifiers("Booking Reference: ab-123456")["booking_no"], "AB-123456")

    def test_words_and_short_values_are_not_identifiers(self):
        self.assertEqual(extract_identifiers("Booking No.: TBA\nBL NO: 12\nBooking Reference: pending"), {})

    def test_key_priority(self):
        self.assertEqual(shipment_key([{"booking_no": "B1", "oc_no": "O1"}], FIELDS7), ("booking:B1", "booking_no"))
        self.assertEqual(shipment_key([{"oc_no": "O1"}], FIELDS7), ("oc:O1", "oc_no"))
        key, source = shipment_key([{}], FIELDS7)
        self.assertEqual((key.startswith("fields:"), source), (True, "si_fingerprint"))
        self.assertEqual(shipment_key([{}], None), (None, None))


class DuplicateTests(unittest.TestCase):
    def test_exact_repeats_grouped_and_first_kept(self):
        recs = [record("e1", 0, subject="Win now", body="spam"), record("e2", 1, subject="RE_ Win now", body="spam"),
                record("e3", 2, subject="Other", body="x")]
        res = {r["email_id"]: {"category": "SPAM"} for r in recs}
        idx = build_version_index(recs, res)
        self.assertEqual(idx["e1"]["duplicates"], ["e2"])
        self.assertEqual(idx["e2"]["duplicate_of"], "e1")
        self.assertIsNone(idx["e3"]["duplicate_of"])

    def test_same_subject_different_shipments_are_not_linked(self):
        recs = [record("a", 0, subject="Draft BL amend BL 041", atts=[("a_SI.txt", "h1", doc("SHIPPING INSTRUCTION", "BOOK0001")), ("a_BL.txt", "h2", doc("BILL OF LADING (DRAFT)", "BOOK0001"))]),
                record("b", 1, subject="Draft BL amend BL 041", body="different", atts=[("b_SI.txt", "h3", doc("SHIPPING INSTRUCTION", "BOOK0002")), ("b_BL.txt", "h4", doc("BILL OF LADING (DRAFT)", "BOOK0002"))])]
        idx = build_version_index(recs, {"a": result(), "b": result()})
        self.assertNotEqual(idx["a"]["shipment_key"], idx["b"]["shipment_key"])
        self.assertEqual((idx["a"]["version_count"], idx["b"]["version_count"]), (1, 1))


class VersionChainTests(unittest.TestCase):
    def chain(self, dates=None, markers=(None, None, None), bl_weights=("22,000 KG", "22,000 KG", "24,000 KG")):
        recs, res = [], {}
        for i, (eid, marker, weight) in enumerate(zip("abc", markers, bl_weights)):
            title = "BILL OF LADING (DRAFT)" + (f" REV {marker}" if marker else "")
            recs.append(record(eid, i, subject=f"Draft BL v{i}", body=f"body {i}", date=(dates[i] if dates else None),
                               atts=[(f"{eid}_SI.txt", f"si{i}", doc("SHIPPING INSTRUCTION")), (f"{eid}_BL.txt", f"bl{i}", doc(title))]))
            res[eid] = result(bl={**FIELDS7, "gross_weight_kg": weight})
        return recs, res

    def test_chain_latest_and_changes(self):
        recs, res = self.chain()
        idx = build_version_index(recs, res)
        self.assertEqual([idx[e]["version"] for e in "abc"], [1, 2, 3])
        self.assertTrue(idx["c"]["is_latest"] and not idx["a"]["is_latest"])
        self.assertEqual((idx["a"]["superseded_by"], idx["b"]["supersedes"]), ("b", "a"))
        self.assertEqual(idx["b"]["changes_from_previous"], [])  # resent, nothing changed
        change = idx["c"]["changes_from_previous"][0]
        self.assertEqual((change["document"], change["field"], change["previous"], change["current"]),
                         ("BL", "gross_weight_kg", "22,000 KG", "24,000 KG"))
        self.assertIsNone(idx["a"]["changes_from_previous"])
        self.assertEqual(idx["a"]["order_basis"], "arrival_order")

    def test_dates_beat_inbox_position(self):
        recs, res = self.chain(dates=[_d(3), _d(1), _d(2)])
        idx = build_version_index(recs, res)
        self.assertEqual(idx["a"]["chain"], ["b", "c", "a"])
        self.assertEqual(idx["a"]["order_basis"], "arrival_date")

    def test_explicit_revision_number_overrides_arrival(self):
        recs, res = self.chain(markers=(3, 1, 2))
        idx = build_version_index(recs, res)
        self.assertEqual(idx["a"]["chain"], ["b", "c", "a"])
        self.assertEqual((idx["a"]["order_basis"], idx["a"]["order_conflict"]), ("revision_marker", True))

    def test_repeat_of_a_version_is_not_a_new_version(self):
        recs, res = self.chain()
        recs.append({**recs[0], "email_id": "d", "index": 3})
        res["d"] = result()
        idx = build_version_index(recs, res)
        self.assertEqual(idx["d"]["duplicate_of"], "a")
        self.assertEqual(idx["a"]["version_count"], 3)

    def test_refresh_after_human_correction(self):
        recs, res = self.chain()
        idx = build_version_index(recs, res)
        for eid, info in idx.items():
            res[eid]["version_info"] = info
        res["c"]["extracted"]["bl"]["gross_weight_kg"] = "22,000 KG"  # reviewer corrects the weight
        refresh_changes(res)
        self.assertEqual(res["c"]["version_info"]["changes_from_previous"], [])

    def test_non_comparable_versions_report_none_not_empty(self):
        self.assertIsNone(diff_versions({}, {"bl": FIELDS7}))
        self.assertEqual(diff_versions({"bl": FIELDS7}, {"bl": FIELDS7}), [])

    def test_summary(self):
        recs, res = self.chain()
        for eid, info in build_version_index(recs, res).items():
            res[eid]["version_info"] = info
        s = version_summary(res)
        self.assertEqual((s["shipments_tracked"], s["shipments_with_revisions"], s["superseded_documents"]), (1, 1, 2))


def _d(day):
    from datetime import datetime, timezone
    return datetime(2026, 9, day, tzinfo=timezone.utc)


class RevisionMarkerTests(unittest.TestCase):
    def test_markers_from_title_and_filename(self):
        self.assertEqual(revision_marker("BILL OF LADING (DRAFT) REV 2\nx"), 2)
        self.assertEqual(revision_marker("BILL OF LADING\n", "BL_v3.pdf"), 3)
        self.assertEqual(revision_marker("SHIPPING INSTRUCTION - Amendment 1"), 1)

    def test_traps_are_not_revisions(self):
        self.assertIsNone(revision_marker("BILL OF LADING\nVessel: INDO SUKSES 65 V.51NW1\n", "email_004_BL.txt"))
        self.assertIsNone(revision_marker("Notes\nline two\nline three\nline four\nREV 9 in the body far below the title\n"))

    def test_revised_word(self):
        self.assertTrue(is_marked_revised("AMENDED BILL OF LADING\n"))
        self.assertFalse(is_marked_revised("BILL OF LADING (DRAFT)\n"))


class TwoRevisionsInOneEmailTests(unittest.TestCase):
    SI = "SHIPPING INSTRUCTION\nShipper: X\n"

    def test_latest_revision_chosen_and_older_reported(self):
        res = assign_roles([("d_SI.txt", self.SI), ("d_BL_v1.txt", "BILL OF LADING (DRAFT) REV 1\n"),
                            ("d_BL_v2.txt", "BILL OF LADING (DRAFT) REV 2\n")])
        self.assertIsNone(res.problem)
        self.assertEqual(res.roles["BL"], "d_BL_v2.txt")
        self.assertEqual(res.superseded, ["d_BL_v1.txt"])

    def test_unnumbered_duplicates_still_go_to_review(self):
        res = assign_roles([("a.txt", "BILL OF LADING (DRAFT)\n"), ("b.txt", "BILL OF LADING (DRAFT)\n"), ("c.txt", self.SI)])
        self.assertEqual(res.problem["internal_reason"], "multiple_documents_same_type")

    def test_same_revision_number_is_not_a_guess(self):
        res = assign_roles([("a.txt", "BILL OF LADING REV 2\n"), ("b.txt", "BILL OF LADING REV 2\n"), ("c.txt", self.SI)])
        self.assertIsNotNone(res.problem)


if __name__ == "__main__":
    unittest.main()
