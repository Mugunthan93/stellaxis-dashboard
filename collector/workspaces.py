"""Command centre: group folders into configured workspaces.

A workspace is {"id", "name", "folders": [path]}; a path belongs to the first workspace with a folder that
is the path itself or a parent of it (whole path components, so ph-admin-old is not under ph-admin).
"""
from __future__ import annotations

import json
import os
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
