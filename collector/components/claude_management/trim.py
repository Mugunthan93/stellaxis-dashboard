"""Archive and move items of the Claude setup (from prune.py): each step goes into the plan's manifest, so restore undoes it."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from core.undo import dig
from core.util import read_json, short_path, write_json

from . import records


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)) if p else None


def edit_json(plan, path: Path, fn, desc: str):
    data = read_json(path, {}) or {}
    plan.say(desc)
    if plan.apply:
        plan.backup_json(path)
        fn(data)
        write_json(path, data, indent=2)


def mcp_location(ctx, it: dict):
    """(json path, key path list) where this server's config lives."""
    scope = it["scope"]
    if scope == "user":
        return ctx.claude_json, ["mcpServers"]
    if scope == "local":
        return ctx.claude_json, ["projects", str(expand(it["scope_path"])), "mcpServers"]
    if scope == "project":
        return expand(it["path"]), ["mcpServers"]
    return None, None


def archive_item(plan, ctx, it: dict):
    kind, path = it["kind"], expand(it.get("path"))
    if not it.get("removable", True):
        plan.note(f"  skip {it['id']}: managed outside this machine ({it.get('manage', 'n/a')})")
        return
    if it.get("plugin") and kind != "plugin":
        plan.note(f"  skip {it['id']}: part of plugin {it['plugin']} — archive the plugin instead")
        return
    if kind in ("skill", "agent", "command", "rule", "claude_md", "memory"):
        src = expand(it.get("dir")) if kind == "skill" and it.get("dir") else path
        if not src or not src.exists():
            plan.note(f"  skip {it['id']}: {src} not found")
            return
        dest = plan.dir / "files" / str(src).lstrip("/")
        plan.say(f"move {short_path(src)} -> {short_path(dest)}")
        plan.manifest["entries"].append({"op": "move", "id": it["id"], "from": str(src), "to": str(dest)})
        if plan.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    elif kind == "mcp":
        jpath, keys = mcp_location(ctx, it)
        if not jpath or not jpath.exists():
            plan.note(f"  skip {it['id']}: config file not found")
            return
        servers = dig(read_json(jpath, {}) or {}, keys) or {}
        if it["name"] not in servers:
            plan.note(f"  skip {it['id']}: not present in {short_path(jpath)}")
            return
        conf = servers[it["name"]]
        plan.manifest["entries"].append({"op": "json_del", "id": it["id"], "file": str(jpath), "keys": keys,
                                         "name": it["name"], "value": conf})
        edit_json(plan, jpath, lambda d: dig(d, keys).pop(it["name"], None),
                  f"remove mcpServers.{it['name']} from {short_path(jpath)}")
    elif kind == "plugin":
        sp = ctx.claude_dir / "settings.json"
        if it["scope"] in ("project", "local") and it.get("scope_path"):
            sp = expand(it["scope_path"]) / ".claude" / ("settings.json" if it["scope"] == "project" else "settings.local.json")
        prev = (read_json(sp, {}) or {}).get("enabledPlugins", {}).get(it["name"])
        plan.manifest["entries"].append({"op": "json_set", "id": it["id"], "file": str(sp), "keys": ["enabledPlugins"],
                                         "name": it["name"], "prev": prev})
        edit_json(plan, sp, lambda d: d.setdefault("enabledPlugins", {}).__setitem__(it["name"], False),
                  f"disable plugin {it['name']} in {short_path(sp)} (uninstall later with: claude plugin uninstall {it['name']})")
    else:
        plan.note(f"  skip {it['id']}: kind {kind} not supported")


def move_item(plan, ctx, it: dict, to: Path):
    kind = it["kind"]
    if it["scope"] not in ("user", "local") or it.get("plugin"):
        plan.note(f"  skip {it['id']}: only user/local-scope items can move down to a project")
        return
    if not (to / ".git").exists() and not (to / ".claude").exists():
        plan.note(f"  warning: {short_path(to)} has no .git or .claude — is it a project root?")
    if kind in ("skill", "agent", "command"):
        src = expand(it.get("dir")) if kind == "skill" else expand(it["path"])
        sub = {"skill": "skills", "agent": "agents", "command": "commands"}[kind]
        base = ctx.claude_dir / sub
        dest = to / ".claude" / sub / src.relative_to(base)
        if dest.exists():
            plan.note(f"  skip {it['id']}: {short_path(dest)} already exists (duplicate — archive one instead)")
            return
        plan.say(f"move {short_path(src)} -> {short_path(dest)}")
        plan.manifest["entries"].append({"op": "move", "id": it["id"], "from": str(src), "to": str(dest)})
        if plan.apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    elif kind == "mcp":
        jpath, keys = mcp_location(ctx, it)
        conf = dig(read_json(jpath, {}) or {}, keys).get(it["name"])
        target = to / ".mcp.json"
        plan.manifest["entries"].append({"op": "json_del", "id": it["id"], "file": str(jpath), "keys": keys,
                                         "name": it["name"], "value": conf})
        plan.manifest["entries"].append({"op": "json_add", "id": it["id"], "file": str(target), "keys": ["mcpServers"],
                                         "name": it["name"]})
        edit_json(plan, jpath, lambda d: dig(d, keys).pop(it["name"], None),
                  f"remove mcpServers.{it['name']} from {short_path(jpath)}")
        if plan.apply and not target.exists():
            write_json(target, {"mcpServers": {}}, indent=2)
        edit_json(plan, target, lambda d: d.setdefault("mcpServers", {}).__setitem__(it["name"], conf),
                  f"add mcpServers.{it['name']} to {short_path(target)} (commit it, or add to .gitignore if it holds secrets)")
    else:
        plan.note(f"  skip {it['id']}: moving {kind} is manual (edit the files)")


def archive(plan, ctx, ids: list) -> bool:
    items = records.items_by_id(ctx)
    for iid in ids:
        it = items.get(iid)
        if not it:
            plan.note(f"  unknown id {iid} (run `prune.py list`)")
            continue
        archive_item(plan, ctx, it)
    return True


def move(plan, ctx, ids: list, to: str) -> bool:
    items = records.items_by_id(ctx)
    for iid in ids:
        it = items.get(iid)
        if not it:
            plan.note(f"  unknown id {iid} (run `prune.py list`)")
            continue
        move_item(plan, ctx, it, expand(to))
    return True
