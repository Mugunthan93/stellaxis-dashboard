#!/usr/bin/env python3
"""Stellaxis prune: archive, move, and restore Claude Code skills/agents/commands/MCP servers/plugins.

Everything is a dry run unless --apply is given. Archived items go to ~/.claude/_archive/<stamp>/
with a manifest.json so `restore` can put them back exactly.

  python3 prune.py list [--kind skill] [--unused-days 30] [--scope user]
  python3 prune.py archive <item-id>... [--apply]
  python3 prune.py move <item-id>... --to ~/code/mesell [--apply]
  python3 prune.py relocate ~/Documents/app ~/code/personal/app [--apply]
  python3 prune.py restore <stamp|last> [--apply]
  python3 prune.py archives
  python3 prune.py purge --older-than 14d [--apply]

Item ids come from the snapshot (see `list`), e.g. skill:user:foo, agent:user:bar, mcp:user:github,
plugin:user:name@marketplace, mcp:local:~/code/x:server, agent:project:~/code/mesell:planner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402
from core import undo  # noqa: E402
from core.undo import dig  # noqa: E402,F401

ARCHIVE = C.CLAUDE_DIR / "_archive"


def load_items() -> dict:
    snap = C.read_json(C.STX_HOME / "snapshot.json")
    if not snap:
        snap = C.collect()
    if not snap:
        sys.exit("no snapshot: Claude management wrote none")
    return {it["id"]: it for it in snap["items"]}


def Plan(apply: bool, label: str) -> undo.Plan:
    return undo.Plan(apply, label, ARCHIVE, on_applied=C.collect,
                     undo_hint=lambda name: f"python3 {C.short_path(Path(__file__))} restore {name} --apply")


def restore(name: str, apply: bool):
    handlers = C.the_core().undo_handlers(C.load_config())
    if "project_management.relocate" in handlers:   # an archive written before relocate was an action names its step "relocate"
        handlers["relocate"] = handlers["project_management.relocate"]
    try:
        undo.restore(name, apply, ARCHIVE, handlers=handlers, on_applied=C.collect)
    except undo.RestoreError as e:
        sys.exit(str(e))


def cmd_list(a):
    items = load_items().values()
    rows = []
    for it in items:
        if a.kind and it["kind"] != a.kind or a.scope and it["scope"] != a.scope:
            continue
        u = it.get("usage") or {}
        if a.unused_days is not None:
            if u.get("calls") and (u.get("days_since_used") or 0) < a.unused_days:
                continue
        rows.append(it)
    rows.sort(key=lambda x: -x["tokens_always"])
    print(f"{'always~tok':>10} {'calls':>5} {'last':>10}  id")
    for it in rows:
        u = it.get("usage") or {}
        last = (u.get("last_used") or "-")[:10]
        flag = "" if it.get("removable", True) else "  (managed: " + it.get("manage", "") + ")"
        print(f"{it['tokens_always']:>10} {u.get('calls', 0):>5} {last:>10}  {it['id']}{flag}")
    print(f"\n{len(rows)} items, ~{sum(i['tokens_always'] for i in rows):,} always-on tokens")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--kind")
    l.add_argument("--scope")
    l.add_argument("--unused-days", type=int)
    for name in ("archive", "move"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="+")
        p.add_argument("--apply", action="store_true")
        if name == "move":
            p.add_argument("--to", required=True)
    rl = sub.add_parser("relocate", help="move a project folder and update Claude + git references to it")
    rl.add_argument("src")
    rl.add_argument("dest")
    rl.add_argument("--apply", action="store_true")
    rl.add_argument("--force", action="store_true", help="even if a Claude session is running inside src")
    r = sub.add_parser("restore")
    r.add_argument("name")
    r.add_argument("--apply", action="store_true")
    sub.add_parser("archives")
    pg = sub.add_parser("purge")
    pg.add_argument("--older-than", default="14d")
    pg.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "archives":
        for d in sorted(ARCHIVE.iterdir()) if ARCHIVE.exists() else []:
            m = C.read_json(d / "manifest.json") or C.read_json(d / "manifest.restored.json") or {}
            state = "restored" if (d / "manifest.restored.json").exists() else "active"
            print(f"{d.name}  {state:8s}  {len(m.get('entries', []))} entries  " +
                  ", ".join(sorted({e.get('id', '') for e in m.get('entries', []) if e.get('id')})))
        return
    if a.cmd == "restore":
        return restore(a.name, a.apply)
    if a.cmd == "relocate":
        src, dest = (Path(os.path.abspath(os.path.expanduser(x))) for x in (a.src, a.dest))
        core = C.the_core()
        if not any(c.name == "project_management" and not c.error for c in core.components):
            sys.exit("Project management is not loaded")
        print(f"{'Applying' if a.apply else 'Plan'}: relocate {C.short_path(src)} -> {C.short_path(dest)}")
        ok = core.run_action("project_management", "relocate", {"src": str(src), "dest": str(dest), "force": a.force},
                             a.apply, C.load_config(), plan=Plan(a.apply, "relocate"))["ok"]   # the core finishes the plan
        if not ok:
            sys.exit(1)
        return
    if a.cmd == "purge":
        cutoff = dt.datetime.now().timestamp() - C.parse_duration(a.older_than, 14 * 86400)
        for d in sorted(ARCHIVE.iterdir()) if ARCHIVE.exists() else []:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                print(("  " if a.apply else "  [dry-run] ") + f"delete {C.short_path(d)}")
                if a.apply:
                    shutil.rmtree(d)
        return

    load_items()
    core = C.the_core()
    if not any(c.name == "claude_management" and not c.error for c in core.components):
        sys.exit("Claude management is not loaded")
    plan = Plan(a.apply, a.cmd)
    print(f"{'Applying' if a.apply else 'Plan'}: {a.cmd} {len(a.ids)} item(s)")
    args = {"ids": a.ids} if a.cmd == "archive" else {"ids": a.ids, "to": a.to}
    core.run_action("claude_management", a.cmd, args, a.apply, C.load_config(), plan=plan)   # the core finishes the plan


if __name__ == "__main__":
    main()
