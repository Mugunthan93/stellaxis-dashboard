#!/usr/bin/env python3
"""Mac side of the Claude Settings Remote artifact.

  settings_remote.py snapshot <out.json>      write the config/current document for the artifact
  settings_remote.py apply <change.json>      apply a queued change's `settings` to ~/.claude/settings.json

apply validates the JSON, backs the current file up to ~/.claude/backups/settings.json.<ts>, then
writes atomically. A change's `projectChanges` ({project path: {rule: "inherit" | "ask" | "deny"}}) is
written to each project's .claude/settings.local.json the same way. Prints {"ok": true, ...} or
{"ok": false, "error": ...}.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
BACKUPS = Path.home() / ".claude" / "backups"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def snapshot(out: str):
    data = json.loads(SETTINGS.read_text())
    Path(out).write_text(json.dumps({"settings": data, "snapshotAt": now(), "source": "~/.claude/settings.json"}))
    print(json.dumps({"ok": True}))


def write_atomic(path: Path, data: dict, backup_name: str) -> str | None:
    backup = None
    if path.exists():
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / (backup_name + "." + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return str(backup) if backup else None


def apply_project(root: str, rules: dict) -> dict:
    home = str(Path.home())
    p = Path(root)
    if not root.startswith(home + "/") or "/.claude" in root[len(home):] or "/Library/" in root or not p.is_dir():
        return {"project": root, "ok": False, "error": "not a project folder"}
    if any(v not in ("inherit", "ask", "deny") for v in rules.values()):
        return {"project": root, "ok": False, "error": "unknown state"}
    path = p / ".claude" / "settings.local.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    perms = data.setdefault("permissions", {})
    for rule, state in rules.items():
        for k in ("ask", "deny"):
            if rule in (perms.get(k) or []):
                perms[k] = [r for r in perms[k] if r != rule]
            if k == state:
                perms[k] = (perms.get(k) or []) + [rule]
    for k in ("ask", "deny"):
        if k in perms and not perms[k]:
            del perms[k]
    if not perms:
        del data["permissions"]
    if not data and not path.exists():
        return {"project": root, "ok": True, "backup": None}
    backup = write_atomic(path, data, "project-" + root.strip("/").replace("/", "-") + ".settings.local.json")
    return {"project": root, "ok": True, "backup": backup}


def apply(change_path: str):
    change = json.loads(Path(change_path).read_text())
    new = change.get("settings")
    if not isinstance(new, dict) or not new:
        print(json.dumps({"ok": False, "error": "change has no settings object"}))
        return
    perms = new.get("permissions", {})
    if not isinstance(perms, dict) or any(not isinstance(perms.get(k, []), list) for k in ("allow", "ask", "deny")):
        print(json.dumps({"ok": False, "error": "permissions must hold allow/ask/deny lists"}))
        return
    backup = None
    if new != json.loads(SETTINGS.read_text()):
        backup = write_atomic(SETTINGS, new, "settings.json")
    projects = [apply_project(root, rules) for root, rules in (change.get("projectChanges") or {}).items()]
    failed = [p for p in projects if not p["ok"]]
    print(json.dumps({"ok": not failed, "backup": backup, "projects": projects,
                      **({"error": "; ".join(p["project"] + ": " + p["error"] for p in failed)} if failed else {})}))


if __name__ == "__main__":
    cmd, arg = sys.argv[1], sys.argv[2]
    {"snapshot": snapshot, "apply": apply}[cmd](arg)
