#!/usr/bin/env python3
"""Stellaxis prune: archive, move, and restore Claude Code skills/agents/commands/MCP servers/plugins.

Everything is a dry run unless --apply is given. Archived items go to ~/.claude/_archive/<stamp>/
with a manifest.json so `restore` can put them back exactly.

  python3 prune.py list [--kind skill] [--unused-days 30] [--scope user]
  python3 prune.py archive <item-id>... [--apply]
  python3 prune.py move <item-id>... --to ~/code/mesell [--apply]
  python3 prune.py restore <stamp|last> [--apply]
  python3 prune.py archives
  python3 prune.py purge --older-than 14d [--apply]

Item ids come from the snapshot (see `list`), e.g. skill:user:foo, agent:user:bar, mcp:user:github,
plugin:user:name@marketplace, mcp:local:~/code/x:server, agent:project:~/code/mesell:planner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402

ARCHIVE = C.CLAUDE_DIR / "_archive"


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)) if p else None


def load_items() -> dict:
    snap = C.read_json(C.STX_HOME / "snapshot.json")
    if not snap:
        snap = C.collect()
    return {it["id"]: it for it in snap["items"]}


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


class Plan:
    def __init__(self, apply: bool, label: str):
        self.apply, self.steps = apply, []
        self.dir = ARCHIVE / f"{stamp()}-{label}"
        self.manifest = {"created": C.iso(C.now_utc()), "label": label, "entries": []}

    def say(self, msg):
        print(("  " if self.apply else "  [dry-run] ") + msg)

    def backup_json(self, path: Path):
        dest = self.dir / "json-backups" / (str(path).strip("/").replace("/", "__"))
        if not any(e.get("backup_of") == str(path) for e in self.manifest["entries"]):
            self.manifest["entries"].append({"op": "json_backup", "backup_of": str(path), "backup": str(dest)})
            if self.apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)

    def finish(self):
        if self.apply and self.manifest["entries"]:
            self.dir.mkdir(parents=True, exist_ok=True)
            C.write_json(self.dir / "manifest.json", self.manifest, indent=2)
            print(f"\nManifest: {C.short_path(self.dir / 'manifest.json')}")
            print(f"Undo:     python3 {C.short_path(Path(__file__))} restore {self.dir.name} --apply")
            C.collect()
        elif not self.apply:
            print("\nNothing changed. Re-run with --apply to execute.")


def edit_json(plan: Plan, path: Path, fn, desc: str):
    data = C.read_json(path, {}) or {}
    plan.say(desc)
    if plan.apply:
        plan.backup_json(path)
        fn(data)
        C.write_json(path, data, indent=2)


def mcp_location(it: dict):
    """(json path, key path list) where this server's config lives."""
    scope = it["scope"]
    if scope == "user":
        return C.CLAUDE_JSON, ["mcpServers"]
    if scope == "local":
        return C.CLAUDE_JSON, ["projects", str(expand(it["scope_path"])), "mcpServers"]
    if scope == "project":
        return expand(it["path"]), ["mcpServers"]
    return None, None


def dig(d: dict, keys: list, create=False):
    for k in keys:
        if k not in d:
            if not create:
                return None
            d[k] = {}
        d = d[k]
    return d


def archive_item(plan: Plan, it: dict):
    kind, path = it["kind"], expand(it.get("path"))
    if not it.get("removable", True):
        print(f"  skip {it['id']}: managed outside this machine ({it.get('manage', 'n/a')})")
        return
    if it.get("plugin") and kind != "plugin":
        print(f"  skip {it['id']}: part of plugin {it['plugin']} — archive the plugin instead")
        return
    if kind in ("skill", "agent", "command", "rule", "claude_md", "memory"):
        src = expand(it.get("dir")) if kind == "skill" and it.get("dir") else path
        if not src or not src.exists():
            print(f"  skip {it['id']}: {src} not found")
            return
        dest = plan.dir / "files" / str(src).lstrip("/")
        plan.say(f"move {C.short_path(src)} -> {C.short_path(dest)}")
        plan.manifest["entries"].append({"op": "move", "id": it["id"], "from": str(src), "to": str(dest)})
        if plan.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    elif kind == "mcp":
        jpath, keys = mcp_location(it)
        if not jpath or not jpath.exists():
            print(f"  skip {it['id']}: config file not found")
            return
        servers = dig(C.read_json(jpath, {}) or {}, keys) or {}
        if it["name"] not in servers:
            print(f"  skip {it['id']}: not present in {C.short_path(jpath)}")
            return
        conf = servers[it["name"]]
        plan.manifest["entries"].append({"op": "json_del", "id": it["id"], "file": str(jpath), "keys": keys,
                                         "name": it["name"], "value": conf})
        edit_json(plan, jpath, lambda d: dig(d, keys).pop(it["name"], None),
                  f"remove mcpServers.{it['name']} from {C.short_path(jpath)}")
    elif kind == "plugin":
        sp = C.CLAUDE_DIR / "settings.json"
        if it["scope"] in ("project", "local") and it.get("scope_path"):
            sp = expand(it["scope_path"]) / ".claude" / ("settings.json" if it["scope"] == "project" else "settings.local.json")
        prev = (C.read_json(sp, {}) or {}).get("enabledPlugins", {}).get(it["name"])
        plan.manifest["entries"].append({"op": "json_set", "id": it["id"], "file": str(sp), "keys": ["enabledPlugins"],
                                         "name": it["name"], "prev": prev})
        edit_json(plan, sp, lambda d: d.setdefault("enabledPlugins", {}).__setitem__(it["name"], False),
                  f"disable plugin {it['name']} in {C.short_path(sp)} (uninstall later with: claude plugin uninstall {it['name']})")
    else:
        print(f"  skip {it['id']}: kind {kind} not supported")


def move_item(plan: Plan, it: dict, to: Path):
    kind = it["kind"]
    if it["scope"] not in ("user", "local") or it.get("plugin"):
        print(f"  skip {it['id']}: only user/local-scope items can move down to a project")
        return
    if not (to / ".git").exists() and not (to / ".claude").exists():
        print(f"  warning: {C.short_path(to)} has no .git or .claude — is it a project root?")
    if kind in ("skill", "agent", "command"):
        src = expand(it.get("dir")) if kind == "skill" else expand(it["path"])
        sub = {"skill": "skills", "agent": "agents", "command": "commands"}[kind]
        base = C.CLAUDE_DIR / sub
        dest = to / ".claude" / sub / src.relative_to(base)
        if dest.exists():
            print(f"  skip {it['id']}: {C.short_path(dest)} already exists (duplicate — archive one instead)")
            return
        plan.say(f"move {C.short_path(src)} -> {C.short_path(dest)}")
        plan.manifest["entries"].append({"op": "move", "id": it["id"], "from": str(src), "to": str(dest)})
        if plan.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    elif kind == "mcp":
        jpath, keys = mcp_location(it)
        conf = dig(C.read_json(jpath, {}) or {}, keys).get(it["name"])
        target = to / ".mcp.json"
        plan.manifest["entries"].append({"op": "json_del", "id": it["id"], "file": str(jpath), "keys": keys,
                                         "name": it["name"], "value": conf})
        plan.manifest["entries"].append({"op": "json_add", "id": it["id"], "file": str(target), "keys": ["mcpServers"],
                                         "name": it["name"]})
        edit_json(plan, jpath, lambda d: dig(d, keys).pop(it["name"], None),
                  f"remove mcpServers.{it['name']} from {C.short_path(jpath)}")
        if plan.apply and not target.exists():
            C.write_json(target, {"mcpServers": {}}, indent=2)
        edit_json(plan, target, lambda d: d.setdefault("mcpServers", {}).__setitem__(it["name"], conf),
                  f"add mcpServers.{it['name']} to {C.short_path(target)} (commit it, or add to .gitignore if it holds secrets)")
    else:
        print(f"  skip {it['id']}: moving {kind} is manual (edit the files)")


def restore(name: str, apply: bool):
    dirs = sorted(d for d in ARCHIVE.iterdir() if (d / "manifest.json").exists()) if ARCHIVE.exists() else []
    d = dirs[-1] if name == "last" and dirs else ARCHIVE / name
    man = C.read_json(d / "manifest.json")
    if not man:
        sys.exit(f"no manifest in {d}")
    print(f"Restoring {d.name}:")
    for e in reversed(man["entries"]):
        pre = "  " if apply else "  [dry-run] "
        if e["op"] == "move":
            print(f"{pre}move {C.short_path(e['to'])} -> {C.short_path(e['from'])}")
            if apply and Path(e["to"]).exists():
                Path(e["from"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(e["to"], e["from"])
        elif e["op"] == "json_del":
            print(f"{pre}re-add {e['name']} to {C.short_path(e['file'])}")
            if apply:
                data = C.read_json(Path(e["file"]), {}) or {}
                dig(data, e["keys"], create=True)[e["name"]] = e["value"]
                C.write_json(Path(e["file"]), data, indent=2)
        elif e["op"] == "json_add":
            print(f"{pre}remove {e['name']} from {C.short_path(e['file'])}")
            if apply:
                data = C.read_json(Path(e["file"]), {}) or {}
                (dig(data, e["keys"]) or {}).pop(e["name"], None)
                C.write_json(Path(e["file"]), data, indent=2)
        elif e["op"] == "json_set":
            print(f"{pre}reset {e['name']} in {C.short_path(e['file'])} to {e['prev']!r}")
            if apply:
                data = C.read_json(Path(e["file"]), {}) or {}
                tgt = dig(data, e["keys"], create=True)
                if e["prev"] is None:
                    tgt.pop(e["name"], None)
                else:
                    tgt[e["name"]] = e["prev"]
                C.write_json(Path(e["file"]), data, indent=2)
    if apply:
        (d / "manifest.json").rename(d / "manifest.restored.json")
        C.collect()
    else:
        print("\nNothing changed. Re-run with --apply to execute.")


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
    if a.cmd == "purge":
        cutoff = dt.datetime.now().timestamp() - C.parse_duration(a.older_than, 14 * 86400)
        for d in sorted(ARCHIVE.iterdir()) if ARCHIVE.exists() else []:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                print(("  " if a.apply else "  [dry-run] ") + f"delete {C.short_path(d)}")
                if a.apply:
                    shutil.rmtree(d)
        return

    items = load_items()
    plan = Plan(a.apply, a.cmd)
    print(f"{'Applying' if a.apply else 'Plan'}: {a.cmd} {len(a.ids)} item(s)")
    for iid in a.ids:
        it = items.get(iid)
        if not it:
            print(f"  unknown id {iid} (run `prune.py list`)")
            continue
        if a.cmd == "archive":
            archive_item(plan, it)
        else:
            move_item(plan, it, expand(a.to))
    plan.finish()


if __name__ == "__main__":
    main()
