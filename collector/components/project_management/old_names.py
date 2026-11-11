"""The names from before 2026-11-11, kept as aliases: workspace (now project), its "folders" (now "repos")
and the group "other" (now "unassigned"). See "Names during the move" in the core spec.
This is the only code that may say the old word.
"""
from __future__ import annotations

import re

ROUTE = "/api/workspaces"
INDEX_FILE = "workspaces.json"


def _id(pid):
    return "unassigned" if pid == "other" else pid


def config(cfg: dict) -> dict:
    """The config with the new keys: an old "workspaces" list becomes "projects", each entry's "folders" its "repos"."""
    if "workspaces" not in cfg:
        return cfg
    old = cfg["workspaces"]
    new = [{("repos" if k == "folders" else k): v for k, v in e.items()} if isinstance(e, dict) else e for e in old] if isinstance(old, list) else old
    return {**cfg, "projects": new}


def path(path: str) -> str | None:
    """The new route for an old one ("/api/workspaces/other/tasks" -> "/api/projects/unassigned/tasks"), else None."""
    if path == ROUTE:
        return "/api/projects"
    m = re.fullmatch(r"/api/workspaces/([^/]+)(/.+)", path)
    return f"/api/projects/{_id(m.group(1))}{m.group(2)}" if m else None


def page(path: str, query: str) -> str | None:
    """The new address for an old page address, the query kept as it came except id=other; else None."""
    if path not in ("/workspace", "/workspace.html"):
        return None
    new = "/project" + path[len("/workspace"):]
    if query:
        new += "?" + "&".join("id=unassigned" if q == "id=other" else q for q in query.split("&"))
    return new


def body(b: dict) -> dict:
    """A POST /api/sessions/new body with "workspace" given as "project"; a body with no old key is returned as it is."""
    if "workspace" not in b:
        return b
    return {**{k: v for k, v in b.items() if k != "workspace"}, "project": _id(b["workspace"])}


def answer(index: dict) -> dict:
    """The index (or listing) in its old shape: "projects" given as "workspaces", the unassigned group as "other"."""
    old = [{**p, "id": "other", "name": "Other"} if isinstance(p, dict) and p.get("id") == "unassigned" else p
           for p in index.get("projects") or []]
    return {**{k: v for k, v in index.items() if k != "projects"}, "workspaces": old}
