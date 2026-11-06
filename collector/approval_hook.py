"""PermissionRequest hook: ask the Stellaxis page to allow or deny a tool call (run as `python3 -I`, stdlib only).

Does nothing (no output, exit 0, so Claude shows its normal prompt) unless "Approve from page" is on for this
session, and on any error, bad input, odd answer or timeout. It prints a decision only when the answer file
for its own request says exactly `allow` or `deny`.
"""
import json
import os
import re
import stat
import sys
import time
import unicodedata
import uuid
from pathlib import Path

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CAP = 2000
SKIP_TOOLS = ("AskUserQuestion", "ExitPlanMode")  # their prompts are questions or plans, not a command to judge here


def summary_of(tool, inp):
    """(summary, extra) the owner will see, or None when it can't be shown whole and plainly."""
    cmd = inp.get("command")
    if tool == "Bash":
        if not isinstance(cmd, str):
            return None
        # every other key can change the run (e.g. dangerouslyDisableSandbox), so it is shown too
        return cmd, {k: v for k, v in inp.items() if k not in ("command", "description", "timeout")}
    s = json.dumps(inp, separators=(",", ":"), ensure_ascii=False)
    return (s, {}) if len(s) <= CAP else None  # too long to show whole: the normal prompt handles it


def hidden(ch):
    """Characters that could make shown text differ from what runs: controls (but not newline or tab),
    Unicode format characters (zero-width, bidi, soft hyphen, word joiner, tags, BOM) and line/paragraph separators."""
    return (unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\n\t") or ch in "\u2028\u2029"


def plain(*vals):
    """True when no string or key anywhere in the values (walked raw, before any JSON encoding) is hidden."""
    stack = list(vals)
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            if any(hidden(ch) for ch in v):
                return False
        elif isinstance(v, dict):
            stack.extend(v.keys())
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return True


def decision(behavior):
    d = {"behavior": behavior}
    if behavior == "deny":
        d["message"] = "Denied from the Stellaxis page."
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


def run():
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict) or req.get("hook_event_name") != "PermissionRequest":
        return None
    sid, tool, inp = req.get("session_id"), req.get("tool_name"), req.get("tool_input")
    if not (isinstance(sid, str) and UUID_RE.fullmatch(sid) and isinstance(tool, str) and isinstance(inp, dict)):
        return None
    cwd = req.get("cwd") if isinstance(req.get("cwd"), str) else ""
    shown = None if tool in SKIP_TOOLS else summary_of(tool, inp)
    if shown is None or not plain(inp, cwd, tool):
        return None
    home = Path(os.environ.get("STELLAXIS_HOME", "~/.stellaxis")).expanduser()
    try:
        if not stat.S_ISREG(os.lstat(home / "approvals" / "on" / sid).st_mode):
            return None
    except OSError:
        return None
    wait = 120.0
    try:
        wait = min(wait, float(os.environ["STELLAXIS_APPROVAL_WAIT"]))  # tests shorten it; never longer
    except (KeyError, ValueError):
        pass
    d = home / "approvals"
    rid = uuid.uuid4().hex
    body = json.dumps({"id": rid, "session_id": sid, "tool": tool, "summary": shown[0], "extra": shown[1], "cwd": cwd,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    tmp, rq, ans = d / (rid + ".tmp"), d / (rid + ".req"), d / (rid + ".ans")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, rq)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                a = ans.read_text()
            except OSError:
                a = None
            if a in ("allow", "deny"):
                return decision(a)
            time.sleep(0.2)
        return None
    finally:
        for p in (tmp, rq, ans):
            try:
                p.unlink()
            except OSError:
                pass


def main():
    try:
        out = run()
        if out is not None:
            sys.stdout.write(json.dumps(out))
            sys.stdout.flush()
    except BaseException:
        pass  # any doubt: print nothing, so the normal prompt appears


if __name__ == "__main__":
    main()
    sys.exit(0)
