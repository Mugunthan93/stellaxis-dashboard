"""The tables of Project management: the repos found, and the project index without its costs.

The index rows name a project by its place (`pos` of the projects table), not its id: a config may repeat an id,
and a config project may be named `unassigned`; the index then holds that group twice, as `centre.build_index` does.

Each table is written whole, in one transaction, in every collection. The cost of a session belongs to Claude
management: `index` takes it as an argument.
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta

from core.app import StoreError

RECORDS = [
    "CREATE TABLE IF NOT EXISTS project_management_repos (pos INTEGER PRIMARY KEY, path TEXT NOT NULL, abs TEXT NOT NULL, "
    "raw TEXT NOT NULL, has_git INTEGER NOT NULL, worktree_of TEXT)",
    "CREATE TABLE IF NOT EXISTS project_management_runs (id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS project_management_projects (pos INTEGER PRIMARY KEY, id TEXT NOT NULL, name TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS project_management_index_repos (project INTEGER NOT NULL, pos INTEGER NOT NULL, path TEXT NOT NULL, "
    "abs TEXT NOT NULL, here INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY (project, pos))",
    "CREATE TABLE IF NOT EXISTS project_management_sessions (id TEXT NOT NULL, project INTEGER NOT NULL, repo INTEGER NOT NULL, "
    "branch TEXT NOT NULL, branch_pos INTEGER NOT NULL, pos INTEGER NOT NULL, seq INTEGER NOT NULL, title TEXT NOT NULL, "
    "first_ts TEXT, last_ts TEXT, app_id TEXT, archived INTEGER NOT NULL, path TEXT NOT NULL, "
    "PRIMARY KEY (project, repo, branch_pos, pos))",
]


def save_repos(ctx, rows: list) -> None:
    """The rows of `repos.discover`, in their order; nothing when the store is down."""
    try:
        db = ctx.db()
    except StoreError:
        return
    with closing(db), db:
        db.execute("DELETE FROM project_management_repos")
        db.executemany("INSERT INTO project_management_repos (pos, path, abs, raw, has_git, worktree_of) VALUES (?, ?, ?, ?, ?, ?)",
                       [(pos, r["path"], r["abs"], r["raw"], int(r["has_git"]), r["worktree_of"]) for pos, r in enumerate(rows)])


def repos(ctx) -> list:
    with closing(ctx.db()) as db:
        return [{"path": r["path"], "abs": r["abs"], "raw": r["raw"], "has_git": bool(r["has_git"]), "worktree_of": r["worktree_of"]}
                for r in db.execute("SELECT * FROM project_management_repos ORDER BY pos")]


def save_index(ctx, index: dict, seq: dict) -> None:
    """The index of `centre.build_index` without its costs; `seq` is each session's place in the list it was built from.
    Nothing when the store is down."""
    try:
        db = ctx.db()
    except StoreError:
        return
    with closing(db), db:
        for table in ("runs", "projects", "index_repos", "sessions"):
            db.execute(f"DELETE FROM project_management_{table}")
        db.execute("INSERT INTO project_management_runs (id, generated_at) VALUES (1, ?)", (index["generated_at"],))
        for ppos, p in enumerate(index["projects"]):
            db.execute("INSERT INTO project_management_projects (pos, id, name) VALUES (?, ?, ?)", (ppos, p["id"], p["name"]))
            for rpos, r in enumerate(p["repos"]):
                db.execute("INSERT INTO project_management_index_repos (project, pos, path, abs, here, state) VALUES (?, ?, ?, ?, ?, ?)",
                           (ppos, rpos, r["path"], r["abs"], int(r["exists"]), json.dumps(r["state"])))
                for bpos, b in enumerate(r["branches"]):
                    for spos, s in enumerate(b["sessions"]):
                        db.execute("INSERT INTO project_management_sessions (id, project, repo, branch, branch_pos, pos, seq, title, "
                                   "first_ts, last_ts, app_id, archived, path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   (s["id"], ppos, rpos, b["name"], bpos, spos, seq[s["id"]], s["title"], s["first_ts"],
                                    s["last_ts"], s["app_id"], int(s["archived"]), s["path"]))


def index(ctx, costs: dict) -> dict | None:
    """The dict `centre.build_index` returned, rebuilt from the rows with `costs` (session id -> cost); None when the
    store is down or holds no index."""
    try:
        db = ctx.db()
    except StoreError:
        return None
    with closing(db):
        db.execute("BEGIN")   # one read transaction: every SELECT sees the same pass
        try:
            return _index(db, costs)
        finally:
            db.rollback()


def _index(db, costs: dict) -> dict | None:
    run = db.execute("SELECT generated_at FROM project_management_runs WHERE id = 1").fetchone()
    if run is None:
        return None
    cut = (datetime.strptime(run["generated_at"], "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    total: dict = {}
    for s in db.execute("SELECT id, project, last_ts FROM project_management_sessions ORDER BY seq"):
        if (s["last_ts"] or "")[:19] >= cut:
            total[s["project"]] = total.get(s["project"], 0) + (costs.get(s["id"]) or 0)
    repos: dict = {}   # project pos -> [repo]
    for r in db.execute("SELECT * FROM project_management_index_repos ORDER BY project, pos"):
        repos.setdefault(r["project"], []).append({"path": r["path"], "abs": r["abs"], "exists": bool(r["here"]),
                                                   "state": json.loads(r["state"]), "branches": []})
    for s in db.execute("SELECT * FROM project_management_sessions ORDER BY project, repo, branch_pos, pos"):
        branches = repos[s["project"]][s["repo"]]["branches"]
        if len(branches) == s["branch_pos"]:
            branches.append({"name": s["branch"], "sessions": []})
        branches[s["branch_pos"]]["sessions"].append(
            {"id": s["id"], "title": s["title"], "first_ts": s["first_ts"], "last_ts": s["last_ts"],
             "cost": costs.get(s["id"]) or 0, "app_id": s["app_id"], "archived": bool(s["archived"]), "path": s["path"]})
    return {"generated_at": run["generated_at"],
            "projects": [{"id": p["id"], "name": p["name"], "cost_30d": round(total.get(p["pos"], 0), 2), "repos": repos.get(p["pos"], [])}
                         for p in db.execute("SELECT pos, id, name FROM project_management_projects ORDER BY pos")]}


def sessions(ctx) -> list:
    """[{"id", "project", "repo", "branch", "first_ts", "last_ts"}] of the index, in the order it was built from; a session
    the index holds twice (a repeated project id) is listed once, where it comes first."""
    with closing(ctx.db()) as db:
        out: dict = {}
        for r in db.execute(
                "SELECT s.id, p.id AS project, r.path AS repo, s.branch, s.first_ts, s.last_ts FROM project_management_sessions s "
                "JOIN project_management_projects p ON p.pos = s.project "
                "JOIN project_management_index_repos r ON r.project = s.project AND r.pos = s.repo "
                "ORDER BY s.seq, s.project, s.repo, s.branch_pos, s.pos"):
            out.setdefault(r["id"], dict(r))
        return list(out.values())
