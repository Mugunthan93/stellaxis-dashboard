#!/usr/bin/env python3
"""Map each user-level permission rule to the projects and sessions whose tool calls it covers.

Reads ~/.claude/settings.json and the transcripts under ~/.claude/projects, and prints one JSON
document (the Settings Remote artifact's `config/permUsage`). Usage: perm_usage.py [days] > out.json
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOME = str(Path.home())
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def short(path: str) -> str:
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


_roots: dict = {}


def root_of(cwd: str) -> str:
    """The folder whose .claude/settings.local.json a session in `cwd` uses: the main checkout's git root."""
    cwd = cwd.split("/.claude/worktrees/")[0].split("/.worktrees/")[0]
    if cwd not in _roots:
        p = Path(cwd)
        root = next((a for a in [p, *p.parents] if (a / ".git").exists() and str(a) != HOME), None)
        _roots[cwd] = str(root) if root else cwd
    return _roots[cwd]


def project_of(cwd: str) -> str:
    return short(root_of(cwd)) if cwd else "(unknown folder)"


def bash_match(rule_body: str, command: str) -> bool:
    parts = [p.strip() for p in SPLIT.split(command or "") if p.strip()]
    if rule_body.endswith(":*"):
        pre = rule_body[:-2]
        return any(p == pre or p.startswith(pre + " ") for p in parts)
    if rule_body.endswith(" *") or rule_body.endswith("*"):
        pre = rule_body.rstrip("*").rstrip()
        return any(p.startswith(pre) for p in parts)
    return any(p == rule_body for p in parts)


def matches(rule: str, name: str, inp: dict) -> bool:
    m = re.fullmatch(r"(\w+)\((.*)\)", rule, re.S)
    if m:
        tool, body = m.groups()
        if tool != name:
            return False
        if tool == "Bash":
            return bash_match(body, inp.get("command", ""))
        if tool == "WebFetch" and body.startswith("domain:"):
            return body[7:] in (inp.get("url") or "")
        return True  # path rules etc.: counted as the tool being used
    if rule.endswith("*"):
        return name.startswith(rule[:-1])
    return name == rule


def lists(path: Path) -> dict:
    try:
        perms = json.loads(path.read_text()).get("permissions") or {}
    except Exception:
        perms = {}
    return {k: perms.get(k) or [] for k in ("allow", "ask", "deny")}


def projects(seen_cwds: set) -> list:
    """Main project folders Claude knows or ran in (worktrees share their main checkout's local file)."""
    cj = json.loads((Path.home() / ".claude.json").read_text()) if (Path.home() / ".claude.json").exists() else {}
    roots = set((cj.get("projects") or {}).keys()) | {r for r in map(root_of, seen_cwds) if (Path(r) / ".git").exists() or (Path(r) / ".claude").is_dir()}
    out = []
    for proj in sorted(roots):
        if proj in (HOME, "/") or not proj.startswith(HOME + "/"):
            continue
        if "/.claude/worktrees/" in proj or "/Library/Application Support/" in proj or not Path(proj).is_dir():
            continue
        out.append({"name": short(proj), "abs": proj,
                    "local": lists(Path(proj) / ".claude" / "settings.local.json"),
                    "shared": lists(Path(proj) / ".claude" / "settings.json")})
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    perms = (json.loads((CLAUDE_DIR / "settings.json").read_text()).get("permissions") or {})
    rules = {k: perms.get(k) or [] for k in ("allow", "ask", "deny")}
    every = [(k, r) for k, rs in rules.items() for r in rs]
    stats = {r: {"list": k, "calls": 0, "sessions": set(), "projects": {}, "lastUsed": None} for k, r in every}
    sessions_seen, calls_seen, cwds = set(), 0, set()
    for f in (CLAUDE_DIR / "projects").glob("*/*.jsonl"):
        if dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc) < since:
            continue
        with f.open(errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = d.get("timestamp") or ""
                if ts and ts < since.isoformat():
                    continue
                content = (d.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                proj, sid = project_of(d.get("cwd") or ""), d.get("sessionId")
                if d.get("cwd"):
                    cwds.add(d["cwd"])
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    calls_seen += 1
                    sessions_seen.add(sid)
                    for _, r in every:
                        if matches(r, b.get("name") or "", b.get("input") or {}):
                            s = stats[r]
                            s["calls"] += 1
                            s["sessions"].add(sid)
                            s["projects"][proj] = s["projects"].get(proj, 0) + 1
                            if ts > (s["lastUsed"] or ""):
                                s["lastUsed"] = ts
    out = {}
    for r, s in stats.items():
        top = sorted(s["projects"].items(), key=lambda kv: -kv[1])
        out[r] = {"list": s["list"], "calls": s["calls"], "sessions": len(s["sessions"]),
                  "projectCount": len(top), "projects": [{"name": n, "calls": c} for n, c in top[:8]],
                  "lastUsed": s["lastUsed"]}
    print(json.dumps({"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "windowDays": days,
                      "toolCalls": calls_seen, "sessions": len(sessions_seen), "rules": out,
                      "projects": projects(cwds)}))


if __name__ == "__main__":
    main()
