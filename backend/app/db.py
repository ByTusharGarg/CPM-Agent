from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def init_db(eng=None) -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(eng or engine)


@contextmanager
def session_scope(eng=None) -> Iterator[Session]:
    with Session(eng or engine, expire_on_commit=False) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    if settings.as_of_date:
        return date.fromisoformat(settings.as_of_date)
    return date.today()


def migrate(eng=None) -> None:
    """Bring the schema to the latest Alembic revision.

    A database created earlier with create_all (no alembic_version table) is
    stamped first so its existing tables are not re-created.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    eng = eng or engine
    if not settings.auto_migrate:
        return
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        if tables and "alembic_version" not in tables:
            if "project" in tables and "user" not in tables:
                # pre-phase-7 dev DB: add the new tables, then mark as current
                from . import models  # noqa: F401
                SQLModel.metadata.create_all(conn)
            command.stamp(cfg, "head")
        else:
            command.upgrade(cfg, "head")
