"""Command centre: group folders into configured workspaces.

A workspace is {"id", "name", "folders": [path]}; a path belongs to the first workspace with a folder that
is the path itself or a parent of it (whole path components, so ph-admin-old is not under ph-admin).
"""
from __future__ import annotations

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
