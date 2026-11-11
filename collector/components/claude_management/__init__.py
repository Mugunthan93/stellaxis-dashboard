"""Claude management: reads the transcripts and the Claude setup, prices the use, and writes the snapshot."""
from __future__ import annotations

import time

from core.component import Component
from core.util import write_json

from . import records, setup, snapshot, usage


def usage_reader(ctx) -> None:
    """Round one: the session rows of every transcript, kept for round two and saved in the store."""
    if "collector" not in ctx.inputs:
        return
    ctx.memo["t0"] = time.time()
    files = ctx.transcripts()
    rows = usage.sessions(files, ctx.cfg.get("pricing") or {})
    ctx.memo.update(files=files, sessions=rows)
    records.save_sessions(ctx, rows, ctx.cfg.get("pricing") or {})


def setup_reader(ctx) -> None:
    """Round two: the Claude setup joined with the rows of round one, written to snapshot.json and the store."""
    if "collector" not in ctx.inputs:
        return
    col, files, rows = ctx.inputs["collector"], ctx.memo["files"], ctx.memo["sessions"]
    items, projects, meta = setup.inventory(ctx, {st["cwd"] for st in files.values() if st.get("cwd")})
    probes = (setup.run_probes(items, ctx.cfg, ctx.cache, ctx.status, col["version"]) if col.get("probe")
              else ctx.cache.get("probes", {}))
    snap = snapshot.build(ctx, rows, len(files), items, projects, meta, probes, ctx.memo["t0"])
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    snapshot.append_history(ctx.home, snap)
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    ctx.memo["folders"] = snap["projects"]
    records.save_items(ctx, snap["items"])


def _sessions(ctx, hours: bool = False) -> list:
    """[{"id", "project", "first_ts", "last_ts", "branch", "cost"}] from the store, with "hours": {hour: cost} when asked."""
    return records.sessions(ctx, hours)


def _items(ctx) -> list:
    return records.items(ctx)


def _pass(ctx) -> list:
    """This pass's session rows and the folders of its snapshot, until Project management reads them itself."""
    memo = ctx.memo
    if "sessions" in memo and "folders" in memo:
        return [{"sessions": memo["sessions"], "folders": memo["folders"]}]
    return []


COMPONENT = Component(name="claude_management", readers=[usage_reader, setup_reader], lines=usage.LINES,
                      records=records.RECORDS, offers={"sessions": _sessions, "items": _items, "pass": _pass})
