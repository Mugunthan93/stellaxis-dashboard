"""The repos of this Mac: the folders of the sessions, of `~/.claude.json` and of the folder walk."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import core.util
from core.util import parse_duration, read_json, short_path


def read_text(p: Path, limit: int = 2_000_000) -> str:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def worktree_of(P: Path):
    """Main repo of a git worktree (its .git is a file pointing into <repo>/.git/worktrees/<name>)."""
    g = P / ".git"
    if g.is_file():
        m = re.match(r"gitdir:\s*(.+)", read_text(g, 4096).strip())
        if m:
            gd = Path(m.group(1).strip())
            gd = gd if gd.is_absolute() else (P / gd).resolve()
            if gd.parent.name == "worktrees":
                return short_path(gd.parent.parent.parent)
    parts = P.parts
    if ".claude" in parts and "worktrees" in parts:
        i = parts.index(".claude")
        return short_path(Path(*parts[:i]))
    return None


def _folders(ctx, transcript_cwds: set, claude_json: dict) -> list:
    cfg, cache = ctx.cfg, ctx.cache
    roots = set(p for p in transcript_cwds if p)
    roots |= set((claude_json.get("projects") or {}).keys())
    walk = cache.get("walk") or {}
    if time.time() - walk.get("at", 0) > parse_duration(cfg.get("rescan_every", "1h"), 3600):
        found = set()
        excl = set(cfg.get("exclude_dirs", []))
        for r in cfg.get("scan_roots", []):
            base = Path(os.path.expanduser(r))
            base_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).parts) - base_depth
                if depth <= 2:
                    ctx.status("scanning folders", force=False, path=short_path(dirpath))
                dirnames[:] = [d for d in dirnames if d not in excl and not (d.startswith(".") and d != ".claude")]
                if depth >= cfg.get("scan_depth", 6):
                    dirnames[:] = [d for d in dirnames if d == ".claude"]
                if Path(dirpath) == core.util.HOME:
                    dirnames[:] = [d for d in dirnames if d != ".claude"]
                    continue
                if Path(dirpath).name == ".claude":
                    dirnames[:] = []
                    continue
                if ".claude" in dirnames or "CLAUDE.md" in filenames or ".mcp.json" in filenames:
                    found.add(dirpath)
        walk = cache["walk"] = {"at": time.time(), "dirs": sorted(found)}
    strong = set(roots)  # session cwds and ~/.claude.json projects always count
    walked = set(walk.get("dirs", []))
    for d in sorted(walked):
        # a folder with only a nested CLAUDE.md inside another project is that project's lazy-loaded file
        inside = any(d != r and d.startswith(r.rstrip(os.sep) + os.sep) for r in strong | walked)
        if not inside or (Path(d) / ".claude").is_dir():
            strong.add(d)
    return sorted(r for r in strong if Path(r).is_dir() and Path(r) != core.util.HOME and not str(r).startswith(str(ctx.claude_dir)))


def discover(ctx, cwds: set) -> list[dict]:
    """[{"path", "abs", "raw", "has_git", "worktree_of"}] for every repo found, sorted by `raw` (the string as found)."""
    rows = []
    for raw in _folders(ctx, cwds, read_json(ctx.claude_json, {}) or {}):
        P = Path(raw)
        wt = worktree_of(P)
        rows.append({"path": short_path(P), "abs": str(P), "raw": raw, "has_git": True if wt else (P / ".git").exists(),
                     "worktree_of": wt})
    return rows
