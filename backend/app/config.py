"""Runtime configuration. Override any value with an environment variable of the same name."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Read backend/.env (KEY=VALUE lines). Real environment variables win."""
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file() or os.environ.get("CPM_IGNORE_DOTENV") == "1":
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = _env("CPM_DATABASE_URL", "sqlite:///./cpm.db")

    # Model per role. All default to Claude Opus 5.5; switch a role to a cheaper
    # model (e.g. "claude-sonnet-5-5") here if the owner decides to.
    router_model: str = _env("CPM_ROUTER_MODEL", "claude-opus-5-5")
    worker_model: str = _env("CPM_WORKER_MODEL", "claude-opus-5-5")
    watchdog_model: str = _env("CPM_WATCHDOG_MODEL", "claude-opus-5-5")

    router_effort: str = _env("CPM_ROUTER_EFFORT", "medium")
    worker_effort: str = _env("CPM_WORKER_EFFORT", "high")

    # Use the stub LLM (no API calls) - handy for UI development and tests.
    use_stub_llm: bool = _env("CPM_STUB_LLM", "0") == "1"

    # LLM provider: "anthropic" (default) | "ollama" (local model for offline testing) | "stub"
    llm_provider: str = _env("CPM_LLM_PROVIDER", "anthropic")
    ollama_model: str = _env("CPM_OLLAMA_MODEL", "qwen3:8b")
    ollama_url: str = _env("CPM_OLLAMA_URL", "http://localhost:11434")
    ollama_num_ctx: int = int(_env("CPM_OLLAMA_NUM_CTX", "12288"))
    ollama_think: str = _env("CPM_OLLAMA_THINK", "off")  # off | on | auto - thinking is slow on laptop GPUs

    # Worker mandate defaults
    worker_max_turns: int = int(_env("CPM_WORKER_MAX_TURNS", "12"))
    router_max_turns: int = int(_env("CPM_ROUTER_MAX_TURNS", "10"))

    # Watchdog: a running worker with no progress for this long is considered stuck.
    watchdog_stuck_minutes: int = int(_env("CPM_WATCHDOG_STUCK_MINUTES", "10"))
    watchdog_interval_minutes: int = int(_env("CPM_WATCHDOG_INTERVAL_MINUTES", "5"))

    # Scheduler (local server time)
    daily_check_hour: int = int(_env("CPM_DAILY_CHECK_HOUR", "7"))
    weekly_report_day: str = _env("CPM_WEEKLY_REPORT_DAY", "mon")
    enable_scheduler: bool = _env("CPM_ENABLE_SCHEDULER", "1") == "1"

    # Optional fixed "today" for demos/tests (YYYY-MM-DD). Empty = real date.
    as_of_date: str = _env("CPM_AS_OF_DATE", "")

    # --- auth
    secret_key: str = _env("CPM_SECRET_KEY", "dev-insecure-change-me")
    # Dev only: return the login code in the API response (and log it) instead of
    # requiring a working mail server. NEVER enable in production.
    dev_show_otp: bool = _env("CPM_DEV_SHOW_OTP", "0") == "1"
    cookie_secure: bool = _env("CPM_COOKIE_SECURE", "0") == "1"
    app_base_url: str = _env("CPM_APP_URL", "http://localhost:3010")

    # --- email (SMTP). Empty host = messages are written to the outbox only.
    smtp_host: str = _env("CPM_SMTP_HOST", "")
    smtp_port: int = int(_env("CPM_SMTP_PORT", "587"))
    smtp_user: str = _env("CPM_SMTP_USER", "")
    smtp_password: str = _env("CPM_SMTP_PASSWORD", "")
    smtp_from: str = _env("CPM_SMTP_FROM", "CPM Agent <no-reply@localhost>")

    # --- inbound email (SendGrid Inbound Parse / Mailgun routes post to /api/channels/email/inbound/<secret>)
    email_inbound_secret: str = _env("CPM_EMAIL_INBOUND_SECRET", "")
    public_api_url: str = _env("CPM_PUBLIC_API_URL", "http://localhost:8010")  # used in signed email links

    # --- WhatsApp Cloud API (Meta)
    wa_token: str = _env("CPM_WA_TOKEN", "")
    wa_phone_number_id: str = _env("CPM_WA_PHONE_NUMBER_ID", "")
    wa_verify_token: str = _env("CPM_WA_VERIFY_TOKEN", "")
    wa_app_secret: str = _env("CPM_WA_APP_SECRET", "")
    wa_api_base: str = _env("CPM_WA_API_BASE", "https://graph.facebook.com/v21.0")
    # Approved template used to reach someone outside the 24h customer-service window
    # (one body variable: the alert title). Empty = such messages wait for the user to write first.
    wa_alert_template: str = _env("CPM_WA_ALERT_TEMPLATE", "")
    wa_template_lang: str = _env("CPM_WA_TEMPLATE_LANG", "en")

    # --- voice notes: "none" | "faster_whisper" (local, pip install faster-whisper)
    transcriber: str = _env("CPM_TRANSCRIBER", "none")

    # --- file storage: "local" (dir) or "s3" (bucket, S3-compatible)
    storage_backend: str = _env("CPM_STORAGE", "local")
    storage_dir: str = _env("CPM_STORAGE_DIR", "./storage")
    s3_bucket: str = _env("CPM_S3_BUCKET", "")
    s3_endpoint: str = _env("CPM_S3_ENDPOINT", "")

    # --- running several API instances against one Postgres
    multi_instance: bool = _env("CPM_MULTI_INSTANCE", "0") == "1"
    auto_migrate: bool = _env("CPM_AUTO_MIGRATE", "1") == "1"

    cors_origins: list[str] = field(
        default_factory=lambda: _env("CPM_CORS_ORIGINS", "http://localhost:3000").split(",")
    )


settings = Settings()
