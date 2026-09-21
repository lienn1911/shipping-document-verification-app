"""Build the Streamlit "Secrets" text for hosting from your local files. It never prints a secret.

Run from the web-app folder:
  python scripts/make_streamlit_secrets.py --copy    copy the secrets text to the clipboard (macOS), to paste into the host
  python scripts/make_streamlit_secrets.py --write   write ../.streamlit/secrets.toml (git-ignored) to rehearse the hosted setup locally

Reads GEMINI_API_KEY / GEMINI_MODEL / GEMINI_FALLBACK_MODELS from web-app/.env and the service-account JSON from
web-app/serviceAccountKey.json (or FIREBASE_SERVICE_ACCOUNT).
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

WEB_APP = Path(__file__).resolve().parents[1]
REPO = WEB_APP.parent


def toml_string(value: str) -> str:
    return json.dumps(value)  # a JSON string with ASCII escapes is a valid TOML basic string


def build_secrets(env: dict, service_account: dict) -> str:
    key = (env.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise ValueError("GEMINI_API_KEY is empty in web-app/.env")
    for field in ("private_key", "client_email", "project_id"):
        if not service_account.get(field):
            raise ValueError(f"The service-account JSON has no '{field}': is it the right file?")
    compact = json.dumps(service_account, separators=(",", ":"))
    if "'''" in compact:
        raise ValueError("Service-account JSON contains ''' and cannot be embedded")
    lines = [
        'GEMINI_AI_ENABLED = "true"',
        f"GEMINI_API_KEY = {toml_string(key)}",
        f'GEMINI_MODEL = {toml_string((env.get("GEMINI_MODEL") or "").strip())}',
    ]
    fallbacks = (env.get("GEMINI_FALLBACK_MODELS") or "").strip()
    if fallbacks:
        lines.append(f"GEMINI_FALLBACK_MODELS = {toml_string(fallbacks)}")
    lines += ['FIREBASE_ENABLED = "true"', f"FIREBASE_SERVICE_ACCOUNT_JSON = '''{compact}'''"]
    text = "\n".join(lines) + "\n"
    parsed = tomllib.loads(text)  # prove the host will read back exactly what we put in
    if parsed["GEMINI_API_KEY"] != key or json.loads(parsed["FIREBASE_SERVICE_ACCOUNT_JSON"]) != service_account:
        raise ValueError("Round-trip check failed; the secrets text would not be read back correctly")
    return text


def load_inputs() -> tuple[dict, dict]:
    from dotenv import dotenv_values

    env = dotenv_values(WEB_APP / ".env")
    account_path = Path(env.get("FIREBASE_SERVICE_ACCOUNT") or "serviceAccountKey.json").expanduser()
    if not account_path.is_absolute():
        account_path = WEB_APP / account_path
    if not account_path.is_file():
        raise ValueError(f"Service-account file not found: {account_path}")
    return env, json.loads(account_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--copy", action="store_true", help="copy to the clipboard (macOS pbcopy)")
    parser.add_argument("--write", action="store_true", help="write ../.streamlit/secrets.toml for a local rehearsal")
    args = parser.parse_args()
    if not (args.copy or args.write):
        parser.print_help()
        return 2
    try:
        text = build_secrets(*load_inputs())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("FAILED:", exc)
        return 1
    print(f"Built {len(text)} characters (contents are secret and are not shown). Round-trip check passed.")
    if args.copy:
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        except (OSError, subprocess.CalledProcessError):
            print("Could not use pbcopy. Use --write instead and copy from ../.streamlit/secrets.toml.")
            return 1
        print("Copied to the clipboard. Paste it into the host's Secrets box, then clear the clipboard with: pbcopy < /dev/null")
    if args.write:
        target = REPO / ".streamlit" / "secrets.toml"
        ignored = subprocess.run(["git", "check-ignore", "-q", str(target)], cwd=REPO).returncode == 0
        if not ignored:
            print(f"REFUSED: git does not ignore {target}. Nothing written.")
            return 1
        target.parent.mkdir(exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Wrote {target} (git-ignored, owner-only). Delete it when the rehearsal is done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
