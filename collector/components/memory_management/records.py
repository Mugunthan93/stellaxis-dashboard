from __future__ import annotations

import sqlite3

RECORDS = [
    "CREATE TABLE IF NOT EXISTS memory_management_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, "
    "finished_at TEXT, finished_ts REAL, seconds REAL, complete INTEGER, note TEXT, disk_path TEXT, "
    "disk_total INTEGER, disk_free INTEGER, files INTEGER, skipped INTEGER)",
    "CREATE TABLE IF NOT EXISTS memory_management_folders (run INTEGER, path TEXT, parent TEXT, place TEXT, kind TEXT, "
    "owner TEXT, state TEXT, bytes INTEGER, logical_bytes INTEGER, linked_bytes INTEGER, files INTEGER, changed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS memory_management_skipped (run INTEGER, path TEXT, reason TEXT)",
]

ROW = ("path", "parent", "place", "kind", "owner", "state", "bytes", "logical_bytes", "linked_bytes", "files", "changed_at")


def save(db: sqlite3.Connection, run: dict, result: dict) -> None:
    """Write one run and its rows, then drop the rows of every older run. The caller commits."""
    disk = run["disk"] or {}
    id = db.execute(
        "INSERT INTO memory_management_runs (started_at, finished_at, finished_ts, seconds, complete, note, "
        "disk_path, disk_total, disk_free, files, skipped) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run["started_at"], run["finished_at"], run["finished_ts"], run["seconds"], int(result["complete"]),
         result["note"], disk.get("path"), disk.get("total"), disk.get("free"), result["files"],
         result["skipped_count"])).lastrowid
    for r in result["rows"]:
        db.execute(f"INSERT INTO memory_management_folders (run, {', '.join(ROW)}) VALUES (?{',?' * len(ROW)})",
                   (id, *[r[k] for k in ROW]))
    for s in result["skipped"]:
        db.execute("INSERT INTO memory_management_skipped (run, path, reason) VALUES (?,?,?)",
                   (id, s["path"], s["reason"]))
    db.execute("DELETE FROM memory_management_folders WHERE run != ?", (id,))
    db.execute("DELETE FROM memory_management_skipped WHERE run != ?", (id,))


def last(db: sqlite3.Connection) -> dict | None:
    r = db.execute("SELECT * FROM memory_management_runs ORDER BY id DESC LIMIT 1").fetchone()
    if r is None:
        return None
    disk = None if r["disk_path"] is None else {"path": r["disk_path"], "total": r["disk_total"], "free": r["disk_free"]}
    return {
        "run": {"started_at": r["started_at"], "finished_at": r["finished_at"], "seconds": r["seconds"],
                "complete": bool(r["complete"]), "note": r["note"], "disk": disk, "files": r["files"],
                "skipped": r["skipped"]},
        "rows": [dict(x) for x in db.execute(
            f"SELECT {', '.join(ROW)} FROM memory_management_folders WHERE run = ? ORDER BY rowid", (r["id"],))],
        "skipped": [dict(x) for x in db.execute(
            "SELECT path, reason FROM memory_management_skipped WHERE run = ? ORDER BY rowid", (r["id"],))],
    }


def last_finished_ts(db: sqlite3.Connection) -> float | None:
    r = db.execute("SELECT finished_ts FROM memory_management_runs ORDER BY id DESC LIMIT 1").fetchone()
    return None if r is None else r["finished_ts"]
