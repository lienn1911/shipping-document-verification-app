"""Gemini calls: backup models on overload, no retries on bad credentials, friendly messages. No network."""

import json
import os
from pathlib import Path
import sys
import types as pytypes
import unittest
from unittest import mock

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))
os.environ.setdefault("FIREBASE_ENABLED", "false")

import ai_service  # noqa: E402

GOOD = json.dumps({"summary": "Weights differ by 1,000 KG.", "risk_level": "high", "observations": ["weight"]})
OVERLOAD = Exception("503 UNAVAILABLE. {'error': {'message': 'This model is currently experiencing high demand.'}}")
NOT_FOUND = Exception("404 NOT_FOUND. model is not found")
DENIED = Exception("403 PERMISSION_DENIED. API_KEY_INVALID")
DAILY = Exception("429 RESOURCE_EXHAUSTED. Quota exceeded for metric ... PerDay")


class FakeClient:
    plan: dict = {}
    calls: list = []

    def __init__(self, api_key=None, http_options=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def models(self):
        return self

    def generate_content(self, model, contents, config=None):
        FakeClient.calls.append(model)
        outcome = FakeClient.plan.get(model, GOOD)
        if isinstance(outcome, Exception):
            raise outcome
        return pytypes.SimpleNamespace(text=outcome)


class GeminiResilienceTests(unittest.TestCase):
    ENV = {"GEMINI_AI_ENABLED": "true", "GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "main-model",
           "GEMINI_FALLBACK_MODELS": "backup-a,backup-b"}

    def setUp(self):
        FakeClient.plan, FakeClient.calls = {}, []
        ai_service._success_cache.clear()
        ai_service._quota_until.clear()
        patches = [
            mock.patch.dict(os.environ, self.ENV),
            mock.patch("env_loader.load_environment"),
            mock.patch("google.genai.Client", FakeClient),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_analysis(self, tag="x"):
        return ai_service.analyze_shipping_document({"si": {"shipper": tag}, "bl": {"shipper": tag}})

    def test_main_model_success_uses_no_backup(self):
        result = self.run_analysis()
        self.assertEqual((result["status"], result["model"], result["fallback_used"]), ("completed", "main-model", False))
        self.assertEqual(FakeClient.calls, ["main-model"])

    def test_overload_falls_back_and_says_so(self):
        FakeClient.plan = {"main-model": OVERLOAD}
        result = self.run_analysis()
        self.assertEqual((result["status"], result["model"], result["fallback_used"]), ("completed", "backup-a", True))
        self.assertEqual([a["outcome"] for a in result["attempts"]], ["overloaded", "ok"])
        self.assertEqual(result["result"]["risk_level"], "high")

    def test_missing_model_is_skipped(self):
        FakeClient.plan = {"main-model": NOT_FOUND, "backup-a": OVERLOAD}
        result = self.run_analysis()
        self.assertEqual(result["model"], "backup-b")
        self.assertEqual([a["outcome"] for a in result["attempts"]], ["model_unavailable", "overloaded", "ok"])

    def test_invalid_json_tries_the_next_model(self):
        FakeClient.plan = {"main-model": "not json at all"}
        self.assertEqual(self.run_analysis()["model"], "backup-a")

    def test_all_models_overloaded_gives_a_friendly_retryable_error(self):
        FakeClient.plan = {m: OVERLOAD for m in ("main-model", "backup-a", "backup-b")}
        result = self.run_analysis()
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["retryable"])
        self.assertIn("temporarily overloaded", result["error"])
        self.assertNotIn("503", result["error"])
        self.assertEqual(len(result["attempts"]), 3)

    def test_bad_credentials_stop_immediately(self):
        FakeClient.plan = {"main-model": DENIED}
        result = self.run_analysis()
        self.assertEqual(FakeClient.calls, ["main-model"])
        self.assertIn("credentials", result["error"])

    def test_daily_quota_stops_and_pauses_later_calls(self):
        FakeClient.plan = {"main-model": DAILY}
        first = self.run_analysis("a")
        self.assertEqual(first["status"], "quota_exhausted")
        self.assertEqual(FakeClient.calls, ["main-model"])
        FakeClient.calls.clear()
        second = self.run_analysis("b")
        self.assertEqual(second["status"], "quota_exhausted")
        self.assertEqual(FakeClient.calls, [])  # paused, no new request

    def test_success_is_cached(self):
        self.run_analysis()
        FakeClient.calls.clear()
        again = self.run_analysis()
        self.assertTrue(again["cached"])
        self.assertEqual(FakeClient.calls, [])


class FallbackListTests(unittest.TestCase):
    def names(self, value, primary="main"):
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": value}):
            return ai_service.fallback_models(primary)

    def test_blank_uses_defaults(self):
        self.assertEqual(self.names(""), ai_service.DEFAULT_FALLBACKS.split(","))

    def test_none_disables(self):
        self.assertEqual(self.names("none"), [])
        self.assertEqual(self.names(" NONE "), [])

    def test_list_is_deduplicated_and_excludes_primary(self):
        self.assertEqual(self.names("main, a, a ,b"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
