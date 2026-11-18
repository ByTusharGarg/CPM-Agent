"""Cross-instance leases using Postgres session advisory locks.

When several API instances share one Postgres (CPM_MULTI_INSTANCE=1):
* exactly one instance runs a project's Router loop (lease "router", project_id);
* exactly one instance runs a given worker session (lease "worker", work_item_id).
A lease lives as long as the DB connection that took it, so if an instance dies
its leases are released automatically and another instance takes over.
With SQLite or a single instance every lease is granted locally (no-op).
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import text

from ..config import settings

log = logging.getLogger(__name__)
NAMESPACES = {"router": 1, "worker": 2}


class Leases:
    def __init__(self, engine):
        self.engine = engine
        self.enabled = settings.multi_instance and engine.dialect.name == "postgresql"
        self._held: dict[tuple[str, int], object] = {}
        self._lock = threading.Lock()

    def holds(self, ns: str, key: int) -> bool:
        return not self.enabled or (ns, key) in self._held

    def acquire(self, ns: str, key: int) -> bool:
        if not self.enabled:
            return True
        with self._lock:
            if (ns, key) in self._held:
                return True
            conn = self.engine.connect()
            try:
                ok = conn.execute(text("SELECT pg_try_advisory_lock(:a, :b)"), {"a": NAMESPACES[ns], "b": key}).scalar()
                conn.commit()
            except Exception:
                conn.close()
                raise
            if ok:
                self._held[(ns, key)] = conn
                return True
            conn.close()
            return False

    def release(self, ns: str, key: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            conn = self._held.pop((ns, key), None)
        if conn is not None:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:a, :b)"), {"a": NAMESPACES[ns], "b": key})
                conn.commit()
            finally:
                conn.close()

    def release_all(self) -> None:
        for ns, key in list(self._held):
            self.release(ns, key)
