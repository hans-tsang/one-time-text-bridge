"""Application configuration loaded from environment variables.

Secrets and environment-specific settings must live in environment
variables (see .env.example). No secrets are hard-coded here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the app.

    ``environment`` controls whether we enforce HTTPS redirects and
    stricter startup validation. In production, the app refuses to
    start unless required configuration (e.g. ALLOWED_HOSTS) is set.
    """

    environment: str = field(default_factory=lambda: os.environ.get("ENVIRONMENT", "development"))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./data/app.db"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", "dev-insecure-secret-key-change-me"))
    allowed_hosts: list[str] = field(default_factory=lambda: _get_list("ALLOWED_HOSTS", ["localhost", "127.0.0.1"]))
    trusted_proxy_ips: list[str] = field(
        default_factory=lambda: _get_list("TRUSTED_PROXY_IPS", ["127.0.0.1"])
    )
    max_message_length: int = field(default_factory=lambda: _get_int("MAX_MESSAGE_LENGTH", 2000))
    max_upload_bytes: int = field(default_factory=lambda: _get_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
    cleanup_interval_seconds: int = field(default_factory=lambda: _get_int("CLEANUP_INTERVAL_SECONDS", 60))
    rate_limit_per_minute: int = field(default_factory=lambda: _get_int("RATE_LIMIT_PER_MINUTE", 20))
    base_url: str = field(default_factory=lambda: os.environ.get("BASE_URL", "http://localhost:8000"))
    # Optional extra layer: a shared passphrase required in addition to the
    # one-time link. Disabled by default; see README "Optional hardening".
    shared_passphrase: str | None = field(default_factory=lambda: os.environ.get("SHARED_PASSPHRASE") or None)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def validate_for_startup(self) -> None:
        """Refuse to start in production without required configuration."""
        if not self.is_production:
            return
        errors = []
        if not self.secret_key or self.secret_key == "dev-insecure-secret-key-change-me":
            errors.append("SECRET_KEY must be set to a strong random value in production")
        if not self.allowed_hosts or self.allowed_hosts == ["localhost", "127.0.0.1"]:
            errors.append("ALLOWED_HOSTS must be set to your production hostname(s)")
        if not self.base_url or self.base_url.startswith("http://localhost"):
            errors.append("BASE_URL must be set to your production https URL")
        if not self.base_url.startswith("https://"):
            errors.append("BASE_URL must use https:// in production")
        if errors:
            raise RuntimeError(
                "Refusing to start in production due to missing/invalid configuration: "
                + "; ".join(errors)
            )


settings = Settings()
