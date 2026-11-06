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
import uuid
from pathlib import Path

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CAP = 2000


def summary_of(tool: str, inp: dict) -> str:
    cmd = inp.get("command")
    if tool == "Bash" and isinstance(cmd, str):
        return cmd  # never shortened: the owner must see all of it
    s = json.dumps(inp, separators=(",", ":"), ensure_ascii=False)
    return s if len(s) <= CAP else s[:CAP] + f" ... [truncated, {len(s) - CAP} more characters]"


def decision(behavior: str) -> dict:
    d = {"behavior": behavior}
    if behavior == "deny":
        d["message"] = "Denied from the Stellaxis page."
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


def run() -> dict | None:
    req = json.loads(sys.stdin.read())
    if not isinstance(req, dict) or req.get("hook_event_name") != "PermissionRequest":
        return None
    sid, tool, inp = req.get("session_id"), req.get("tool_name"), req.get("tool_input")
    if not (isinstance(sid, str) and UUID_RE.fullmatch(sid) and isinstance(tool, str) and isinstance(inp, dict)):
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
    body = json.dumps({"id": rid, "session_id": sid, "tool": tool, "summary": summary_of(tool, inp),
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


def main() -> None:
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
