"""Memory management: measures where the disk space of this Mac goes, in the background, and answers GET /api/memory."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import closing
from pathlib import Path

from core.app import StoreError
from core.component import Component
from core.util import now_iso

from . import records, walk

DEFAULT_PLACES = [
    ("code", "~/code", "project_management", 1, True),
    ("claude", "", "claude_management", 2, False),
    ("claude_app", "~/Library/Application Support/Claude", "claude_management", 1, False),
    ("stellaxis", "", "stellaxis", 1, False),
    ("knowledge", "~/Knowledge", "none", 2, False),
    ("archive", "~/Archive", "none", 1, False),
    ("xcode", "~/Library/Developer", "none", 3, False),
    ("android_sdk", "~/Library/Android", "none", 2, False),
    ("android", "~/.android", "none", 1, False),
    ("caches", "~/Library/Caches", "none", 1, False),
    ("homebrew", "/opt/homebrew", "none", 1, False),
    ("local", "~/.local", "none", 2, False),
] + [(n, p, "none", 1, False) for n, p in [
    ("pnpm", "~/Library/pnpm"), ("npm", "~/.npm"), ("bun", "~/.bun"), ("yarn", "~/.yarn"), ("cache", "~/.cache"),
    ("rustup", "~/.rustup"), ("cargo", "~/.cargo"), ("m2", "~/.m2"), ("gradle", "~/.gradle"),
    ("cocoapods", "~/.cocoapods"), ("nvm", "~/.nvm"), ("ollama", "~/.ollama"), ("docker", "~/.docker"),
    ("vscode", "~/.vscode")]]
IDLE = "no places to measure: the Stellaxis data folder is outside the home folder and config names no places"
_JOBS: dict = {}
_LOCK = threading.Lock()


def duration(text, default: float) -> float:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if text >= 0 else default
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text) if isinstance(text, str) else None
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else default


def settings(cfg: dict, stx_home) -> dict:
    m = cfg.get("memory") if isinstance(cfg, dict) else None
    m = m if isinstance(m, dict) else {}
    home = Path.home()

    def expand(p: str) -> str:
        return os.path.abspath(str(home / p[2:]) if p.startswith("~/") else str(home) if p == "~" else p)

    top, here = os.path.realpath(home).rstrip(os.sep), os.path.realpath(stx_home)
    inside = here == top or here.startswith(top + os.sep)
    places: dict = {}
    if inside:
        for name, path, owner, depth, repos in DEFAULT_PLACES:
            path = (os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude" if name == "claude"
                    else str(stx_home) if name == "stellaxis" else path)
            places[name] = {"name": name, "path": expand(path), "owner": owner, "depth": depth, "repos": repos}
    given = m.get("places")
    for e in given if isinstance(given, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str):
            continue
        if e.get("off") is True:
            places.pop(e["name"], None)
        elif isinstance(e.get("path"), str):
            depth = e.get("depth")
            places[e["name"]] = {
                "name": e["name"], "path": expand(e["path"]),
                "owner": e["owner"] if isinstance(e.get("owner"), str) else "none",
                "depth": depth if isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth <= 6 else 1,
                "repos": e.get("repos") is True}
    extra = m.get("guarded")
    guard = walk.Guard([str(home / g) for g in walk.GUARDED_HOME] + walk.GUARDED_ABS
                       + [expand(g) for g in (extra if isinstance(extra, list) else []) if isinstance(g, str)])
    return {"every": max(60.0, duration(m.get("every"), 6 * 3600)),
            "max_seconds": max(1.0, duration(m.get("max_seconds"), 900)),
            "places": list(places.values()), "guard": guard, "idle": None if places else IDLE}


def _measure(ctx, s: dict, job: dict) -> None:
    try:
        try:
            st = os.statvfs(ctx.home)
            disk = {"path": str(ctx.home), "total": st.f_frsize * st.f_blocks, "free": st.f_frsize * st.f_bavail}
        except OSError:
            disk = None
        t0 = time.monotonic()
        result = walk.measure(s["places"], s["guard"], t0 + s["max_seconds"], job["progress"])
        if (ctx.home / "stellaxis.db").exists():
            run = {"started_at": job["started_at"], "finished_at": now_iso(), "finished_ts": time.time(),
                   "seconds": time.monotonic() - t0, "disk": disk}
            with closing(ctx.db()) as db, db:
                records.save(db, run, result)
        error = None
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
    with _LOCK:
        job["error"] = error


def measure_reader(ctx) -> None:
    s = settings(ctx.cfg, ctx.home)
    if not s["places"]:
        return
    with _LOCK:
        job = _JOBS.setdefault(str(ctx.home), {"thread": None, "started_at": None, "progress": {},
                                               "last_try": None, "error": None})
        if job["thread"] is not None and job["thread"].is_alive():
            return
        if job["last_try"] is None:
            with closing(ctx.db()) as db:
                job["last_try"] = records.last_finished_ts(db)
        if job["last_try"] is not None and time.time() - job["last_try"] < s["every"]:
            return
        job["last_try"], job["started_at"], job["progress"] = time.time(), now_iso(), {}
        job["thread"] = threading.Thread(target=_measure, args=(ctx, s, job), name="memory-measure", daemon=True)
        job["thread"].start()


def answer(ctx) -> dict:
    with _LOCK:
        job = _JOBS.get(str(ctx.home))
        alive = job is not None and job["thread"] is not None and job["thread"].is_alive()
        measuring = {"started_at": job["started_at"], "place": job["progress"].get("place"),
                     "files": job["progress"].get("files", 0)} if alive else None
        error = job["error"] if job else None
    with closing(ctx.db()) as db:
        got = records.last(db)
    out = {"state": "never", "measured_at": None, "started_at": None, "seconds": None, "complete": None,
           "note": None, "measuring": measuring, "disk": None, "places": [], "folders": [],
           "elsewhere_bytes": None, "not_measured": [], "not_measured_count": 0,
           "idle": settings(ctx.cfg, ctx.home)["idle"], "error": error}
    if got:
        run, rows = got["run"], got["rows"]
        disk = run["disk"] and dict(run["disk"], used=run["disk"]["total"] - run["disk"]["free"])
        out.update(
            state="measured", measured_at=run["finished_at"], started_at=run["started_at"], seconds=run["seconds"],
            complete=run["complete"], note=run["note"], disk=disk,
            places=[r for r in rows if r["kind"] == "place"], folders=[r for r in rows if r["kind"] != "place"],
            not_measured=got["skipped"], not_measured_count=run["skipped"])
        if disk:
            out["elsewhere_bytes"] = disk["used"] - sum(r["bytes"] for r in out["places"])
    return out


def handle(handler, method: str, ctx) -> bool:
    if method != "GET" or handler.path.split("?")[0] != "/api/memory":
        return False
    try:
        status, obj = 200, answer(ctx)
    except StoreError as e:
        status, obj = 503, {"error": str(e)}
    body = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


COMPONENT = Component(name="memory_management", readers=[measure_reader], records=records.RECORDS, handle=handle)
