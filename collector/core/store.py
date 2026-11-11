from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


class Store:
    """The one SQLite file. Every call to connect() returns a new connection, so threads never share one."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def ensure(self, name: str, records: list[str]) -> None:
        """Run a component's CREATE statements, but only if every one makes a table or index named <name>_…"""
        own = re.compile(rf"\s*CREATE\s+(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+{re.escape(name)}_", re.I)
        for sql in records:
            if not own.match(sql):
                raise ValueError(f"component {name!r} may only create tables and indexes named {name}_…: {sql!r}")
        with closing(self.connect()) as db, db:
            for sql in records:
                db.execute(sql)
