"""Jarvis: the briefing, ask, hear, say and hush routes. Runs today and is in no component yet."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import settings_api
import workspaces as W


def _jarvis(handler) -> bool:
    """GET /api/jarvis: the last briefing the Jarvis agent wrote, {"text", "written"}; both null when there is none."""
    try:
        if not settings_api.host_ok(handler):
            raise W.ApiError(403, "only same-origin localhost requests are answered")
        p = W._home() / "jarvis" / "briefing.md"
        try:
            with p.open("rb") as f:
                text = f.read(200_000).decode("utf-8", "replace")
            written = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
        except FileNotFoundError:
            text = written = None
        settings_api._send(handler, 200, {"text": text, "written": written})
    except W.ApiError as e:
        settings_api._send(handler, e.status, {"error": e.msg, **e.extra})
    except OSError as e:
        settings_api._send(handler, 500, {"error": f"{type(e).__name__}: {e}"})
    return True


# --- asking Jarvis: speech > ffmpeg (16 kHz wav) > whisper-cli > a headless Claude that may only read ---
MAX_AUDIO = 10 * 1024 * 1024
_ASK_LOCK = threading.Lock()  # one question at a time; the Mac is often busy
JARVIS_ASK = ("You are Jarvis, the owner's assistant for every project on this Mac. The user message is what the "
              "owner just asked, typed or heard by speech recognition, so allow for mishearing. Answer from the "
              "briefing and status below; read files (session transcripts under ~/.claude/projects, repos under "
              "~/code) only when those don't cover it. Your answer is read aloud: one to four short, plain "
              "sentences, no Markdown, no lists. Say so when you don't know. You can only read, so never say you "
              "did or changed anything.")


def _tool(cfg: dict, key: str, default: str) -> str:
    return os.path.expanduser(cfg.get(key) or default)


def transcribe(audio: bytes, cfg: dict) -> str:
    """Text Whisper heard in a recording (any format ffmpeg reads); "" when it heard nothing."""
    with tempfile.TemporaryDirectory() as d:
        src, wav = Path(d) / "in", Path(d) / "in.wav"
        src.write_bytes(audio)
        _dbg = W._home() / "jarvis" / "heard"; _dbg.mkdir(parents=True, exist_ok=True)  # TEMP: diagnosing blank text
        (_dbg / f"{int(time.time())}.wav").write_bytes(audio)
        try:
            subprocess.run([_tool(cfg, "ffmpeg_bin", "/opt/homebrew/bin/ffmpeg"), "-nostdin", "-loglevel", "error",
                            "-i", str(src), "-ar", "16000", "-ac", "1", str(wav)],
                           capture_output=True, timeout=30, check=True)
            r = subprocess.run([_tool(cfg, "whisper_bin", "/opt/homebrew/bin/whisper-cli"),
                                "-m", _tool(cfg, "whisper_model", "~/.stellaxis/models/ggml-base.en.bin"),
                                "-f", str(wav), "-nt", "-np", "-l", "en", "--prompt",  # names it would mishear
                                ", ".join(["Jarvis", "Stellaxis", *(str(w.get("name")) for w in cfg.get("workspaces") or [] if isinstance(w, dict) and w.get("name"))])],
                               capture_output=True, text=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            raise W.ApiError(502, f"couldn't turn the recording into text: {e}")
    text = " ".join(r.stdout.split())
    return "" if re.fullmatch(r"(\[[^\]]*\]|\([^)]*\)|\s)*", text) else text  # [BLANK_AUDIO], (silence)


def _ask_context(cfg: dict) -> str:
    """The latest briefing, the last 7 days of sessions with live status, and open approvals, for Jarvis."""
    out = []
    try:
        out.append("# Latest briefing\n" + (W._home() / "jarvis" / "briefing.md").read_text()[:20000])
    except OSError:
        out.append("# Latest briefing\nNone written yet.")
    status = {a.get("sessionId"): a.get("status") for a in W.live(W._bin(cfg)) or [] if isinstance(a, dict)}
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = []
    for w in W._index().get("workspaces", []):
        for r in w.get("repos", []):
            for b in r.get("branches", []):
                for s in b.get("sessions", []):
                    if str(s.get("last_ts") or "") >= since and not s.get("archived"):
                        rows.append(f"{w.get('name')} | {r.get('path')} | {b.get('name')} | {s.get('title') or s.get('id')} "
                                    f"| {status.get(s.get('id'), 'closed')} | last {s.get('last_ts')} | ${s.get('cost') or 0:.2f} | id {s.get('id')}")
    out.append("# Sessions active in the last 7 days\nworkspace | repo | branch | title | status | last active | cost | id\n"
               + "\n".join(rows[:400]))
    appr = W.pending()
    out.append("# Waiting for the owner's approval\n" + ("\n".join(f"{a['tool']}: {a['summary'][:300]}" for a in appr) or "None."))
    return "\n\n".join(out)[:80000]


def ask(question: str, cfg: dict) -> str:
    """Jarvis's spoken answer to one question, from a headless Claude limited to reading files."""
    args = [W._bin(cfg), "-p", "--model", "claude-haiku-5-5", "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
            "--no-session-persistence", "--strict-mcp-config", "--append-system-prompt", JARVIS_ASK + "\n\n" + _ask_context(cfg)]
    try:
        r = subprocess.run(args, input=question, capture_output=True, text=True, timeout=150, cwd=str(W._home()),
                           env={**os.environ, "ENABLE_TOOL_SEARCH": "false"})
    except (OSError, subprocess.SubprocessError) as e:
        raise W.ApiError(502, f"Jarvis couldn't answer: {e}")
    if r.returncode or not r.stdout.strip():
        raise W.ApiError(502, "Jarvis couldn't answer: " + ((r.stderr or r.stdout).strip()[:200] or "no reply"))
    return r.stdout.strip()


def _audio(handler) -> bytes:
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if not 0 < n <= MAX_AUDIO:
        raise W.ApiError(413 if n > MAX_AUDIO else 400, "send a recording of up to 10 MB")
    return handler.rfile.read(n)


_HEAR_LOCK = threading.Lock()
_SAY_LOCK = threading.Lock()
_saying: list = []  # the `say` process now speaking, so hush can end it


def say(text: str, cfg: dict) -> None:
    """Speak text through the Mac's own voice (no browser needed) and return when it is done or hushed."""
    if not _SAY_LOCK.acquire(blocking=False):
        raise W.ApiError(429, "Jarvis is already speaking.")
    try:
        p = subprocess.Popen([_tool(cfg, "say_bin", "/usr/bin/say"), "-f", "-"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        _saying[:] = [p]
        try:
            p.communicate(text, timeout=180)
        except subprocess.TimeoutExpired:
            p.kill()
    except OSError as e:
        raise W.ApiError(502, f"couldn't speak: {e}")
    finally:
        _saying.clear()
        _SAY_LOCK.release()


def _jarvis_voice(handler, path: str, cfg: dict) -> bool:
    """POST /api/jarvis/hear (a recording: {"heard"}), /api/jarvis/say ({"text"}: returns when spoken) and
    /api/jarvis/hush (stops speaking)."""
    try:
        if not settings_api.host_ok(handler):
            raise W.ApiError(403, "only same-origin localhost requests are answered")
        if path == "/api/jarvis/hear":
            if not handler.headers.get("Content-Type", "").startswith("audio/"):
                raise W.ApiError(415, "send audio/*")
            audio = _audio(handler)
            if not _HEAR_LOCK.acquire(blocking=False):
                raise W.ApiError(429, "still hearing the last recording")
            try:
                out = {"heard": transcribe(audio, cfg)}
                print(f"jarvis heard {len(audio)} bytes: {out['heard']!r}", flush=True)  # in serve.log
            finally:
                _HEAR_LOCK.release()
        elif path == "/api/jarvis/say":
            text = W._prompt({"prompt": settings_api.read_body(handler).get("text")})
            say(text, cfg)
            out = {}
        else:
            for p in list(_saying):
                p.kill()
            out = {}
        settings_api._send(handler, 200, out)
    except W.ApiError as e:
        settings_api._send(handler, e.status, {"error": e.msg, **e.extra})
    except OSError as e:
        settings_api._send(handler, 500, {"error": f"{type(e).__name__}: {e}"})
    return True


def _jarvis_ask(handler, cfg: dict) -> bool:
    """POST /api/jarvis/ask with a recording (Content-Type audio/*) or JSON {"question"}: {"heard", "answer"}."""
    try:
        if not settings_api.host_ok(handler):
            raise W.ApiError(403, "only same-origin localhost requests are answered")
        if handler.headers.get("Content-Type", "").startswith("audio/"):
            audio = _audio(handler)
            question = None
        else:
            q = settings_api.read_body(handler).get("question")
            question = W._prompt({"prompt": q})
        if not _ASK_LOCK.acquire(blocking=False):
            raise W.ApiError(429, "Jarvis is still answering the last question.")
        try:
            if question is None:
                question = transcribe(audio, cfg)
                if not question:
                    raise W.ApiError(422, "I didn't catch that. Hold the button while you speak.")
            out = {"heard": question, "answer": ask(question, cfg)}
        finally:
            _ASK_LOCK.release()
        settings_api._send(handler, 200, out)
    except W.ApiError as e:
        settings_api._send(handler, e.status, {"error": e.msg, **e.extra})
    except OSError as e:
        settings_api._send(handler, 500, {"error": f"{type(e).__name__}: {e}"})
    return True


def handle(handler, method: str, cfg: dict) -> bool:
    """Serve GET /api/jarvis and POST /api/jarvis/ask, /hear, /say and /hush. False when the path is not ours."""
    path = handler.path.split("?")[0]
    if path == "/api/jarvis" and method == "GET":
        return _jarvis(handler)
    if path == "/api/jarvis/ask" and method == "POST":
        return _jarvis_ask(handler, cfg)
    if path in ("/api/jarvis/hear", "/api/jarvis/say", "/api/jarvis/hush") and method == "POST":
        return _jarvis_voice(handler, path, cfg)
    return False
