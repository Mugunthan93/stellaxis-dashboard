"""The tables of Claude management: one row per session and one per item of the last snapshot."""
from __future__ import annotations

import json
from contextlib import closing

from core.app import StoreError
from core.util import read_json

from .usage import cost_of

RECORDS = [
    "CREATE TABLE IF NOT EXISTS claude_management_sessions (id TEXT PRIMARY KEY, project TEXT, first_ts TEXT, last_ts TEXT, "
    "branch TEXT, cost REAL NOT NULL, data TEXT NOT NULL, hours TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS claude_management_items (id TEXT PRIMARY KEY, pos INTEGER NOT NULL, kind TEXT NOT NULL, "
    "scope TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL)",
]


def _data(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if not k.startswith("_")})


def hours(hourly: dict, pricing: dict) -> dict:
    """hour -> the cost of that hour, from a session row's `_hourly`."""
    return {hour: round(sum(cost_of(model, row, pricing) for model, row in per.items()), 6) for hour, per in hourly.items()}


def save_sessions(ctx, rows: list, pricing: dict) -> None:
    """Write the rows that are new or changed and delete the sessions that are gone; nothing when the store is down."""
    try:
        db = ctx.db()
    except StoreError:
        return
    with closing(db), db:
        have = {r["id"]: (r["cost"], r["data"], r["hours"])
                for r in db.execute("SELECT id, cost, data, hours FROM claude_management_sessions")}
        for row in rows:
            data, hrs = _data(row), json.dumps(hours(row["_hourly"], pricing))
            if have.get(row["id"]) != (row["cost"], data, hrs):
                db.execute("INSERT OR REPLACE INTO claude_management_sessions (id, project, first_ts, last_ts, branch, cost, data, hours) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (row["id"], row["project"], row["first_ts"], row["last_ts"], row["branch"], row["cost"], data, hrs))
        gone = set(have) - {row["id"] for row in rows}
        db.executemany("DELETE FROM claude_management_sessions WHERE id = ?", [(i,) for i in sorted(gone)])


def save_items(ctx, items: list) -> None:
    """Write the items that are new, changed or moved and delete the ones that are gone; nothing when the store is down."""
    try:
        db = ctx.db()
    except StoreError:
        return
    with closing(db), db:
        have = {r["id"]: (r["pos"], r["data"]) for r in db.execute("SELECT id, pos, data FROM claude_management_items")}
        for pos, it in enumerate(items):
            data = _data(it)
            if have.get(it["id"]) != (pos, data):
                db.execute("INSERT OR REPLACE INTO claude_management_items (id, pos, kind, scope, name, data) VALUES (?, ?, ?, ?, ?, ?)",
                           (it["id"], pos, it["kind"], it["scope"], it["name"], data))
        gone = set(have) - {it["id"] for it in items}
        db.executemany("DELETE FROM claude_management_items WHERE id = ?", [(i,) for i in sorted(gone)])


def sessions(ctx, hours: bool = False) -> list:
    """[{"id", "project", "first_ts", "last_ts", "branch", "cost"}]: this pass's rows while a pass holds them, else the
    store's; with "hours": {hour: cost} when asked, always from the store."""
    rows = ctx.memo.get("sessions")
    if rows is not None and not hours:
        return [{k: r[k] for k in ("id", "project", "first_ts", "last_ts", "branch", "cost")} for r in rows]
    with closing(ctx.db()) as db:
        out = []
        for r in db.execute("SELECT * FROM claude_management_sessions ORDER BY last_ts DESC, id"):
            row = {"id": r["id"], "project": r["project"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                   "branch": r["branch"], "cost": r["cost"]}
            if hours:
                row["hours"] = json.loads(r["hours"])
            out.append(row)
        return out


def items(ctx) -> list:
    with closing(ctx.db()) as db:
        return [json.loads(r["data"]) for r in db.execute("SELECT data FROM claude_management_items ORDER BY pos")]


def items_by_id(ctx) -> dict:
    """id -> item: the store's items; when the store is down or holds none, the items of snapshot.json."""
    try:
        rows = items(ctx)
    except StoreError:
        rows = []
    if not rows:
        rows = (read_json(ctx.home / "snapshot.json", {}) or {}).get("items") or []
    return {it["id"]: it for it in rows}
