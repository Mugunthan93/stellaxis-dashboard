from __future__ import annotations

# The shared transcript helper: reads every new line of `~/.claude/projects/**/*.jsonl` one time.
# The state of each file lives in `cache["files"]` (the format of `cache.json`): the head facts kept here,
# then the keys of each consumer, then `v`. Each consumer's `line` gets every new line once.

import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VERSION = "0.2.2"   # the version of a file's state; a state with another `v` is read again from the start
HEAD = ("path", "offset", "size", "session", "cwd", "first_ts", "last_ts", "entrypoint", "version", "branch", "sidechain")


@dataclass
class Consumer:
    fresh: Callable[[], dict]                         # the keys it keeps in a file's state, with their start values
    line: Callable[[dict, dict, dict, int], None]     # (state, line, ids, owner), for each new line


def owner(key: str) -> int:
    return zlib.crc32(key.encode())


def _new_state(path: str, consumers: list[Consumer]) -> dict:
    st = {"path": path, "offset": 0, "size": 0, "session": None, "cwd": None, "first_ts": None,
          "last_ts": None, "entrypoint": None, "version": None, "branch": None,
          "sidechain": "/subagents/" in path}
    for c in consumers:
        st.update(c.fresh())
    st["v"] = VERSION
    return st


def _head(st: dict, d: dict):
    ts = d.get("timestamp")
    if ts:
        st["first_ts"] = st["first_ts"] or ts
        st["last_ts"] = ts
    st["session"] = st["session"] or d.get("sessionId")
    st["cwd"] = st["cwd"] or d.get("cwd")
    st["entrypoint"] = st["entrypoint"] or d.get("entrypoint")
    st["version"] = d.get("version") or st["version"]
    st["branch"] = d.get("gitBranch") or st["branch"]


def read(projects_dir: Path, cache: dict, consumers: list[Consumer]) -> dict:
    wanted = [k for c in consumers for k in c.fresh()]
    clash = sorted({k for k in wanted if k in HEAD or k == "v" or wanted.count(k) > 1})
    if clash:
        raise ValueError(f"consumer keys clash with the head or with each other: {clash}")
    files = cache.setdefault("files", {})
    ids = cache.setdefault("ids", {})  # call id -> owning file, across all files; a replay is not new usage
    keep = set(HEAD) | {"v"} | set(wanted)
    found, seen = [], set()
    for p in projects_dir.rglob("*.jsonl") if projects_dir.exists() else []:
        seen.add(str(p))
        try:
            s = p.stat()
        except OSError:
            continue
        found.append((s.st_mtime, str(p), s.st_size, p))
    found.sort(key=lambda x: x[:3])  # oldest first, so the original of a replay is the one counted
    sizes = {k: size for _, k, size, _ in found}
    stale = {k for k, st in files.items()
             if k not in seen or sizes.get(k, st["size"]) < st["size"] or st.get("v") != VERSION
             or any(w not in st for w in wanted)}
    if stale:
        gone = {owner(k) for k in stale}
        cache["ids"] = ids = {i: o for i, o in ids.items() if o not in gone}
    for _, key, size, p in found:
        st = files.get(key)
        if st is None or key in stale:
            st = files[key] = _new_state(key, consumers)
        if size == st["offset"]:
            continue
        try:
            with open(p, "rb") as f:
                f.seek(st["offset"])
                chunk = f.read(size - st["offset"])
        except OSError:
            continue
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            continue
        for k in [k for k in st if k not in keep]:   # the keys of a consumer that is gone
            del st[k]
        own = owner(key)
        for raw in chunk[:last_nl].splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            try:
                _head(st, d)
            except (ValueError, TypeError, AttributeError):
                pass
            for c in consumers:
                try:
                    c.line(st, d, ids, own)
                except (ValueError, TypeError, AttributeError):
                    pass
        st["offset"] += last_nl + 1
        st["size"] = size
    for k in list(files):
        if k not in seen:
            del files[k]
    return files


def sessions(files: dict) -> dict:
    """Session id -> its plain facts and its file states, in the order a session first appears in `files`."""
    out: dict = {}
    for st in files.values():
        sid = st["session"] or Path(st["path"]).stem
        s = out.setdefault(sid, {"id": sid, "cwd": None, "branch": None, "first_ts": None, "last_ts": None,
                                 "entrypoint": None, "version": None, "subagent_files": 0, "files": []})
        if st["sidechain"]:
            s["subagent_files"] += 1
        else:
            s["cwd"] = s["cwd"] or st["cwd"]
            s["entrypoint"] = s["entrypoint"] or st["entrypoint"]
            s["branch"] = st["branch"] or s["branch"]
        s["cwd"] = s["cwd"] or st["cwd"]
        s["version"] = st["version"] or s["version"]
        if st["first_ts"] and (not s["first_ts"] or st["first_ts"] < s["first_ts"]):
            s["first_ts"] = st["first_ts"]
        if st["last_ts"] and (not s["last_ts"] or st["last_ts"] > s["last_ts"]):
            s["last_ts"] = st["last_ts"]
        s["files"].append(st)
    return out
