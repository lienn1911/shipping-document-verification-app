"""The read-only Saved results page: query, tolerance for messy records, and the page itself. No network."""

import copy
import logging
import os
from pathlib import Path
import sys
import types
import unittest

logging.getLogger("streamlit").setLevel(logging.ERROR)  # bare-mode warnings when testing the page
WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))
os.environ.setdefault("FIREBASE_ENABLED", "false")
os.environ.setdefault("GEMINI_AI_ENABLED", "false")

import firebase_config  # noqa: E402
from src import cloud_records  # noqa: E402
from src.service import apply_human_review, process_dataset  # noqa: E402

DEMO = WEB_APP / "demo" / "versions-bundle"


class Snap:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    def to_dict(self):
        return copy.deepcopy(self._data)


class Query:
    def __init__(self, db):
        self.db = db

    def order_by(self, field, direction=None):
        self.db.calls.append(("order_by", field, direction))
        return self

    def limit(self, n):
        self.db.calls.append(("limit", n))
        return self

    def stream(self):
        return iter(self.db.snaps)


class FakeDb:
    def __init__(self, snaps):
        self.snaps, self.calls = snaps, []

    def collection(self, name):
        self.calls.append(("collection", name))
        return Query(self)


def demo_snaps():
    art = process_dataset(DEMO)
    extracted = copy.deepcopy(art.internal_results["email_904"]["extracted"])
    extracted["bl"]["gross_weight_kg"] = extracted["si"]["gross_weight_kg"]
    reviewed = apply_human_review(art, "email_904", extracted["si"], extracted["bl"], "confirmed with carrier")
    subject = {e["email_id"]: e["subject"] for e in art.emails}
    records = {
        k: cloud_records.audit_record(k, reviewed.submission[k], reviewed.internal_results[k], subject[k])
        for k in ("email_904", "email_901")
    }
    return [Snap(k, v) for k, v in records.items()]


@unittest.skipUnless((DEMO / "inbox").is_dir(), "demo bundle not present")
class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snaps = demo_snaps()

    def test_the_query_is_newest_first_and_capped(self):
        db = FakeDb(self.snaps)
        records = cloud_records.fetch_recent(db)
        self.assertEqual(db.calls, [("collection", "verification_results"), ("order_by", "saved_at", "DESCENDING"), ("limit", 50)])
        self.assertEqual([r["email_id"] for r in records], ["email_904", "email_901"])

    def test_rows_summary_and_review_changes(self):
        records = cloud_records.fetch_recent(FakeDb(self.snaps))
        rows = cloud_records.saved_rows(records)
        self.assertEqual(rows[0]["Email"], "email_904")
        self.assertEqual((rows[0]["Person reviewed"], rows[1]["Person reviewed"]), ("Yes", ""))
        summary = cloud_records.saved_summary(records)
        self.assertEqual((summary["records"], summary["reviewed"]), (2, 1))
        changes = cloud_records.review_changes_table(records[0])
        self.assertEqual([(c["Document"], c["Field"]) for c in changes], [("BL", "gross_weight_kg")])

    def test_old_and_odd_documents_never_break_the_page_logic(self):
        odd = [
            {"email_id": "old_1", "status": "OK", "human_review": "not a dict", "mismatches": "x"},
            {"email_id": "old_2", "human_review": {"decision": "corrected", "changed_fields": "oops"}},
            {},
        ]
        rows = cloud_records.saved_rows(odd)
        self.assertEqual(len(rows), 3)
        self.assertEqual(cloud_records.saved_summary(odd)["reviewed"], 1)
        self.assertEqual(cloud_records.review_changes_table(odd[1]), [])

    def test_a_missing_id_falls_back_to_the_document_id(self):
        records = cloud_records.fetch_recent(FakeDb([Snap("email_777", {"status": "OK"})]))
        self.assertEqual(records[0]["email_id"], "email_777")


@unittest.skipUnless((DEMO / "inbox").is_dir(), "demo bundle not present")
class PageTests(unittest.TestCase):
    def open_page(self, db):
        from streamlit.testing.v1 import AppTest

        original = firebase_config.db
        firebase_config.db = db
        self.addCleanup(setattr, firebase_config, "db", original)
        at = AppTest.from_file(str(WEB_APP / "app.py"), default_timeout=120).run()
        [b for b in at.sidebar.button if b.label == "Saved results"][0].click()
        return at.run()

    def test_records_are_listed_and_one_can_be_opened(self):
        at = self.open_page(FakeDb(demo_snaps()))
        self.assertEqual(len(at.exception), 0)
        self.assertEqual([m.value for m in at.metric][:2], ["2", "1"])  # records shown, mismatches
        self.assertEqual(len(at.dataframe[0].value), 2)
        self.assertTrue(any("cover only the 2 records shown" in c.value for c in at.caption))
        at.selectbox(key="saved_open").select("email_904").run()
        self.assertEqual(len(at.exception), 0)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("Human review decision", text)
        self.assertIn("confirmed with carrier", text)

    def test_no_firestore_shows_a_plain_message(self):
        at = self.open_page(None)
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("not configured" in i.value for i in at.info))

    def test_an_empty_collection_explains_what_to_do(self):
        at = self.open_page(FakeDb([]))
        self.assertTrue(any("No records written by this version yet" in i.value for i in at.info))

    def test_a_failing_read_is_reported_without_breaking_the_app(self):
        class Broken(FakeDb):
            def collection(self, name):
                raise ConnectionError("network down")

        at = self.open_page(Broken([]))
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("Could not read from Firestore (ConnectionError)" in w.value for w in at.warning))


if __name__ == "__main__":
    unittest.main()
