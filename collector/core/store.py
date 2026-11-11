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

    def ensure(self, name: str, records: list[str], changes: list[list[str]] = ()) -> None:
        """Bring a component's tables to the shape of `records`, in one transaction.

        `records` is the present shape. `changes` are the steps that bring an older store to it; the
        version of the component in core_schema says how many of them the store has had."""
        n = re.escape(name)
        make = re.compile(
            rf"\s*(CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{n}_|CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{n}_\w*\s+ON\s+{n}_)", re.I)
        alter = re.compile(
            rf"\s*(ALTER\s+TABLE\s+{n}_|DROP\s+(TABLE|INDEX)\s+IF\s+EXISTS\s+{n}_|UPDATE\s+{n}_"
            rf"|DELETE\s+FROM\s+{n}_|INSERT\s+INTO\s+{n}_)", re.I)
        rename = re.compile(r"\bRENAME\s+TO\s+(?!" + n + "_)", re.I)

        def one(sql: str) -> bool:
            # exactly one statement: after one trailing ";" is dropped, no ";" may remain
            return ";" not in sql.strip().removesuffix(";")

        for sql in records:
            if not (make.match(sql) and one(sql)):
                raise ValueError(f"component {name!r} may only create tables and indexes named {name}_…, one statement each: {sql!r}")
        for step in changes:
            for sql in step:
                if not ((make.match(sql) or alter.match(sql)) and one(sql) and not rename.search(sql)):
                    raise ValueError(f"component {name!r} may only change tables and indexes named {name}_…, one statement each: {sql!r}")
        db = self.connect()
        db.isolation_level = None
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("CREATE TABLE IF NOT EXISTS core_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
                row = db.execute("SELECT version FROM core_schema WHERE component = ?", (name,)).fetchone()
                if row is not None:
                    version = row[0]
                else:
                    mine = db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE ? ESCAPE '\\' LIMIT 1",
                                      (name.replace("_", "\\_") + "\\_%",)).fetchone()
                    version = 0 if mine else len(changes)
                for step in changes[version:]:
                    for sql in step:
                        db.execute(sql)
                for sql in records:
                    db.execute(sql)
                db.execute("INSERT OR REPLACE INTO core_schema (component, version) VALUES (?, ?)", (name, max(version, len(changes))))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()
