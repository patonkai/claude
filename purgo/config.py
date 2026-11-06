"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("PURGO_DATABASE_URL", "sqlite:///./purgo.db"))
    secret_key: str = field(default_factory=lambda: os.environ.get("PURGO_SECRET_KEY") or secrets.token_hex(32))
    brokers_file: Path = field(default_factory=lambda: Path(os.environ.get("PURGO_BROKERS_FILE", BASE_DIR / "brokers.yaml")))
    admin_email: str = field(default_factory=lambda: os.environ.get("PURGO_ADMIN_EMAIL", "").strip().lower())

    # Identity of the operator acting as the customer's authorised representative.
    operator_name: str = field(default_factory=lambda: os.environ.get("PURGO_OPERATOR_NAME", "Purgo Privacy Service"))
    operator_address: str = field(default_factory=lambda: os.environ.get("PURGO_OPERATOR_ADDRESS", ""))
    operator_email: str = field(default_factory=lambda: os.environ.get("PURGO_OPERATOR_EMAIL", "privacy@example.org"))

    # Outgoing mail. Without SMTP_HOST, messages are written to the outbox dir as .eml files.
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    smtp_starttls: bool = field(default_factory=lambda: os.environ.get("SMTP_STARTTLS", "1") != "0")
    outbox_dir: Path = field(default_factory=lambda: Path(os.environ.get("PURGO_OUTBOX_DIR", "./outbox")))

    # Deadlines. GDPR Art. 12(3): one month to respond.
    response_days: int = field(default_factory=lambda: _int("PURGO_RESPONSE_DAYS", 30))
    followup_grace_days: int = field(default_factory=lambda: _int("PURGO_FOLLOWUP_GRACE_DAYS", 14))
    rescan_days: int = field(default_factory=lambda: _int("PURGO_RESCAN_DAYS", 90))

    # Scanning
    scan_timeout: float = field(default_factory=lambda: float(os.environ.get("PURGO_SCAN_TIMEOUT", "15")))
    scan_user_agent: str = field(default_factory=lambda: os.environ.get("PURGO_SCAN_USER_AGENT", "Mozilla/5.0 (compatible; PurgoScanner/0.1)"))
    https_only_cookies: bool = field(default_factory=lambda: os.environ.get("PURGO_HTTPS_ONLY", "0") == "1")


settings = Settings()
