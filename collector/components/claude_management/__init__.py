"""Claude management: reads the transcripts and the Claude setup, prices the use, and writes the snapshot. Its actions archive
and move items of the setup and save Claude's settings files."""
from __future__ import annotations

import time

from core.component import Component
from core.util import write_json

from . import records, settings, setup, snapshot, trim, usage


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
    if "collector" not in ctx.inputs or "files" not in ctx.memo:   # round one failed: its error is the one to show
        return
    col, files, rows = ctx.inputs["collector"], ctx.memo["files"], ctx.memo["sessions"]
    items, projects, meta = setup.inventory(ctx, ctx.read("project_management", "repos"))
    probes = (setup.run_probes(items, ctx.cfg, ctx.cache, ctx.status, col["version"]) if col.get("probe")
              else ctx.cache.get("probes", {}))
    snap = snapshot.build(ctx, rows, len(files), items, projects, meta, probes, ctx.memo["t0"])
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    snapshot.append_history(ctx.home, snap)
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    records.save_items(ctx, snap["items"])


COMPONENT = Component(name="claude_management", readers=[usage_reader, setup_reader], lines=usage.LINES,
                      records=records.RECORDS, reads=[("project_management", "repos")], handle=settings.handle,
                      actions={"archive": trim.archive, "move": trim.move, "save_settings": settings.save_settings},
                      undo={"claude_management.settings_file": settings.settings_file},
                      offers={"sessions": records.sessions, "items": records.items})
