"""Gemini is on demand: single requests call it, full-dataset runs do not. No network."""

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))
os.environ.setdefault("FIREBASE_ENABLED", "false")

import ai_service  # noqa: E402
from src.service import UploadedAttachment, process_dataset, process_single_email  # noqa: E402

DEMO = WEB_APP / "demo" / "versions-bundle"
SAMPLE = WEB_APP.parent / "sample-upload"
FAKE_AI = {"enabled": True, "status": "completed", "result": {"summary": "s", "observations": []}}


class BatchSkipsGeminiTests(unittest.TestCase):
    def dataset(self, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=False), \
             mock.patch("env_loader.load_environment"), \
             mock.patch("src.pipeline._ai_cross_check", return_value=FAKE_AI) as ai:
            return process_dataset(DEMO), ai

    def test_default_batch_makes_no_gemini_calls(self):
        os.environ.pop("GEMINI_BATCH_ENABLED", None)
        artifacts, ai = self.dataset()
        ai.assert_not_called()
        compared = [d for d in artifacts.internal_results.values() if d.get("extracted")]
        self.assertGreater(len(compared), 3)
        for detail in compared:
            self.assertEqual(detail["ai_analysis"]["status"], "skipped")
            self.assertEqual(detail["ai_analysis"]["reason"], "batch")

    def test_opt_in_calls_once_per_compared_case(self):
        artifacts, ai = self.dataset({"GEMINI_BATCH_ENABLED": "true"})
        compared = [d for d in artifacts.internal_results.values() if d.get("extracted")]
        self.assertEqual(ai.call_count, len(compared))

    def test_skipping_ai_never_changes_the_scored_result(self):
        os.environ.pop("GEMINI_BATCH_ENABLED", None)
        off, _ = self.dataset()
        on, _ = self.dataset({"GEMINI_BATCH_ENABLED": "true"})
        self.assertEqual(off.submission, on.submission)

    def test_single_request_still_calls_gemini(self):
        import json

        email = json.loads((SAMPLE / "1-email.json").read_text(encoding="utf-8"))
        si = UploadedAttachment("si.txt", (SAMPLE / "2-shipping-instruction-SI.txt").read_bytes())
        bl = UploadedAttachment("bl.txt", (SAMPLE / "3-draft-bill-of-lading-BL.txt").read_bytes())
        with mock.patch("src.pipeline._ai_cross_check", return_value=FAKE_AI) as ai:
            artifacts = process_single_email(email, si, bl)
        self.assertEqual(ai.call_count, 1)
        detail = artifacts.internal_results[email["email_id"]]
        self.assertEqual(detail["ai_analysis"]["status"], "completed")


class BatchFlagTests(unittest.TestCase):
    def flag(self, value):
        with mock.patch.dict(os.environ, {"GEMINI_BATCH_ENABLED": value}), mock.patch("env_loader.load_environment"):
            return ai_service.batch_ai_enabled()

    def test_values(self):
        self.assertFalse(self.flag(""))
        self.assertFalse(self.flag("false"))
        self.assertTrue(self.flag("true"))
        self.assertTrue(self.flag(" YES "))


if __name__ == "__main__":
    unittest.main()
