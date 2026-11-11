"""Read and save Claude Code settings files for the dashboard's Settings page (from settings_api.py).

Scopes: user (~/.claude/settings.json, every session on this Mac), project (<repo>/.claude/settings.json)
and local (<repo>/.claude/settings.local.json). Saves validate, back up the old file next to it as
<name>.bak-<stamp>, keep the file's existing JSON formatting, and replace it atomically. A save whose
content is unchanged writes the original bytes back, so a no-op save is byte-for-byte identical.
A save is the action `save_settings`: its manifest lets `prune.py restore` put the old file back.

  GET  /api/settings?scope=user|project|local&project=<path>
  POST /api/settings/preview   {scope, project, data}            -> unified diff
  POST /api/settings/save      {scope, project, data, base_sha}  -> {path, backup, sha}
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core import util
from core.server import ApiError, host_ok, read_body, send_json
from core.undo import pre
from core.util import short_path

REPO_DIR = Path(__file__).resolve().parents[3]
SCOPES = {"user", "project", "local"}
DEFAULT_FORMAT = {"indent": 2, "ascii": False, "newline": False}  # what Claude Code itself writes

HOOK_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "Notification",
               "UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop", "PreCompact", "SessionStart",
               "SessionEnd"]


# ---------------------------------------------------------------- files

def known_projects(ctx) -> list[str]:
    """Folders the user may pick for project/local scope: this repo plus every project in the snapshot."""
    out = [str(_main_checkout(REPO_DIR))]
    try:
        snap = json.loads((ctx.home / "snapshot.json").read_text(encoding="utf-8"))
        out += [p["path"] for p in snap.get("projects", []) if p.get("path")]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    seen, res = set(), []
    for p in out:
        p = str(Path(p).expanduser())
        if p not in seen and p != str(util.HOME):
            seen.add(p)
            res.append(p)
    return res


def _main_checkout(p: Path) -> Path:
    # a worktree under <repo>/.claude/worktrees/<name> belongs to <repo>
    parts = p.parts
    if ".claude" in parts:
        i = parts.index(".claude")
        if i + 1 < len(parts) and parts[i + 1] == "worktrees":
            return Path(*parts[:i])
    return p


def settings_path(ctx, scope: str, project: str | None) -> Path:
    if scope not in SCOPES:
        raise ApiError(400, f"unknown scope {scope!r}")
    if scope == "user":
        return ctx.claude_dir / "settings.json"
    if not project:
        raise ApiError(400, "project scope needs a project folder")
    proj = str(Path(project).expanduser())
    if proj not in known_projects(ctx):
        raise ApiError(400, f"{proj} is not a known project folder")
    name = "settings.json" if scope == "project" else "settings.local.json"
    return Path(proj) / ".claude" / name


def sha(b: bytes | None) -> str | None:
    return None if b is None else hashlib.sha256(b).hexdigest()


def read_bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def detect_format(raw: bytes | None, data) -> dict:
    """Find the json.dumps options that reproduce the file exactly, so edits only touch changed lines."""
    if raw is None or data is None:
        return dict(DEFAULT_FORMAT)
    for indent in (2, 4, "\t", None):
        for ascii_ in (False, True):
            s = json.dumps(data, indent=indent, ensure_ascii=ascii_).encode()
            if s == raw or s + b"\n" == raw:
                return {"indent": indent, "ascii": ascii_, "newline": s != raw, "exact": True}
    text = raw.decode("utf-8", "replace")
    indent = 4 if "\n    \"" in text and "\n  \"" not in text else ("\t" if "\n\t" in text else 2)
    return {"indent": indent, "ascii": "\\u" in text, "newline": text.endswith("\n"), "exact": False}


def dump(data, fmt: dict) -> bytes:
    s = json.dumps(data, indent=fmt["indent"], ensure_ascii=fmt["ascii"])
    return (s + ("\n" if fmt["newline"] else "")).encode()


def atomic_write(p: Path, body: bytes, mode: int | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:  # settings can hold secrets in env: new files are owner-only
        mode = p.stat().st_mode & 0o777 if p.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- validation

def validate(data) -> list[str]:
    """Type checks for the keys the page edits. Unknown keys pass through untouched."""
    if not isinstance(data, dict):
        return ["settings must be a JSON object"]
    errs = []

    def is_str_list(v):
        return isinstance(v, list) and all(isinstance(x, str) for x in v)

    for k in ("model", "outputStyle"):
        if k in data and not isinstance(data[k], str):
            errs.append(f"{k} must be a string")
    if "includeCoAuthoredBy" in data and not isinstance(data["includeCoAuthoredBy"], bool):
        errs.append("includeCoAuthoredBy must be true or false")
    if "cleanupPeriodDays" in data:
        v = data["cleanupPeriodDays"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            errs.append("cleanupPeriodDays must be a whole number of days, 0 or more")
    perms = data.get("permissions")
    if perms is not None:
        if not isinstance(perms, dict):
            errs.append("permissions must be an object")
        else:
            for k in ("allow", "deny", "ask", "additionalDirectories"):
                if k in perms and not is_str_list(perms[k]):
                    errs.append(f"permissions.{k} must be a list of strings")
            if "defaultMode" in perms and not isinstance(perms["defaultMode"], str):
                errs.append("permissions.defaultMode must be a string")
    if "env" in data:
        env = data["env"]
        if not isinstance(env, dict):
            errs.append("env must be an object")
        else:
            errs += [f"env.{k} must be a string" for k, v in env.items() if not isinstance(v, str)]
            errs += [f"env has an empty variable name" for k in env if not k.strip()][:1]
    if "enabledPlugins" in data:
        ep = data["enabledPlugins"]
        if not isinstance(ep, dict):
            errs.append("enabledPlugins must be an object")
        else:
            errs += [f"enabledPlugins.{k} must be true or false" for k, v in ep.items()
                     if not isinstance(v, (bool, list))]
    if "statusLine" in data:
        sl = data["statusLine"]
        if not isinstance(sl, dict):
            errs.append("statusLine must be an object")
        elif sl.get("type") == "command" and not isinstance(sl.get("command"), str):
            errs.append("statusLine.command must be a string")
    if "hooks" in data:
        hooks = data["hooks"]
        if not isinstance(hooks, dict):
            errs.append("hooks must be an object of event → list of matcher groups")
        else:
            for ev, groups in hooks.items():
                if not isinstance(groups, list):
                    errs.append(f"hooks.{ev} must be a list")
                    continue
                for i, g in enumerate(groups):
                    if not isinstance(g, dict):
                        errs.append(f"hooks.{ev}[{i}] must be an object")
                        continue
                    if "matcher" in g and not isinstance(g["matcher"], str):
                        errs.append(f"hooks.{ev}[{i}].matcher must be a string")
                    hs = g.get("hooks")
                    if not isinstance(hs, list):
                        errs.append(f"hooks.{ev}[{i}].hooks must be a list")
                        continue
                    for j, h in enumerate(hs):
                        if not isinstance(h, dict) or not isinstance(h.get("type"), str):
                            errs.append(f"hooks.{ev}[{i}].hooks[{j}] needs a \"type\"")
                        elif h["type"] == "command" and not isinstance(h.get("command"), str):
                            errs.append(f"hooks.{ev}[{i}].hooks[{j}].command must be a string")
    return errs


# ---------------------------------------------------------------- operations

def load(ctx, scope: str, project: str | None) -> dict:
    p = settings_path(ctx, scope, project)
    raw = read_bytes(p)
    data, error = ({}, None) if raw is None else (None, None)
    if raw is not None:
        try:
            data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError) as e:
            error = f"file is not valid JSON: {e}"
    fmt = detect_format(raw, data)
    return {"scope": scope, "project": project, "path": str(p), "exists": raw is not None,
            "text": None if raw is None else raw.decode("utf-8", "replace"), "data": data,
            "sha": sha(raw), "format": fmt, "error": error, "projects": known_projects(ctx),
            "default_project": known_projects(ctx)[0], "hook_events": HOOK_EVENTS}


def _plan(ctx, body: dict):
    scope, project, data = body.get("scope", "user"), body.get("project"), body.get("data")
    errs = validate(data)
    if errs:
        raise ApiError(422, "settings did not validate", errors=errs)
    p = settings_path(ctx, scope, project)
    raw = read_bytes(p)
    old = None
    if raw is not None:
        try:
            old = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError):
            old = None
    fmt = detect_format(raw, old)
    # compare serialised forms, not ==, so key order and true-vs-1 count as changes
    unchanged = raw is not None and old is not None and dump(old, fmt) == dump(data, fmt)
    new = raw if unchanged else dump(data, fmt)
    return p, raw, new, unchanged


def preview(ctx, body: dict) -> dict:
    p, raw, new, unchanged = _plan(ctx, body)
    old_text = "" if raw is None else raw.decode("utf-8", "replace")
    diff = "".join(difflib.unified_diff(old_text.splitlines(True), new.decode("utf-8").splitlines(True),
                                        fromfile=f"{p} (on disk)", tofile=f"{p} (after save)", n=3))
    return {"path": str(p), "changed": raw != new, "unchanged_content": unchanged, "diff": diff,
            "sha": sha(raw), "exists": raw is not None}


def save(ctx, body: dict) -> dict:
    p, raw, new, unchanged = _plan(ctx, body)
    if body.get("base_sha", "missing") != sha(raw):
        raise ApiError(409, "the file changed on disk since you loaded it; reload and re-apply your edits",
                       sha=sha(raw))
    backup = None
    if raw is not None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        b = p.with_name(f"{p.name}.bak-{stamp}")
        n = 1
        while b.exists():
            b = p.with_name(f"{p.name}.bak-{stamp}-{n}")
            n += 1
        atomic_write(b, raw, p.stat().st_mode & 0o777)
        backup = str(b)
    atomic_write(p, new)
    return {"path": str(p), "backup": backup, "sha": sha(new), "unchanged_content": unchanged,
            "bytes_identical": raw == new}


# ---------------------------------------------------------------- action and undo

def save_settings(plan, ctx, body: dict) -> bool:
    if not plan.apply:
        plan.result = preview(ctx, body)
        plan.say(f"save {short_path(plan.result['path'])}")
        return True
    plan.result = save(ctx, body)
    plan.say(f"save {short_path(plan.result['path'])}")
    plan.manifest["entries"].append({"op": "claude_management.settings_file", "file": plan.result["path"], "backup": plan.result["backup"]})
    return True


save_settings.private = ("body",)


def settings_file(entry: dict, apply: bool, out, ctx) -> bool:
    """Undo a save: the file gets the bytes of its backup back, or goes when the save made it."""
    f = Path(entry["file"])
    if entry["backup"] is None:
        out(f"{pre(apply)}remove {short_path(f)}: it did not exist before the save")
        if apply and f.exists():
            f.unlink()
        return True
    backup = Path(entry["backup"])
    if not backup.is_file():
        out(f"{pre(apply)}{short_path(backup)} is missing: {short_path(f)} was not put back")
        return False
    out(f"{pre(apply)}put {short_path(f)} back from {short_path(backup)}")
    if apply:
        atomic_write(f, backup.read_bytes())
    return True


# ---------------------------------------------------------------- http

def handle(handler, method: str, ctx) -> bool:
    """Serve /api/settings*. Returns False when the path is not ours."""
    u = urlsplit(handler.path)
    if not u.path.startswith("/api/settings"):
        return False
    try:
        if not host_ok(handler):
            raise ApiError(403, "settings API only answers same-origin localhost requests")
        if method == "GET" and u.path == "/api/settings":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            send_json(handler, 200, load(ctx, q.get("scope", "user"), q.get("project")))
            return True
        if method == "POST" and u.path in ("/api/settings/preview", "/api/settings/save"):
            body = read_body(handler)
            send_json(handler, 200, ctx.act("save_settings", {"body": body}, u.path.endswith("save"))["result"])
            return True
        raise ApiError(404, "no such settings endpoint")
    except ApiError as e:
        send_json(handler, e.status, {"error": e.msg, **e.extra})
    except OSError as e:
        send_json(handler, 500, {"error": f"{type(e).__name__}: {e}"})
    return True
