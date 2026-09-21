"""The hosted-secrets helper: correct TOML round trip and no secret ever printed. No network."""

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
import unittest.mock

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP / "scripts"))
sys.path.insert(0, str(WEB_APP))

import make_streamlit_secrets as tool  # noqa: E402
from tests.test_cloud_config import fake_service_account  # noqa: E402


class BuildSecretsTests(unittest.TestCase):
    def setUp(self):
        self.account = fake_service_account()
        self.env = {"GEMINI_API_KEY": "AQ.fake-key_123", "GEMINI_MODEL": "", "GEMINI_FALLBACK_MODELS": "a,b"}

    def test_round_trips_through_toml_including_multiline_private_key(self):
        text = tool.build_secrets(self.env, self.account)
        parsed = tool.tomllib.loads(text)
        self.assertEqual(parsed["GEMINI_API_KEY"], "AQ.fake-key_123")
        self.assertEqual(parsed["GEMINI_MODEL"], "")
        self.assertEqual(parsed["GEMINI_FALLBACK_MODELS"], "a,b")
        self.assertEqual(json.loads(parsed["FIREBASE_SERVICE_ACCOUNT_JSON"]), self.account)
        self.assertIn("BEGIN PRIVATE KEY", self.account["private_key"])

    def test_the_app_can_use_the_result(self):
        import os
        from unittest import mock

        import firebase_config

        text = tool.build_secrets(self.env, self.account)
        parsed = tool.tomllib.loads(text)
        with mock.patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": parsed["FIREBASE_SERVICE_ACCOUNT_JSON"]}):
            self.assertEqual(firebase_config._inline_credentials(), self.account)

    def test_missing_or_wrong_inputs_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            tool.build_secrets({"GEMINI_API_KEY": " "}, self.account)
        with self.assertRaisesRegex(ValueError, "private_key"):
            tool.build_secrets(self.env, {"client_email": "x", "project_id": "y"})

    def test_a_quote_in_the_key_cannot_break_the_toml(self):
        text = tool.build_secrets({"GEMINI_API_KEY": 'has"quote\\and'}, self.account)
        self.assertEqual(tool.tomllib.loads(text)["GEMINI_API_KEY"], 'has"quote\\and')

    def test_running_with_no_flags_prints_help_and_never_secrets(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys, "argv", ["make_streamlit_secrets.py"]):
            code = tool.main()
        self.assertEqual(code, 2)
        self.assertNotIn("private_key", out.getvalue())


if __name__ == "__main__":
    unittest.main()
