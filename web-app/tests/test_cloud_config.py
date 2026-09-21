"""Hosting-related configuration: credentials from env/secrets, safe errors, Gemini model default."""

import base64
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

WEB_APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_APP))
os.environ.setdefault("FIREBASE_ENABLED", "false")  # importing firebase_config must never touch the network

import ai_service  # noqa: E402
import env_loader  # noqa: E402
import firebase_config  # noqa: E402


def fake_service_account() -> dict:
    """A structurally valid service account built from a throwaway key (no real secret)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account", "project_id": "demo-project", "private_key_id": "abc123",
        "private_key": pem, "client_email": "demo@demo-project.iam.gserviceaccount.com",
        "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token",
    }


class FirebaseCredentialTests(unittest.TestCase):
    def setUp(self):
        self.account = fake_service_account()
        firebase_config._error = None

    def connect(self, env: dict):
        base = {"FIREBASE_ENABLED": "true", "FIREBASE_SERVICE_ACCOUNT": str(WEB_APP / "does-not-exist.json")}
        with mock.patch.dict(os.environ, {**base, **env}), \
             mock.patch("firebase_admin._apps", {}), \
             mock.patch("firebase_admin.initialize_app") as init, \
             mock.patch("firebase_admin.firestore.client", return_value="CLIENT"):
            return firebase_config.get_firestore_client(), init

    def test_raw_json_from_settings_connects(self):
        client, init = self.connect({"FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps(self.account)})
        self.assertEqual(client, "CLIENT")
        init.assert_called_once()
        self.assertIsNone(firebase_config._error)

    def test_base64_json_connects(self):
        encoded = base64.b64encode(json.dumps(self.account).encode()).decode()
        client, _ = self.connect({"FIREBASE_SERVICE_ACCOUNT_JSON": encoded})
        self.assertEqual(client, "CLIENT")

    def test_invalid_json_is_reported_without_echoing_it(self):
        client, init = self.connect({"FIREBASE_SERVICE_ACCOUNT_JSON": "{not-json-SECRETTEXT"})
        self.assertIsNone(client)
        init.assert_not_called()
        self.assertIn("not valid", firebase_config._error)
        self.assertNotIn("SECRETTEXT", firebase_config._error)

    def test_credential_errors_never_leak_material(self):
        with mock.patch("firebase_admin.credentials.Certificate", side_effect=ValueError("private-key-material-XYZ")):
            client, _ = self.connect({"FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps(self.account)})
        self.assertIsNone(client)
        self.assertEqual(firebase_config._error, "Firebase rejected the credentials")

    def test_disabled_flag_wins(self):
        client, init = self.connect({"FIREBASE_ENABLED": "false", "FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps(self.account)})
        self.assertIsNone(client)
        init.assert_not_called()

    def test_missing_credentials_report_and_status(self):
        client, _ = self.connect({"FIREBASE_SERVICE_ACCOUNT_JSON": ""})
        self.assertIsNone(client)
        self.assertIn("not found", firebase_config._error)
        with mock.patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps(self.account)}):
            self.assertTrue(firebase_config.integration_status()["configured"])

    def test_generated_account_is_accepted_by_the_real_parser(self):
        from firebase_admin import credentials

        credentials.Certificate(self.account)  # raises if our parsed dict were unusable


class GeminiConfigTests(unittest.TestCase):
    def status(self, env):
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("env_loader.load_environment"):
            return ai_service.integration_status()

    def test_blank_model_uses_default(self):
        self.assertEqual(self.status({"GEMINI_MODEL": ""})["model"], ai_service.DEFAULT_MODEL)
        self.assertEqual(self.status({"GEMINI_MODEL": "  "})["model"], ai_service.DEFAULT_MODEL)
        self.assertEqual(self.status({"GEMINI_MODEL": "my-model"})["model"], "my-model")

    def test_status_states(self):
        self.assertEqual(self.status({"GEMINI_AI_ENABLED": "false", "GEMINI_API_KEY": "k"})["status"], "disabled")
        self.assertEqual(self.status({"GEMINI_AI_ENABLED": "true", "GEMINI_API_KEY": ""})["status"], "missing_api_key")
        self.assertEqual(self.status({"GEMINI_AI_ENABLED": "true", "GEMINI_API_KEY": "k"})["status"], "ready")


class EnvLoaderTests(unittest.TestCase):
    def test_streamlit_secrets_fill_gaps_but_never_override(self):
        import streamlit

        secrets = {"GEMINI_API_KEY": "from-secrets", "GEMINI_MODEL": "secret-model"}
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": "from-env"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            with mock.patch.object(streamlit, "secrets", secrets):
                env_loader.load_environment()
            self.assertEqual(os.environ["GEMINI_API_KEY"], "from-secrets")
            self.assertEqual(os.environ["GEMINI_MODEL"], "from-env")
            os.environ.pop("GEMINI_API_KEY", None)

    def test_missing_secrets_file_is_not_an_error(self):
        env_loader.load_environment()  # must not raise when no secrets are configured


if __name__ == "__main__":
    unittest.main()
