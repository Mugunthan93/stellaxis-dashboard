"""Command centre: group folders into configured workspaces.

A workspace is {"id", "name", "folders": [path]}; a path belongs to the first workspace with a folder that
is the path itself or a parent of it (whole path components, so ph-admin-old is not under ph-admin).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings_api


def _norm(p: str) -> Path:
    return Path(os.path.normpath(os.path.expanduser(p)))


def assign(path: str, workspaces: list[dict]) -> str:
    p = _norm(path)
    for w in workspaces:
        for f in w.get("folders", []):
            root = _norm(f)
            if p == root or root in p.parents:
                return w["id"]
    return "other"


def app_records(base: Path) -> dict[str, dict]:
    """Desktop app session records under base, keyed by cliSessionId; unreadable or id-less files are skipped."""
    out = {}
    for f in Path(base).rglob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not d.get("cliSessionId"):
            continue
        out[d["cliSessionId"]] = {"app_id": d.get("sessionId"), "title": d.get("title"), "branch": d.get("branch"),
                                  "source_branch": d.get("sourceBranch"), "created_ms": d.get("createdAt"),
                                  "archived": bool(d.get("isArchived"))}
    return out


def app_base() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"


def repo_state(path: str) -> dict:
    """{"remote", "ahead", "dirty"} for a repo: has a remote, commits ahead of upstream, changed files."""
    def git(*args):
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if r.returncode:
            raise ValueError(r.stderr.strip() or "git failed")
        return r.stdout
    try:
        lines = git("status", "--porcelain=v1", "-b").splitlines()
        remote = bool(git("remote").strip())
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
    head = lines[0] if lines else ""
    m = re.search(r"\[ahead (\d+)", head)
    return {"remote": remote, "ahead": int(m.group(1)) if m else 0,
            "dirty": sum(1 for l in lines if not l.startswith("##"))}


_STATE_CACHE: dict[str, tuple[float, dict]] = {}  # path > (time computed, repo_state); errors are cached too
STATE_TTL = 300


def repo_states(paths: list[str]) -> dict[str, dict]:
    """repo_state per path, cached for STATE_TTL seconds; misses run in a small thread pool."""
    now = time.monotonic()
    miss = [p for p in dict.fromkeys(paths) if p not in _STATE_CACHE or now - _STATE_CACHE[p][0] >= STATE_TTL]
    if miss:
        with ThreadPoolExecutor(max_workers=min(8, len(miss))) as ex:
            for p, st in zip(miss, ex.map(repo_state, miss)):
                _STATE_CACHE[p] = (time.monotonic(), st)
    return {p: _STATE_CACHE[p][1] for p in paths}


def build_index(sessions: list, projects: list, app: dict, cfg: dict, now: str | None = None) -> dict:
    """Every session grouped workspace > repo > branch, sessions newest first; `now` (ISO) is for tests."""
    now_dt = datetime.fromisoformat(now.replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
    cut = (now_dt - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    wss = list(cfg.get("workspaces") or [])
    by_path = {p["path"]: p for p in projects}
    tree: dict = {}  # workspace id > repo path > branch > [session]
    cost = {}
    for s in sessions:
        proj = by_path.get(s["project"]) or {}
        repo = proj.get("worktree_of") or s["project"].split("/.claude/worktrees/")[0]  # deleted worktrees have no worktree_of
        a = app.get(s["id"]) or {}
        wid = assign(repo, wss)
        row = {"id": s["id"], "title": a.get("title") or "", "first_ts": s.get("first_ts"), "last_ts": s.get("last_ts"),
               "cost": s.get("cost") or 0, "app_id": a.get("app_id"), "archived": bool(a.get("archived")),
               "path": proj.get("abs") or s["project"]}
        branch = a.get("branch") or s.get("branch") or "(no branch)"
        tree.setdefault(wid, {}).setdefault(repo, {}).setdefault(branch, []).append(row)
        if (s.get("last_ts") or "")[:19] >= cut:
            cost[wid] = cost.get(wid, 0) + row["cost"]
    states = repo_states([str(_norm(r)) for repos in tree.values() for r in repos])
    out = []
    for w in [*wss, {"id": "other", "name": "Other"}]:
        repos = []
        for repo, branches in tree.get(w["id"], {}).items():
            bl = []
            for name, rows in branches.items():
                rows.sort(key=lambda r: r["first_ts"] or "", reverse=True)
                bl.append({"name": name, "sessions": rows})
            bl.sort(key=lambda b: max(r["last_ts"] or "" for r in b["sessions"]), reverse=True)
            ab = _norm(repo)
            repos.append({"path": repo, "exists": ab.exists(), "state": states[str(ab)], "branches": bl})
        out.append({"id": w["id"], "name": w.get("name") or w["id"], "cost_30d": round(cost.get(w["id"], 0), 2),
                    "repos": repos})
    return {"generated_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "workspaces": out}


def live(claude_bin: str, timeout: float = 5) -> list[dict] | None:
    """Rows of `claude agents --json`; None when the CLI is missing, slow, fails or prints something else."""
    try:
        r = subprocess.run([os.path.expanduser(claude_bin), "agents", "--json"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return rows if isinstance(rows, list) else None


def status_of(row: dict | None) -> str:
    if row is None:
        return "closed"
    if row.get("state") == "blocked" or row.get("status") == "waiting":
        return "waiting"
    return "running" if row.get("status") == "busy" else "open"


def handle(handler, method: str, cfg: dict) -> bool:
    """Serve GET /api/workspaces: the index plus live status per session. False when the path is not ours."""
    if method != "GET" or handler.path.split("?")[0] != "/api/workspaces":
        return False
    home = Path(os.environ.get("STELLAXIS_HOME", "~/.stellaxis")).expanduser()
    try:
        idx = json.loads((home / "workspaces.json").read_text())
    except (OSError, ValueError):
        idx = {"workspaces": []}
    rows = live(cfg.get("claude_bin") or "~/.local/bin/claude")
    by_id = {r.get("sessionId"): r for r in rows or [] if isinstance(r, dict)}
    for w in idx.get("workspaces", []):
        for repo in w.get("repos", []):
            for b in repo.get("branches", []):
                for s in b.get("sessions", []):
                    s["status"] = None if rows is None else status_of(by_id.get(s["id"]))
    idx["live_ok"] = rows is not None
    settings_api._send(handler, 200, idx)
    return True
