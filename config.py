"""
config.py

Loads Warcraft Logs API credentials from a .env file in the project
directory, via python-dotenv. Falls back to already-set environment
variables if there's no .env file.

Setup:
    1. Copy .env.example to .env
    2. Fill in your WCL_CLIENT_ID and WCL_CLIENT_SECRET
    3. Never commit .env -- it's already in .gitignore
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # loads .env from the current directory if present; no-op otherwise


def get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret), or raise RuntimeError with a clear message if either is missing."""
    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing WCL_CLIENT_ID / WCL_CLIENT_SECRET. Copy .env.example to "
            ".env in this folder and fill in your Warcraft Logs API credentials."
        )
    return client_id, client_secret
