import os

os.environ["CPM_IGNORE_DOTENV"] = "1"  # tests never read a developer .env
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp()) / "test.db"
# Run the whole suite on Postgres with: CPM_TEST_DB_URL=postgresql+psycopg://cpm:cpm_dev@localhost/cpm_test
os.environ["CPM_DATABASE_URL"] = os.environ.get("CPM_TEST_DB_URL") or f"sqlite:///{_tmp.as_posix()}"
os.environ["CPM_ENABLE_SCHEDULER"] = "0"
os.environ["CPM_AS_OF_DATE"] = "2026-11-18"
os.environ["CPM_DEV_SHOW_OTP"] = "1"
os.environ["CPM_LLM_PROVIDER"] = "stub"
os.environ["CPM_STORAGE_DIR"] = str(_tmp.parent / "storage")
os.environ.update({  # phase 8 channels - talk to fakes in tests
    "CPM_WA_TOKEN": "test-token", "CPM_WA_PHONE_NUMBER_ID": "PNID", "CPM_WA_APP_SECRET": "app-secret",
    "CPM_WA_VERIFY_TOKEN": "verify-me", "CPM_WA_ALERT_TEMPLATE": "cpm_alert", "CPM_EMAIL_INBOUND_SECRET": "inbound-secret",
    "CPM_SMTP_HOST": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.harness.llm import LLMResponse, text_response  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    from sqlalchemy import text

    SQLModel.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    init_db()
    ids = seed()
    yield ids


class RoleLLM:
    """Scripted LLM that picks a script by agent role (router vs each worker),
    so concurrent agents get deterministic responses."""

    ROLE_MARKERS = {
        "router": "PROJECT MANAGER",
        "site_updates": "SITE UPDATES OFFICER",
        "procurement": "PROCUREMENT MANAGER",
        "planner": "You are the PLANNER",
        "design": "DESIGN COORDINATOR",
        "cost": "COST CONTROLLER",
        "reporter": "status report",
        "watchdog": "You supervise background AI workers",
    }

    def __init__(self, scripts=None):
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.calls = []
        self.gates = {}  # role -> asyncio.Event to block on

    def role_of(self, system: str) -> str:
        for role, marker in self.ROLE_MARKERS.items():
            if marker in system:
                return role
        return "unknown"

    async def complete(self, *, model, system, messages, tools, effort="medium", on_text=None) -> LLMResponse:
        role = self.role_of(system)
        self.calls.append({"role": role, "messages": messages, "n_messages": len(messages), "tools": [t["name"] for t in tools]})
        if role in self.gates:
            await self.gates[role].wait()
        script = self.scripts.get(role) or []
        item = script.pop(0) if script else text_response(f"{role} done")
        resp = item({"messages": messages}) if callable(item) else item
        if on_text and resp.text:
            await on_text(resp.text)
        return resp
