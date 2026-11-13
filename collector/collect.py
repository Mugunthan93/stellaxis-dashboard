#!/usr/bin/env python3
"""Stellaxis collector: inventory + usage of Claude Code skills, agents, plugins and MCP servers.

Stdlib only. Reads ~/.claude, ~/.claude.json, every folder with Claude sessions or .claude files,
and writes ~/.stellaxis/snapshot.json (+ history.jsonl). Transcripts are parsed incrementally.

  python3 collect.py run            # one pass, print summary
  python3 collect.py serve          # loop every `interval` + dashboard on http://127.0.0.1:7777
  python3 collect.py summary        # print summary of the last snapshot
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.server
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from core import server as core_server
from core import util as core_util
from core.app import Core
from core.util import parse_duration, read_json, write_json, short_path
from unplaced import jarvis

VERSION = "0.2.2"
HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude")).expanduser()
STX_HOME = Path(os.environ.get("STELLAXIS_HOME", HOME / ".stellaxis")).expanduser()
REPO_DIR = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG = {
    "interval": "1m",            # collection interval: "30s", "1m", "15m", "1h"
    "port": 7777,
    "scan_roots": ["~"],          # walked for folders containing .claude/ or CLAUDE.md
    "scan_depth": 6,
    "rescan_every": "1h",         # filesystem walk is cached this long
    "exclude_dirs": ["node_modules", ".git", "Library", ".Trash", ".cache", ".npm", ".pnpm-store",
                     "venv", ".venv", "__pycache__", "dist", "build", "Pods", "DerivedData",
                     ".gradle", ".m2", "go", ".cargo", ".rustup", "Applications", "Movies",
                     "Music", "Pictures", ".stellaxis", ".docker", ".local", ".vscode", ".cursor"],
    "probe_mcp": True,            # spawn stdio MCP servers once a day to measure tool-schema size
    "probe_every": "24h",
    "probe_timeout": 20,
    "max_sessions": 400,          # sessions kept in snapshot (newest first)
    "series_days": 30,
    "projects": [],               # [{id, name, repos: [path], tests, routines}]: repos grouped into a project
    "claude_bin": "~/.local/bin/claude",
    "project_notes": [],          # your own notes: [{name, summary, path, facts: {k: v}, links: [{label, url}], open: [...]}]
    # USD per million tokens. cache_write_5m = 1.25x input, cache_write_1h = 2x input.
    # Longest matching prefix of the model id wins. Notional on a Max plan.
    "pricing": {
        "claude-fable-5-1": {"in": 10.0, "out": 50.0, "cache_read": 0.25},
        "claude-fable-5": {"in": 10.0, "out": 50.0, "cache_read": 1.0},
        "claude-mythos": {"in": 10.0, "out": 50.0, "cache_read": 0.25},
        "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20},
        "claude-opus-5": {"in": 5.0, "out": 25.0, "cache_read": 0.50},
        "claude-opus-4": {"in": 5.0, "out": 25.0, "cache_read": 0.50},
        "claude-sonnet-5": {"in": 2.0, "out": 10.0, "cache_read": 0.20},
        "claude-sonnet-4": {"in": 3.0, "out": 15.0, "cache_read": 0.30},
        "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01},
        "claude-haiku-4": {"in": 1.0, "out": 5.0, "cache_read": 0.10},
    },
}


# ---------------------------------------------------------------- utilities

def now_utc() -> dt.datetime:
    return core_util.now()


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


_status_at = 0.0


def status(phase: str, force: bool = True, **detail):
    """Write ~/.stellaxis/status.json so a stalled run shows where it is stuck (e.g. a macOS
    folder-permission prompt blocking a read in ~/Documents)."""
    global _status_at
    if not force and time.time() - _status_at < 1:
        return
    _status_at = time.time()
    try:
        write_json(STX_HOME / "status.json", {"phase": phase, "at": iso(now_utc()), "pid": os.getpid(), **detail})
    except OSError:
        pass


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = read_json(STX_HOME / "config.json", {}) or {}
    for k, v in user.items():
        if k == "pricing" and isinstance(v, dict):
            cfg["pricing"].update(v)
        else:
            cfg[k] = v
    return cfg


def project_notes(cfg: dict) -> list:
    """The user's hand-written project cards from config.json, trimmed to plain strings: "project_notes" when it has
    entries, else the entries of "projects" with no id (where the cards were kept before)."""
    def txt(v, n=500):
        return str(v)[:n] if v is not None else ""
    cards = cfg.get("project_notes") or [p for p in cfg.get("projects") or [] if isinstance(p, dict) and not p.get("id")]
    out = []
    for p in cards:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        facts = p.get("facts") if isinstance(p.get("facts"), dict) else {}
        links = [x for x in (p.get("links") or []) if isinstance(x, dict) and x.get("url")]
        out.append({
            "name": txt(p["name"], 120), "summary": txt(p.get("summary")), "path": txt(p.get("path"), 300),
            "facts": {txt(k, 60): txt(v) for k, v in facts.items()},
            "links": [{"label": txt(x.get("label") or x["url"], 80), "url": txt(x["url"], 500)} for x in links],
            "open": [txt(x) for x in (p.get("open") or [])],
        })
    return out


def reels_summary() -> dict | None:
    """Reel Brain's summary.json (written by reels/build_dashboard.py); None when it isn't set up on this Mac."""
    try:
        data = json.loads((REELS_HOME / "summary.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_lock = threading.Lock()


_core: Core | None = None
_core_lock = threading.Lock()


def the_core() -> Core:
    global _core
    if _core is None:
        with _core_lock:
            if _core is None:
                # ~/.claude.json, worked out here and not at import: tests point CLAUDE_DIR and the env at a fake home
                _core = Core(STX_HOME, CLAUDE_DIR, Path(__file__).resolve().parent / "components",
                             claude_json=(CLAUDE_DIR / ".claude.json") if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json")
                _core.on_status = status
    return _core


def pass_error(core: Core, res: dict) -> str | None:
    """Every component's error, as one string: the components that did not load, then the readers that failed."""
    errs = [f"{c.name}: {c.error}" for c in core.components if c.error]
    errs += [f"{name}: {e}" for name, comp in res["components"].items() for e in comp["errors"]]
    return "; ".join(errs) or None


last_error: str | None = None   # the errors of the last collection (pass_error), for the ways in that print them


def _stamp(f: Path) -> int | None:
    try:
        return f.stat().st_mtime_ns
    except OSError:
        return None


def collect(cfg=None, probe=False) -> dict:
    """One pass of the core as a collection: the snapshot this pass wrote, else {}. Its errors are kept in `last_error`."""
    global last_error
    with _lock:
        cfg = cfg or load_config()
        STX_HOME.mkdir(parents=True, exist_ok=True)
        f = STX_HOME / "snapshot.json"
        before = _stamp(f)
        was = read_json(STX_HOME / "status.json", {})
        was = was if isinstance(was, dict) else {}
        status("reading transcripts")
        core = the_core()
        res = core.run_pass(cfg, {"collector": {"version": VERSION, "probe": probe, "reels": reels_summary(),
                                                "project_notes": project_notes(cfg)}})
        after = _stamp(f)
        snap = (read_json(f, {}) or {}) if after is not None and after != before else {}
        last_error = pass_error(core, res)
        last = ((snap.get("generated_at"), (snap.get("meta") or {}).get("elapsed_ms")) if snap   # no snapshot: keep the last one's
                else (was.get("last_snapshot"), was.get("elapsed_ms")))
        status("idle", last_snapshot=last[0], elapsed_ms=last[1], index_error=last_error, core_error=core.error)
        return snap


# ---------------------------------------------------------------- serve

PAGES = {"/": "home.html", "/overview": "index.html", "/index.html": "index.html", "/project": "project.html", "/hierarchy": "hierarchy.html", "/hierarchy.html": "hierarchy.html",
         "/settings": "settings.html", "/settings.html": "settings.html", "/skills": "skills.html"}
# Reel Brain's dashboard (built by reels/build_dashboard.py): /reels/ shows it inside the shell, /reels/raw is the page
REELS_HOME = Path(os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels")))
# Skill Library's catalog (built by skills_library/library.py), read by /skills through /api/skills
SKILLS_HOME = Path(os.path.expanduser(os.environ.get("SKILLS_HOME", "~/Knowledge/skills")))
# A second set of notes, built by the same script with REELS_HOME pointing here: social media and digital marketing
MARKETING_HOME = Path(os.path.expanduser(os.environ.get("MARKETING_REELS_HOME", "~/Knowledge/marketing-reels")))
REEL_SETS = {"reels": "Reels", "marketing": "Marketing"}  # first part of the address -> name in the tab


def reel_home(name: str) -> Path:
    return REELS_HOME if name == "reels" else MARKETING_HOME


def reels_frame(name: str) -> str:
    return (f'<title>{REEL_SETS[name]} · Stellaxis</title><iframe class="sx-frame" src="/{name}/raw" '
            f'title="{REEL_SETS[name]} reel notes"></iframe>')


def reels_scripts(body: bytes) -> str:
    """script-src for the Reel Brain page: only its own script, the last one in the file (build_dashboard.py writes one,
    after all note text). Hashing every script would approve one smuggled in through a note."""
    start, end = body.rfind(b"<script>"), body.rfind(b"</script>")
    if start < 0 or end < start:
        return "script-src 'none'; "
    return f"script-src 'sha256-{base64.b64encode(hashlib.sha256(body[start + 8:end]).digest()).decode()}'; "


FULL_SCREEN = {"/"}  # Home is the voice-only Jarvis portal: no sidebar, nothing to click


def shell_page(page: str, shell: bool = True) -> bytes:
    """A page body inside the localhost app shell (sidebar), with the skeleton the artifact host would add."""
    if shell:
        page = (REPO_DIR / "dashboard" / "shell.html").read_text(encoding="utf-8").replace("<!--PAGE-->", page, 1)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1,viewport-fit=cover"><style>body{margin:0}'
            '[hidden]{display:none!important}</style></head><body>' + page + "</body></html>").encode()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(REPO_DIR / "dashboard"), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        # /reels/raw shows text copied from reels. It shares this origin, and connect-src doesn't stop a script calling
        # the API through its parent frame, so only the page's own script may run: script-src blocks injected scripts
        # and event handlers. Not a sandbox, the app's browser pane refuses images inside sandboxed frames.
        raw = (getattr(self, "reels_csp", "script-src 'none'; ") + "connect-src 'none'; form-action 'none'; base-uri 'none'; "
               if self.path.split("?")[0] in ("/reels/raw", "/marketing/raw") else "")
        self.send_header("Content-Security-Policy", raw + "frame-ancestors 'self'")
        super().end_headers()

    def do_GET(self):
        if not core_server.host_ok(self):  # DNS rebinding: a hostile domain resolving to 127.0.0.1
            return self.send_error(403)
        if core_server.handle(the_core(), self, "GET", load_config()):
            return
        if jarvis.handle(self, "GET", load_config(), the_core()):
            return
        if self.path.split("?")[0] in ("/snapshot.json", "/api/snapshot", "/api/skills"):
            f = SKILLS_HOME / "catalog.json" if self.path.split("?")[0] == "/api/skills" else STX_HOME / "snapshot.json"
            body = f.read_bytes() if f.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.path.split("?")[0]
        name = path.split("/")[1]
        if name in REEL_SETS:
            if path == "/" + name:
                self.send_response(301)
                self.send_header("Location", f"/{name}/")
                self.end_headers()
                return
            if path == f"/{name}/":
                return self.send_body(shell_page(reels_frame(name)), "text/html; charset=utf-8")
            if path == f"/{name}/raw" or path.startswith(f"/{name}/images/"):
                return self.send_reels(name, path)
        page_file = PAGES.get(path)
        if page_file:
            # pages are authored as artifact bodies; give them the same skeleton the artifact host adds
            page = (REPO_DIR / "dashboard" / page_file).read_text(encoding="utf-8")
            return self.send_body(shell_page(page, path not in FULL_SCREEN), "text/html; charset=utf-8")
        if self.path.split("?")[0] == "/api/status":
            body = (STX_HOME / "status.json").read_bytes() if (STX_HOME / "status.json").exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/api/refresh":
            if not _lock.locked():
                threading.Thread(target=collect, daemon=True).start()
            self.send_response(202)
            self.end_headers()
            return
        super().do_GET()

    def send_body(self, body: bytes, ctype: str, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def send_reels(self, which, path):
        home = reel_home(which)
        if path == f"/{which}/raw":
            f, ctype = home / "dashboard.html", "text/html; charset=utf-8"
        else:
            name = urllib.parse.unquote(path[len(f"/{which}/images/"):])
            f = home / "images" / name
            if "/" in name or name.startswith(".") or f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                return self.send_error(404)
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not f.is_file():
            return self.send_error(404, "Reel Brain dashboard not built yet (run reels/build_dashboard.py)")
        body = f.read_bytes()
        if path == f"/{which}/raw":
            self.reels_csp = reels_scripts(body)
        self.send_body(body, ctype, "no-store" if ctype.startswith("text/html") else "max-age=3600")

    def do_POST(self):
        if not core_server.host_ok(self):
            return self.send_error(403)
        if core_server.handle(the_core(), self, "POST", load_config()):
            return
        if jarvis.handle(self, "POST", load_config(), the_core()):
            return
        self.send_error(404)


def serve():
    cfg = load_config()
    the_core().service = True  # the components are imported here, on the main thread, before any thread starts
    last_probe = 0.0

    def loop():
        nonlocal last_probe
        while True:
            c = load_config()  # re-read so interval edits apply without restart
            probe = c.get("probe_mcp") and time.time() - last_probe > parse_duration(c["probe_every"], 86400)
            try:
                snap = collect(c, probe=bool(probe))
                if probe and snap:   # a pass that wrote no snapshot probes again next time
                    last_probe = time.time()
                if snap:
                    print(f"[{snap['generated_at']}] {snap['totals']['sessions']} sessions, "
                          f"{len(snap['items'])} items, {snap['meta']['elapsed_ms']} ms", flush=True)
                else:
                    print("no snapshot was written", flush=True)
                if last_error:
                    print(f"collect failed: {last_error}", file=sys.stderr, flush=True)
            except Exception as e:  # keep the daemon alive
                print(f"collect failed: {e!r}", file=sys.stderr, flush=True)
            time.sleep(max(10, parse_duration(c["interval"], 60)))

    threading.Thread(target=loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(cfg["port"])), Handler)
    print(f"Stellaxis dashboard: http://127.0.0.1:{cfg['port']}  (interval {cfg['interval']})", flush=True)
    srv.serve_forever()


def print_summary(snap: dict):
    t = snap["totals"]
    print(f"Stellaxis {snap['generated_at']} host={snap['host']}  sessions={t['sessions']} projects={t['projects']}")
    print(f"  measured baseline (first-turn context): latest={t['baseline_latest']} median={t['baseline_median']} tokens")
    print(f"  global always-on estimate: ~{t['global_always_tokens_est']} tokens")
    if snap.get("latest_context"):
        print("  latest session context breakdown (~tokens):",
              ", ".join(f"{k}={v}" for k, v in snap["latest_context"].items()))
    for k, v in sorted(t["by_kind"].items(), key=lambda kv: -kv[1]["tokens_always"]):
        print(f"  {k:10s} count={v['count']:4d}  always~{v['tokens_always']:6d} tok  never-used={v['unused']}")
    print(f"  today: {t['today']['tokens']:,} tokens ${t['today']['cost']}  |  30d: {t['month']['tokens']:,} tokens ${t['month']['cost']}")
    print("  top always-on items:")
    for it in snap["items"][:15]:
        print(f"    {it['tokens_always']:6d}  {it['id']}  calls={it['usage']['calls']}  [{it.get('source')}]")
    if snap["duplicates"]:
        print(f"  duplicates: {len(snap['duplicates'])} (e.g. {snap['duplicates'][0]['items']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "serve", "summary"])
    ap.add_argument("--probe", action="store_true", help="probe stdio MCP servers for schema size now")
    ap.add_argument("--json", action="store_true", help="print the snapshot JSON")
    a = ap.parse_args()
    if a.cmd == "serve":
        serve()
        return
    snap = collect(probe=a.probe) if a.cmd == "run" else read_json(STX_HOME / "snapshot.json", {})
    if a.cmd == "run" and not snap and last_error:
        print(f"collect failed: {last_error}", file=sys.stderr, flush=True)
        sys.exit(1)
    if a.json:
        json.dump(snap, sys.stdout, indent=1, default=list)
    elif snap:
        print_summary(snap)
        print(f"\nsnapshot: {short_path(STX_HOME / 'snapshot.json')}")
    if a.cmd == "run" and last_error:   # a snapshot was written, but a component failed
        print(f"collect: {last_error}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
