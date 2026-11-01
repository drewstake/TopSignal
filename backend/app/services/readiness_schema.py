"""Cache only successful, static PostgreSQL schema inspection per engine.

Connectivity, migration ledger and bot admission checks remain uncached. Failed
inspection is never reused; the next request retries. Restarts start empty.
"""
from threading import Lock
from time import monotonic
from weakref import WeakKeyDictionary

from sqlalchemy.engine import Engine


class ReadinessSchemaCache:
    def __init__(self, ttl_seconds=300, clock=monotonic):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries = WeakKeyDictionary()
        self._lock = Lock()

    def get(self, bind, validate):
        # SQLite schemas can change within an offline workspace/test session.
        if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
            return validate()
        with self._lock:
            cached = self._entries.get(bind)
            if cached is not None and self.clock() < cached[0]:
                return cached[1]
            self._entries.pop(bind, None)
            tables = frozenset(validate())
            self._entries[bind] = (self.clock() + self.ttl_seconds, tables)
            return tables

    def invalidate(self, bind):
        if isinstance(bind, Engine):
            with self._lock:
                self._entries.pop(bind, None)


schema_cache = ReadinessSchemaCache()
