"""One place that loads configuration: `.env` locally, Streamlit secrets when hosted."""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_KEYS = (
    "GEMINI_AI_ENABLED",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "FIREBASE_ENABLED",
    "FIREBASE_SERVICE_ACCOUNT",
    "FIREBASE_SERVICE_ACCOUNT_JSON",
)


def load_environment() -> None:
    """Populate os.environ from web-app/.env, then from Streamlit secrets.

    Real environment variables always win, then `.env`, then Streamlit secrets,
    so a hosted deployment needs no `.env` file and a local run needs no secrets.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).with_name(".env"))
    except ImportError:
        pass
    try:
        import streamlit as st

        for key in CONFIG_KEYS:
            if key not in os.environ and key in st.secrets:
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass  # no secrets configured (a normal local run)
