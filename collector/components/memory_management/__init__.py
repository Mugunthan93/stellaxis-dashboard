"""Memory management: measures where the disk space of this Mac goes, in a child process, and answers GET /api/memory."""
from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from core.app import StoreError
from core.component import Component
from core.server import send_json
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
    ("applications", "/Applications", "none", 1, False),
    ("local", "~/.local", "none", 2, False),
] + [(n, p, "none", 1, False) for n, p in [
    ("pnpm", "~/Library/pnpm"), ("npm", "~/.npm"), ("bun", "~/.bun"), ("yarn", "~/.yarn"), ("cache", "~/.cache"),
    ("rustup", "~/.rustup"), ("cargo", "~/.cargo"), ("m2", "~/.m2"), ("gradle", "~/.gradle"),
    ("cocoapods", "~/.cocoapods"), ("nvm", "~/.nvm"), ("ollama", "~/.ollama"), ("docker", "~/.docker"),
    ("vscode", "~/.vscode")]]
IDLE = ("no places to measure: the Stellaxis data folder is not .stellaxis in the account's home folder, "
        "and config names no places")
ALL_OFF = "no places to measure: config turned every place off"
CHILD = [sys.executable, "-I", str(Path(__file__).resolve().parent / "walk.py")]   # the walk runs there, not in the service
GRACE = 30.0    # a child that has not answered this long (plus a tenth of max_seconds) past max_seconds is killed
_JOBS: dict = {}
_LOCK = threading.Lock()


def duration(text, default: float) -> float:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if text >= 0 else default
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text) if isinstance(text, str) else None
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else default


def account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def settings(cfg: dict, stx_home, claude_dir=None) -> dict:
    m = cfg.get("memory") if isinstance(cfg, dict) else None
    m = m if isinstance(m, dict) else {}
    home = account_home()

    def expand(p: str):
        """An absolute path, or None: a path that is not absolute after ~ is expanded is dropped."""
        p = str(home / p[2:]) if p.startswith("~/") else str(home) if p == "~" else p
        return os.path.normpath(p) if os.path.isabs(p) else None

    on = os.path.realpath(stx_home) == os.path.realpath(home / ".stellaxis")
    places: dict = {}
    if on:
        for name, path, owner, depth, repos in DEFAULT_PLACES:
            path = expand(str(claude_dir or "~/.claude") if name == "claude"
                          else str(stx_home) if name == "stellaxis" else path)
            if path:
                places[name] = {"name": name, "path": path, "owner": owner, "depth": depth, "repos": repos}
    given = m.get("places")
    for e in given if isinstance(given, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str):
            continue
        if e.get("off") is True:
            places.pop(e["name"], None)
        elif isinstance(e.get("path"), str) and expand(e["path"]):
            was, depth = places.get(e["name"], {"owner": "none", "depth": 1, "repos": False}), e.get("depth")
            places[e["name"]] = {
                "name": e["name"], "path": expand(e["path"]),
                "owner": e["owner"] if isinstance(e.get("owner"), str) else was["owner"],
                "depth": depth if isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth <= 6
                else was["depth"],
                "repos": e["repos"] if isinstance(e.get("repos"), bool) else was["repos"]}
    extra = m.get("guarded")
    homes = dict.fromkeys([str(home), os.path.realpath(home)])
    guarded = list(dict.fromkeys([os.path.join(h, g) for h in homes for g in walk.GUARDED_HOME] + walk.GUARDED_ABS
                                 + [expand(g) for g in (extra if isinstance(extra, list) else [])
                                    if isinstance(g, str) and expand(g)]))
    return {"every": max(60.0, duration(m.get("every"), 6 * 3600)),
            "max_seconds": max(1.0, duration(m.get("max_seconds"), 1800)),
            "places": list(places.values()), "guarded": guarded,
            "idle": None if places else ALL_OFF if on else IDLE}


def _watch(ctx, s: dict, job: dict) -> None:
    """Start the child, follow what it says, save its result. The child is gone when this returns."""
    try:
        try:
            st = os.statvfs(ctx.home)
            disk = {"path": str(ctx.home), "total": st.f_frsize * st.f_blocks, "free": st.f_frsize * st.f_bavail}
        except OSError:
            disk = None
        t0, late, result, error, noise = time.monotonic(), threading.Event(), None, None, ""
        limit = s["max_seconds"] * 1.1 + GRACE      # the child stops its own walk at max_seconds; this is for one that hangs
        with subprocess.Popen(CHILD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
            job["pid"] = proc.pid
            timer = threading.Timer(limit, lambda: (late.set(), proc.kill()))
            timer.daemon = True
            timer.start()
            try:
                spec = {"places": s["places"], "guarded": s["guarded"], "max_seconds": s["max_seconds"]}
                try:
                    proc.stdin.write((json.dumps(spec) + "\n").encode())
                    proc.stdin.flush()      # stdin stays open: the child ends itself when it closes, also if we die
                except BrokenPipeError:     # it ended before it read: what it wrote says why
                    pass
                for line in proc.stdout:
                    try:
                        said = json.loads(line)
                    except ValueError:
                        said = None
                    if not isinstance(said, dict) or not {"progress", "result", "error"} & set(said):
                        noise = line.decode(errors="replace").strip() or noise    # e.g. the last line of a crash
                        continue
                    if "progress" in said:
                        job["progress"] = said["progress"]
                    else:
                        result, error = said.get("result"), said.get("error")
                        break
            finally:
                timer.cancel()
                proc.kill()
        if result is None and error is None:
            place = job["progress"].get("place")
            error = (f"stopped at the time limit: no answer after {limit:.0f} s" + (f", last in {place}" if place else "")
                     if late.is_set() else
                     f"the measuring process ended with no result (code {proc.returncode})" + (f": {noise}" if noise else ""))
        if error is None and (ctx.home / "stellaxis.db").exists():
            run = {"started_at": job["started_at"], "finished_at": now_iso(), "finished_ts": time.time(),
                   "seconds": time.monotonic() - t0, "disk": disk}
            with closing(ctx.db()) as db, db:
                records.save(db, run, result)
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
    with _LOCK:
        job["error"] = error


def measure_reader(ctx) -> None:
    if not ctx.service:     # only the service measures: a process that makes one pass could not finish a walk
        return
    s = settings(ctx.cfg, ctx.home, ctx.claude_dir)
    if not s["places"]:
        return
    with _LOCK:
        job = _JOBS.setdefault(str(ctx.home), {"thread": None, "pid": None, "started_at": None, "progress": {},
                                               "last_try": None, "error": None})
        if job["thread"] is not None and job["thread"].is_alive():
            return
        if job["last_try"] is None:
            with closing(ctx.db()) as db:
                job["last_try"] = records.last_finished_ts(db)
        if job["last_try"] is not None and time.time() - job["last_try"] < s["every"]:
            return
        job["last_try"], job["started_at"], job["progress"], job["pid"] = time.time(), now_iso(), {}, None
        t = threading.Thread(target=_watch, args=(ctx, s, job), name="memory-measure", daemon=True)
        try:
            t.start()
            job["thread"] = t
        except Exception as e:      # shown on the route; tried again after `every`
            job["error"] = f"{type(e).__name__}: {e}"


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
           "idle": settings(ctx.cfg, ctx.home, ctx.claude_dir)["idle"], "error": error}
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
    send_json(handler, status, obj)
    return True


COMPONENT = Component(name="memory_management", readers=[measure_reader], records=records.RECORDS, handle=handle)
