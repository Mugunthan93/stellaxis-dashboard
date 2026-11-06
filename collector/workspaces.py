"""Command centre: group folders into configured workspaces.

A workspace is {"id", "name", "folders": [path]}; a path belongs to the first workspace with a folder that
is the path itself or a parent of it (whole path components, so ph-admin-old is not under ph-admin).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


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
        repo = proj.get("worktree_of") or s["project"]
        a = app.get(s["id"]) or {}
        wid = assign(repo, wss)
        row = {"id": s["id"], "title": a.get("title") or "", "first_ts": s.get("first_ts"), "last_ts": s.get("last_ts"),
               "cost": s.get("cost") or 0, "app_id": a.get("app_id"), "archived": bool(a.get("archived")),
               "path": proj.get("abs") or s["project"]}
        branch = a.get("branch") or s.get("branch") or "(no branch)"
        tree.setdefault(wid, {}).setdefault(repo, {}).setdefault(branch, []).append(row)
        if (s.get("last_ts") or "")[:19] >= cut:
            cost[wid] = cost.get(wid, 0) + row["cost"]
    out = []
    for w in [*wss, {"id": "other", "name": "Other"}]:
        repos = []
        for repo, branches in tree.get(w["id"], {}).items():
            bl = []
            for name, rows in branches.items():
                rows.sort(key=lambda r: r["first_ts"] or "", reverse=True)
                bl.append({"name": name, "sessions": rows})
            bl.sort(key=lambda b: max(r["last_ts"] or "" for r in b["sessions"]), reverse=True)
            repos.append({"path": repo, "exists": _norm(repo).exists(), "state": {}, "branches": bl})
        out.append({"id": w["id"], "name": w.get("name") or w["id"], "cost_30d": round(cost.get(w["id"], 0), 2),
                    "repos": repos})
    return {"generated_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "workspaces": out}
