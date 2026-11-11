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


def _rekey(entries, a: str, b: str):
    return [{(b if k == a else k): v for k, v in e.items()} if isinstance(e, dict) else e for e in entries] if isinstance(entries, list) else entries


def _shape(user: dict) -> str | None:
    """"main" (a "workspaces" key), "cards" (only hand-written cards under "projects"), or None (nothing to rename)."""
    if "workspaces" in user:
        return None if "project_notes" in user else "main"
    cards = user.get("projects")
    if "project_notes" not in user and isinstance(cards, list) and cards and not any(isinstance(c, dict) and c.get("id") for c in cards):
        return "cards"
    return None


def changes(user: dict) -> list[str]:
    """What to_new would change in the content of config.json, one line each."""
    shape = _shape(user)
    out = []
    if shape == "main":
        old = user["workspaces"] if isinstance(user["workspaces"], list) else []
        out.append(f'config.json: "workspaces" becomes "projects" ({len(old)} entries)')
        n = sum(1 for e in old if isinstance(e, dict) and "folders" in e)
        if n:
            out.append(f'config.json: "folders" becomes "repos" in {n} entries')
    if shape and "projects" in user:
        out.append(f'config.json: "projects" becomes "project_notes" ({len(user["projects"]) if isinstance(user["projects"], list) else 0} notes cards)')
    return out


def to_new(user: dict) -> dict:
    """The content with the new keys, key order kept; the argument is not changed."""
    shape = _shape(user)
    if not shape:
        return user
    out = {}
    for k, v in user.items():
        if k == "workspaces":
            out["projects"] = _rekey(v, "folders", "repos")
        elif k == "projects":
            if shape == "cards":
                out["projects"] = []
            out["project_notes"] = v
        else:
            out[k] = v
    return out


def to_old(user: dict) -> dict:
    """The reverse of to_new, for undo; key order kept."""
    if "workspaces" in user or "projects" not in user:
        return user
    out = {}
    for k, v in user.items():
        if k == "projects":
            out["workspaces"] = _rekey(v, "repos", "folders")
        elif k == "project_notes":
            out["projects"] = v
        else:
            out[k] = v
    return out
