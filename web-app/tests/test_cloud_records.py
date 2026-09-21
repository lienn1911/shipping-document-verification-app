"""Firestore audit records: content, privacy and batched saving. No network."""

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))

from src import cloud_records  # noqa: E402
from src.service import apply_human_review, process_dataset  # noqa: E402

DEMO = WEB_APP / "demo" / "versions-bundle"


class FakeBatch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, record, merge=False):
        self.ops.append((ref, record, merge))

    def commit(self):
        self.db.commits.append(self.ops)


class FakeDb:
    def __init__(self):
        self.commits = []

    def batch(self):
        return FakeBatch(self)

    def collection(self, name):
        assert name == cloud_records.COLLECTION
        return pytypes_ns(document=lambda doc_id: f"{name}/{doc_id}")


def pytypes_ns(**kw):
    import types

    return types.SimpleNamespace(**kw)


@unittest.skipUnless((DEMO / "inbox").is_dir(), "demo bundle not present")
class AuditRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.art = process_dataset(DEMO)

    def record(self, email_id, art=None):
        art = art or self.art
        subject = next(e["subject"] for e in art.emails if e["email_id"] == email_id)
        return cloud_records.audit_record(email_id, art.submission[email_id], art.internal_results[email_id], subject)

    def test_a_mismatch_record_holds_what_is_needed(self):
        rec = self.record("email_904")
        self.assertEqual((rec["status"], rec["defect_fields"], rec["category"]), ("MISMATCH", ["gross_weight_kg"], "BL_COMPARISON"))
        self.assertEqual(rec["mismatches"][0]["si_value"], "131,058 KG")
        self.assertIn("gross_weight_kg", rec["extracted"]["bl"])
        self.assertEqual({d["type"] for d in rec["documents"]}, {"SI", "BL"})
        self.assertEqual((rec["version"]["version"], rec["version"]["is_latest"]), (3, True))
        self.assertFalse(rec["read_by_ai"])

    def test_no_email_body_or_sender_is_stored_and_it_is_plain_data(self):
        rec = self.record("email_904")
        text = json.dumps(rec)  # must be JSON-serialisable to be safe for Firestore
        email = next(e for e in self.art.emails if e["email_id"] == "email_904")
        self.assertNotIn(email["body"].strip()[:30], text)
        self.assertNotIn(email["from"], text)
        self.assertFalse({"body", "from", "sender", "email"} & set(rec))

    def test_human_review_decision_is_recorded_with_what_changed(self):
        extracted = copy.deepcopy(self.art.internal_results["email_904"]["extracted"])
        extracted["bl"]["gross_weight_kg"] = extracted["si"]["gross_weight_kg"]
        updated = apply_human_review(self.art, "email_904", extracted["si"], extracted["bl"], "confirmed with carrier")
        review = self.record("email_904", updated)["human_review"]
        self.assertEqual(review["note"], "confirmed with carrier")
        self.assertEqual([(c["document"], c["field"]) for c in review["changed_fields"]], [("BL", "gross_weight_kg")])
        self.assertNotIn("human_review", self.record("email_904"))  # the original artifacts are untouched

    def test_gemini_verdict_is_summarised_only_when_it_completed(self):
        detail = copy.deepcopy(self.art.internal_results["email_904"])
        detail["ai_analysis"] = {"status": "completed", "model": "m", "result": {"summary": "long text", "risk_level": "high"}}
        rec = cloud_records.audit_record("email_904", self.art.submission["email_904"], detail)
        self.assertEqual(rec["ai"], {"status": "completed", "risk_level": "high", "model": "m"})
        self.assertNotIn("long text", json.dumps(rec))
        detail["ai_analysis"] = {"status": "skipped", "reason": "batch"}
        self.assertNotIn("ai", cloud_records.audit_record("email_904", self.art.submission["email_904"], detail))

    def test_a_scan_read_by_vision_is_flagged(self):
        detail = copy.deepcopy(self.art.internal_results["email_904"])
        next(iter(detail["evidence"]["si"].values()))["read_method"] = "gemini_vision"
        self.assertTrue(cloud_records.audit_record("email_904", self.art.submission["email_904"], detail)["read_by_ai"])


@unittest.skipUnless((DEMO / "inbox").is_dir(), "demo bundle not present")
class SaveCasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.art = process_dataset(DEMO)

    def test_saves_every_case_in_batches_with_stable_ids_and_merge(self):
        db = FakeDb()
        with mock.patch.object(cloud_records, "BATCH_SIZE", 4):
            saved = cloud_records.save_cases(db, self.art)
        self.assertEqual(saved, len(self.art.submission))
        self.assertEqual([len(ops) for ops in db.commits], [4, 4, 2])
        refs = [ref for ops in db.commits for ref, _, _ in ops]
        self.assertEqual(sorted(refs), sorted(f"verification_results/{k}" for k in self.art.submission))
        self.assertTrue(all(merge for ops in db.commits for _, _, merge in ops))

    def test_only_the_requested_cases_are_saved(self):
        db = FakeDb()
        self.assertEqual(cloud_records.save_cases(db, self.art, ["email_904"]), 1)
        (ref, record, _), = db.commits[0]
        self.assertEqual((ref, record["email_id"]), ("verification_results/email_904", "email_904"))

    def test_subject_comes_from_the_email_record(self):
        db = FakeDb()
        cloud_records.save_cases(db, self.art, ["email_904"])
        self.assertIn("REQUEST BL DRAFT", db.commits[0][0][1]["subject"])


if __name__ == "__main__":
    unittest.main()
