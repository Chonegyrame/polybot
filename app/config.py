"""Environment-driven configuration. No hardcoded paths or hostnames anywhere else."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    gamma_api_base: str = _str("GAMMA_API_BASE", "https://gamma-api.polymarket.com")
    data_api_base: str = _str("DATA_API_BASE", "https://data-api.polymarket.com")
    clob_api_base: str = _str("CLOB_API_BASE", "https://clob.polymarket.com")

    supabase_url: str = _str("SUPABASE_URL", "")
    supabase_key: str = _str("SUPABASE_KEY", "")
    database_url: str = _str("DATABASE_URL", "")

    resend_api_key: str = _str("RESEND_API_KEY", "")
    alert_email_to: str = _str("ALERT_EMAIL_TO", "")
    alert_email_from: str = _str("ALERT_EMAIL_FROM", "")

    log_level: str = _str("LOG_LEVEL", "INFO")
    # Pass 5 R17: lowered from 10.0 → 8.0 to leave 20% headroom for retries.
    # Combined with the per-host shared bucket (rate_limiter.get_bucket), the
    # process-wide ceiling stays comfortably under Polymarket's per-IP limit
    # even when retries fire after a 429.
    rate_limit_per_second: float = _float("RATE_LIMIT_PER_SECOND", 8.0)
    http_timeout_seconds: float = _float("HTTP_TIMEOUT_SECONDS", 30.0)


settings = Settings()
