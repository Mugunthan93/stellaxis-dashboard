"""The old-against-new proof: one fake home, two passes, the routes and the prune commands, all with a fixed clock.

It runs the code in --collector on a home it builds itself and writes every output, with the things that may
differ (the temp path, the host name, process ids, timings, stamps) replaced by fixed words.

  HOME=<dir> STELLAXIS_HOME=<dir>/.stellaxis python3 tests/same_output_harness.py --collector collector --out result.json

`tests/same_output_expected.json` is what the code before step 3 of the core architecture (commit dfb604b) gave.
`tests/test_same_output.py` runs this file on the present code and compares. To rebuild the expected file, run this
file with --collector pointing at a checkout of dfb604b: that code has no STELLAXIS_NOW, so its clock is patched here.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import http.client
import http.server
import io
import json
import os
import re
import subprocess
import sys
import threading
import zlib
from pathlib import Path

T1 = "2026-11-11T12:00:00Z"
T2 = "2026-11-11T13:05:00Z"
A = "aaaaaaaa-1111-4111-8111-111111111111"
F = "ffffffff-2222-4222-8222-222222222222"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_DATE": "2026-11-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T00:00:00Z"}
MCP_SH = ('read l; echo \'{"jsonrpc":"2.0","id":1,"result":{"instructions":"Use fake tools wisely."}}\'; read l; read l; '
          'echo \'{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"t0","description":"xxxxxxxxxxxxxxxxxxxx"},'
          '{"name":"t1","description":"yyyyyyyyyyyyyyyyyyyy"}]}}\'')


def w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def jl(rows) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def enc(p) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


def usage(i=0, w5=0, w1=0, r=0, o=0, split=True) -> dict:
    u = {"input_tokens": i, "cache_creation_input_tokens": w5 + w1, "cache_read_input_tokens": r, "output_tokens": o}
    if split:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}
    return u


def asst(base, mid, ts, usage_, blocks=(), model="claude-opus-5-5", side=False) -> dict:
    return {**base, "type": "assistant", "timestamp": ts, "isSidechain": side,
            "message": {"id": mid, "model": model, "content": list(blocks), "usage": usage_}}


def tool(id_, name, **inp) -> dict:
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def att(base, ts, **a) -> dict:
    return {**base, "type": "attachment", "timestamp": ts, "attachment": a}


def build(H: Path) -> None:
    """A Mac-like home: the Claude setup, six repos, eleven transcript files, the desktop app's records and the config."""
    C, code = H / ".claude", H / "code"
    mesell, pa, pb, pc = code / "mesell", code / "partsA", code / "partsB", code / "partsC"
    w(C / "CLAUDE.md", "# Global\nAlways be brief.\n@~/.claude/extra.md\n")
    w(C / "extra.md", "Extra imported rules " * 20)
    w(C / "rules" / "style.md", "Write short sentences.\n")
    w(C / "rules" / "py" / "tests.md", "---\npaths: tests/**\n---\nUse unittest.\n")
    w(C / "skills" / "foo" / "SKILL.md", "---\nname: foo\ndescription: Does foo things\n---\nbody\n")
    w(C / "skills" / "stale" / "SKILL.md", "---\nname: stale\ndescription: Never used skill\n---\nbody\n")
    w(C / "skills" / "quiet" / "SKILL.md", "---\nname: quiet\ndescription: >\n  Only by hand\ndisable-model-invocation: true\n---\nbody\n")
    w(C / "skills" / "synced" / "b1" / "acct" / "SKILL.md", "---\nname: acct\ndescription: Account skill\n---\nbody\n")
    w(C / "agents" / "planner.md", "---\nname: planner\ndescription: Plans work\ntools: Read, Grep\n---\nYou plan.\n")
    w(C / "commands" / "deploy.md", "---\ndescription: Deploy the app\n---\nDeploy.\n")
    w(C / "commands" / "git" / "sync.md", "Sync.\n")
    ap = C / "plugins" / "synced" / "b1" / "acctplug"
    w(ap / ".claude-plugin" / "plugin.json", '{"name": "acctplug", "version": "2.0.0", "description": "from the account"}')
    w(ap / "skills" / "share" / "SKILL.md", "---\nname: share\ndescription: Share it\n---\nShare.\n")
    plug = C / "plugins" / "cache" / "mkt" / "toolkit" / "1.0.0"
    w(plug / ".claude-plugin" / "plugin.json", '{"name": "toolkit", "version": "1.0.0", "description": "kit"}')
    w(plug / "skills" / "lint" / "SKILL.md", "---\nname: lint\ndescription: Lint code\n---\nLint.\n")
    w(plug / "agents" / "reviewer.md", "---\nname: reviewer\ndescription: Reviews code\nmodel: sonnet\n---\nReview.\n")
    w(plug / "commands" / "fix.md", "---\ndescription: Fix it\n---\nFix.\n")
    w(plug / ".mcp.json", '{"mcpServers": {"kitsrv": {"command": "true"}}}')
    w(plug / "hooks" / "hooks.json", '{"hooks": {"Stop": [], "PreToolUse": []}}')
    off = C / "plugins" / "cache" / "mkt" / "sleepy" / "0.1.0"
    w(off / ".claude-plugin" / "plugin.json", '{"name": "sleepy", "mcpServers": {"nap": {"url": "https://nap.invalid/secret-path/mcp"}}}')
    w(off / "skills" / "doze" / "SKILL.md", "---\nname: doze\ndescription: Doze\n---\nDoze.\n")
    local = C / "plugins" / "cache" / "mkt" / "onlyhere" / "1.0.0"
    w(local / ".claude-plugin" / "plugin.json", '{"name": "onlyhere"}')
    w(local / "skills" / "here" / "SKILL.md", "---\nname: here\ndescription: Here only\n---\nHere.\n")
    w(C / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "toolkit@mkt": [{"scope": "user", "installPath": str(plug), "version": "1.0.0"}],
        "sleepy@mkt": [{"scope": "user", "installPath": str(off)}],
        "onlyhere@mkt": [{"scope": "project", "installPath": str(local), "projectPath": str(mesell)}],
        "gone@mkt": [{"scope": "user", "installPath": str(C / "plugins" / "cache" / "mkt" / "gone")}]}}))
    w(C / "settings.json", json.dumps({"enabledPlugins": {"toolkit@mkt": True, "sleepy@mkt": False},
                                        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]},
                                        "model": "opus"}, indent=2))
    w(H / ".claude.json", json.dumps({
        "mcpServers": {"fake": {"type": "stdio", "command": "/bin/sh", "args": ["-c", MCP_SH]},
                       "unused-http": {"type": "http", "url": "https://example.invalid/secret/mcp"}},
        "projects": {str(mesell): {"mcpServers": {"localsrv": {"command": "true"}, "offsrv": {"command": "true"}},
                                   "disabledMcpServers": ["offsrv"]},
                     str(pb): {}}}))
    w(code / "CLAUDE.md", "# All code\n")
    w(mesell / "CLAUDE.md", "# mesell\nFleet of 19 agents.\n")
    w(mesell / "CLAUDE.local.md", "local notes\n")
    w(mesell / ".claude" / "agents" / "planner.md", "---\nname: planner\ndescription: Project planner\n---\nPlan.\n")
    w(mesell / ".claude" / "agents" / "fleet" / "scout.md", "---\nname: scout\ndescription: Scouts\n---\nScout.\n")
    w(mesell / ".claude" / "skills" / "ship" / "SKILL.md", "---\nname: ship\ndescription: Ship it\n---\nShip.\n")
    w(mesell / ".claude" / "commands" / "deploy.md", "---\ndescription: Deploy mesell\n---\nDeploy.\n")
    w(mesell / ".claude" / "rules" / "api.md", "---\npaths: api/**\n---\nREST only.\n")
    w(mesell / ".claude" / "settings.json", json.dumps({"disabledMcpjsonServers": ["projoff"], "enabledPlugins": {"onlyhere@mkt": True}}))
    w(mesell / ".mcp.json", '{"mcpServers": {"projsrv": {"command": "true"}, "projoff": {"url": "https://off.invalid/x"}}}')
    w(mesell / "api" / "CLAUDE.md", "API notes\n")
    (mesell / ".git").mkdir()
    wt = mesell / ".claude" / "worktrees" / "agent-1"
    w(wt / ".claude" / "agents" / "planner.md", "---\nname: planner\ndescription: copy\n---\nPlan.\n")
    w(wt / ".git", f"gitdir: {mesell}/.git/worktrees/agent-1\n")
    w(C / "projects" / enc(mesell) / "memory" / "MEMORY.md", "- remember this\n")
    w(pa / "README.md", "partsA\n")
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(pa), *args], check=True, env={**os.environ, **GIT_ENV}, capture_output=True)
    w(pb / "CLAUDE.md", "# partsB\n")
    pc.mkdir(parents=True)

    P = C / "projects"
    a = {"sessionId": A, "cwd": str(mesell), "version": "2.1.300", "entrypoint": "cli", "gitBranch": "main"}
    w(P / enc(mesell) / f"{A}.jsonl", jl([
        {**a, "type": "user", "timestamp": "2026-11-10T09:00:00Z", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "<command-name>/deploy</command-name> go"}},
        att(a, "2026-11-10T09:00:01Z", type="skill_listing", isInitial=True,
            content="- foo: Does foo things\n- toolkit:lint: Lint code\n- builtin-skill: Comes with Claude\n  second line\n"),
        att(a, "2026-11-10T09:00:01Z", type="agent_listing_delta", builtInTypes=["general-purpose"],
            addedLines=["- planner: Plans work (Tools: Read, Grep)", "- general-purpose: Any task (Tools: *)"]),
        att(a, "2026-11-10T09:00:01Z", type="mcp_instructions_delta", addedNames=["fake"], addedBlocks=["Use fake tools wisely."]),
        att(a, "2026-11-10T09:00:01Z", type="deferred_tools_delta", needsAuthMcpServers=["plane"],
            addedNames=["mcp__fake__t0", "mcp__claude_ai_Slack__send", "Monitor"]),
        att(a, "2026-11-10T09:00:01Z", type="prompt_snapshot", systemPrompt="You are Claude Code.",
            tools=[{"name": "Read", "description": "r" * 300}, {"name": "mcp__localsrv__search", "description": "s" * 100},
                   {"name": "kit", "server": "plugin:toolkit:kitsrv", "description": "k" * 50}]),
        asst(a, "m1", "2026-11-10T09:00:05Z", usage(10, w1=20000, o=100), [tool("t1", "Skill", skill="foo")]),
        asst(a, "m1", "2026-11-10T09:00:05Z", usage(10, w1=20000, o=120), [tool("t2", "Agent", subagent_type="planner", prompt="x")]),
        {**a, "type": "user", "timestamp": "2026-11-10T09:01:00Z", "toolUseResult": {"agentId": "ag1", "totalTokens": 4321},
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "done"}]}},
        asst(a, "m2", "2026-11-10T09:02:00Z", usage(5, w5=500, r=20000, o=50, split=False),
             [tool("t3", "mcp__localsrv__search"), tool("t4", "Skill", skill="toolkit:lint"),
              tool("t5", "SlashCommand", command="/git:sync now"), tool("t6", "mcp__plugin_toolkit_kitsrv__kit"),
              tool("t7", "Task"), tool("t8", "mcp__claude_ai_Slack__send")]),
    ]))
    w(P / enc(mesell) / A / "subagents" / "agent-ag1.jsonl", jl([
        asst(a, "s1", "2026-11-10T09:00:30Z", usage(5, w5=500, r=2000, o=50, split=False), model="claude-sonnet-5-5", side=True)]))
    f = {**a, "sessionId": F, "gitBranch": "feature/x"}
    w(P / enc(mesell) / f"{F}.jsonl", jl([
        asst(f, "m1", "2026-11-10T09:00:05Z", usage(10, w1=20000, o=120), [tool("t2", "Agent", subagent_type="planner")]),
        asst(f, "m2", "2026-11-10T09:02:00Z", usage(5, w5=500, r=20000, o=50, split=False), [tool("t3", "mcp__localsrv__search")]),
        {**f, "type": "user", "timestamp": "2026-11-10T10:00:00Z", "message": {"role": "user", "content": "carry on"}},
        asst(f, "m3", "2026-11-10T10:00:05Z", usage(7, w5=300, r=20500, o=70, split=False), [tool("t9", "Skill", skill="ship")]),
        asst(f, "syn", "2026-11-10T10:00:06Z", usage(1, o=1), model="<synthetic>"),
    ]))
    cold = {**a, "sessionId": "cold", "cwd": str(code / "elsewhere"), "gitBranch": None}
    w(P / enc(mesell) / "cold.jsonl", jl([
        asst(cold, "c1", "2026-11-10T08:00:00Z", usage(5, w1=30000, o=10)),
        asst(cold, "c2", "2026-11-10T08:30:00Z", usage(5, w5=100, r=30000, o=10, split=False)),
        asst(cold, "c3", "2026-11-10T10:00:00Z", usage(5, w1=30000, o=10))]))
    wb = {**a, "sessionId": "w1", "cwd": str(wt), "gitBranch": "agent-1"}
    w(P / enc(wt) / "w1.jsonl", jl([
        {**wb, "type": "user", "timestamp": "2026-11-09T10:00:00Z", "message": {"role": "user", "content": "hi"}},
        asst(wb, "w1m", "2026-11-09T10:00:05Z", usage(3, w5=900, o=9, split=False), model="claude-haiku-5-5")]))
    sdk = {"sessionId": "sdk1", "cwd": str(pa), "version": "2.1.290", "entrypoint": "sdk-py", "gitBranch": "main"}
    w(P / enc(pa) / "sdk1.jsonl", jl([
        asst(sdk, "k1", "2026-11-11T08:00:00Z", usage(100, o=20), model="claude-haiku-5-5"),
        asst(sdk, "k2", "2026-11-11T08:00:10Z", usage(100, o=20), model="weird-model"),
        asst(sdk, "k3", "2026-11-11T08:00:20Z", usage(100, o=20), model="claude-test-1")]))
    w(P / enc(pb) / "nosession.jsonl", jl([
        {"type": "user", "cwd": str(pb), "timestamp": "2026-11-08T07:00:00Z", "message": {"role": "user", "content": "plain"}},
        {"type": "assistant", "cwd": str(pb), "timestamp": "2026-11-08T07:00:05Z", "requestId": "rq1",
         "message": {"model": "claude-opus-5-5", "content": [], "usage": usage(8, w5=2000, o=30, split=False)}},
        "not an object", {"type": "assistant", "message": "broken"}]))
    old = {**a, "sessionId": "old1", "gitBranch": "main"}
    w(P / enc(mesell) / "old1.jsonl", jl([
        {**old, "type": "user", "timestamp": "2026-09-01T10:00:00Z", "message": {"role": "user", "content": "long ago"}},
        asst(old, "o1", "2026-09-01T10:00:05Z", usage(50, w1=25000, o=500), [tool("o", "Skill", skill="/quiet")])]))
    part = {**a, "sessionId": "part1", "cwd": str(pb), "gitBranch": "dev"}
    w(P / enc(pb) / "part1.jsonl", jl([asst(part, "p1", "2026-11-11T09:00:00Z", usage(4, w5=4000, o=40, split=False))])
      + json.dumps(asst(part, "p2", "2026-11-11T09:10:00Z", usage(4, r=4000, o=44, split=False)))[:60])
    w(P / enc(pb) / "empty.jsonl", "")
    w(P / enc(H) / "home.jsonl", jl([asst({**a, "sessionId": "home1", "cwd": str(H), "gitBranch": None}, "h1", "2026-11-07T10:00:00Z",
                                           usage(2, o=2))]))
    for i, p in enumerate(sorted(P.rglob("*.jsonl"))):
        os.utime(p, (1_790_000_000 + i * 60, 1_790_000_000 + i * 60))

    w(H / "Library" / "Application Support" / "Claude" / "claude-code-sessions" / "x" / "y" / "local_1.json", json.dumps(
        {"cliSessionId": A, "sessionId": "local_1", "title": "Deploy the fleet", "branch": "main", "sourceBranch": "main",
         "createdAt": 1790000000000, "isArchived": False}))
    w(H / "Library" / "Application Support" / "Claude" / "claude-code-sessions" / "x" / "y" / "local_2.json", json.dumps(
        {"cliSessionId": "w1", "sessionId": "local_2", "title": "Agent one", "branch": "claude/agent-1", "isArchived": True,
         "cwd": str(pb), "worktreePath": str(pb / "sub")}))
    w(C / "history.jsonl", "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in (
        {"display": "a", "project": str(pb)}, {"display": "b", "project": str(mesell)})))
    w(H / ".zshrc", f"alias pb='cd {pb}'\n")
    w(H / "Knowledge" / "reels" / "summary.json", json.dumps({
        "notes": 3, "open_actions": 2, "updated": "2026-11-04", "next": [], "trends": ["Skills everywhere"],
        "projects": [{"name": "Mesell", "folder": "mesell", "strong": 1, "total": 2, "top": []}]}))
    w(H / "bin" / "claude", '#!/bin/sh\n[ "$1" = agents ] && echo "[]" && exit 0\nexit 1\n')
    (H / "bin" / "claude").chmod(0o755)
    w(H / ".stellaxis" / "config.json", json.dumps({
        "scan_roots": [str(code)], "port": 1, "claude_bin": str(H / "bin" / "claude"), "max_sessions": 5,
        "pricing": {"claude-test": {"in": 1.0, "out": 2.0}},
        "projects": [{"id": "parts", "name": "Parts", "repos": [str(pa), str(pb), str(pc)]}, {"name": "no id"}],
        "project_notes": [{"name": "Reel Brain", "summary": "notes", "facts": {"notes": 3}, "links": [{"url": "https://x.invalid"}],
                           "open": ["one thing"]}]}))
    w(H / ".stellaxis" / "tasks" / "parts.md", "- [ ] first\n- [x] done\n")


def change(H: Path) -> None:
    """What happens between the passes: a session goes on, a cut line is finished, one file is rewritten shorter,
    one is deleted, one is new, and a skill is edited."""
    C, code = H / ".claude", H / "code"
    P, mesell, pb = C / "projects", code / "mesell", code / "partsB"
    a = {"sessionId": A, "cwd": str(mesell), "version": "2.1.301", "entrypoint": "cli", "gitBranch": "main"}
    with open(P / enc(mesell) / f"{A}.jsonl", "a") as fh:
        fh.write(jl([
            {**a, "type": "user", "timestamp": "2026-11-11T12:30:00Z", "origin": {"kind": "human"},
             "message": {"role": "user", "content": [{"type": "text", "text": "<command-name>/toolkit:fix</command-name>"}]}},
            att(a, "2026-11-11T12:30:01Z", type="skill_listing", content="- stale: Never used skill\n"),
            att(a, "2026-11-11T12:30:01Z", type="deferred_tools_delta", removedNames=["Monitor"], readdedNames=["mcp__fake__t0"]),
            asst(a, "m4", "2026-11-11T12:30:05Z", usage(6, w1=21000, o=60), [tool("t10", "Skill", skill="stale")])]))
    part = {**a, "sessionId": "part1", "cwd": str(pb), "gitBranch": "dev", "version": "2.1.300"}
    full = json.dumps(asst(part, "p2", "2026-11-11T09:10:00Z", usage(4, r=4000, o=44, split=False)))
    with open(P / enc(pb) / "part1.jsonl", "a") as fh:
        fh.write(full[60:] + "\n")
    f = {**a, "sessionId": F, "gitBranch": "feature/x", "version": "2.1.300"}
    w(P / enc(mesell) / f"{F}.jsonl", jl([
        asst(f, "m2", "2026-11-10T09:02:00Z", usage(5, w5=500, r=20000, o=50, split=False), [tool("t3", "mcp__localsrv__search")]),
        asst(f, "m5", "2026-11-10T11:00:05Z", usage(7, w5=300, r=20500, o=70, split=False))]))
    (P / enc(pb) / "nosession.jsonl").unlink()
    z = {"sessionId": "new1", "cwd": str(code / "partsC"), "version": "2.1.301", "entrypoint": "cli", "gitBranch": "main"}
    w(P / enc(code / "partsC") / "new1.jsonl", jl([asst(z, "z1", "2026-11-11T12:45:00Z", usage(9, w5=9000, o=90, split=False))]))
    for i, name in enumerate([f"{enc(mesell)}/{A}.jsonl", f"{enc(pb)}/part1.jsonl", f"{enc(mesell)}/{F}.jsonl",
                              f"{enc(code / 'partsC')}/new1.jsonl"]):
        os.utime(P / name, (1_790_100_000 + i * 60, 1_790_100_000 + i * 60))
    w(C / "skills" / "foo" / "SKILL.md", "---\nname: foo\ndescription: Does foo things, and more\n---\nbody\n")


class Clock:
    """A fixed clock for the code under test: STELLAXIS_NOW where the code knows it, else patched in."""

    def __init__(self, C, centre):
        self.C, self.centre = C, centre
        import core.util
        self.new = hasattr(core.util, "now")

    def set(self, iso: str) -> None:
        os.environ["STELLAXIS_NOW"] = iso
        if self.new:
            return
        at = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))

        class Fixed(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return at.astimezone(tz) if tz else at.replace(tzinfo=None)
        self.C.now_utc = lambda: at
        if self.centre is not None:
            self.centre.datetime = Fixed


def files(stx: Path) -> dict:
    out = {}
    for name in ("snapshot.json", "projects.json", "work" + "spaces.json", "status.json", "cache.json"):
        try:
            out[name] = json.loads((stx / name).read_text())
        except (OSError, ValueError):
            out[name] = None
    out["history.jsonl"] = (stx / "history.jsonl").read_text().splitlines() if (stx / "history.jsonl").exists() else None
    cache = out["cache.json"] or {}
    owners = {zlib.crc32(k.encode()): k for k in cache.get("files") or {}}
    cache["ids"] = {i: owners.get(o, o) for i, o in (cache.get("ids") or {}).items()}   # the owning file, not its checksum
    for st in (cache.get("files") or {}).values():   # byte counts hold the length of the temp path: count lines instead
        data = Path(st["path"]).read_bytes()
        st["offset"] = {"lines": data[:st["offset"]].count(b"\n"), "at a line end": st["offset"] == 0 or data[st["offset"] - 1:st["offset"]] == b"\n"}
        st["size"] = "the whole file" if st["size"] == len(data) else "not the whole file"
    (cache.get("walk") or {}).pop("at", None)
    for pr in (cache.get("probes") or {}).values():
        pr.pop("at", None)
    return out


def http_calls(C, H: Path) -> dict:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = {}

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=30)
        raw = None if body is None else json.dumps(body).encode()
        c.request(method, path, body=raw, headers={"Content-Type": "application/json"} if raw is not None else {})
        r = c.getresponse()
        data = r.read()
        c.close()
        try:
            got = json.loads(data)
        except ValueError:
            got = "not JSON"
        out[f"{method} {path}" + (" " + str(len(out)) if f"{method} {path}" in out else "")] = [r.status, got]
        return got
    try:
        mesell = str(H / "code" / "mesell")
        for path in ("/api/status", "/api/snapshot", "/snapshot.json", "/api/projects", "/api/" + "work" + "spaces",
                     "/api/projects/parts/tasks", "/api/projects/parts/work", "/api/projects/unassigned/tasks", "/api/approvals",
                     "/api/skills", "/overview", "/hierarchy", "/settings", "/api/settings", "/api/settings?scope=nope",
                     f"/api/settings?scope=project&project={mesell}", f"/api/settings?scope=local&project={mesell}",
                     "/api/settings?scope=project&project=/nowhere", "/api/settings/other"):
            call("GET", path)
        cur = call("GET", "/api/settings?scope=user")
        data = {**cur["data"], "model": "sonnet", "cleanupPeriodDays": 30}
        call("POST", "/api/settings/preview", {"scope": "user", "data": data})
        call("POST", "/api/settings/preview", {"scope": "user", "data": {"model": 1}})
        call("POST", "/api/settings/save", {"scope": "user", "data": data, "base_sha": "wrong"})
        call("POST", "/api/settings/save", {"scope": "user", "data": data, "base_sha": cur["sha"]})
        call("POST", "/api/settings/save", {"scope": "local", "project": mesell, "data": {"model": "haiku"}, "base_sha": None})
        call("GET", "/api/settings?scope=user")
        call("GET", f"/api/settings?scope=local&project={mesell}")
    finally:
        srv.shutdown()
        srv.server_close()
    return out


def prune_calls(prune, H: Path) -> dict:
    out = {}

    def run(*argv):
        buf, code = io.StringIO(), 0
        old = sys.argv
        sys.argv = ["prune.py", *argv]
        try:
            with contextlib.redirect_stdout(buf):
                prune.main()
        except SystemExit as e:
            code = e.code
        finally:
            sys.argv = old
        out[" ".join(argv) + (" #" + str(len(out)) if " ".join(argv) in out else "")] = [code, buf.getvalue()]
    mesell, code = str(H / "code" / "mesell"), H / "code"
    run("list")
    run("list", "--kind", "skill", "--unused-days", "1")
    run("list", "--scope", "project")
    ids = ["skill:user:stale", "mcp:user:unused-http", "plugin:user:toolkit@mkt", "agent:project:~/code/mesell:planner",
           "mcp:local:~/code/mesell:localsrv", "mcp:project:~/code/mesell:projsrv", "skill:user:toolkit@mkt:toolkit:lint",
           "skill:account:acct", "claude_md:project:~/code/mesell:CLAUDE.local.md", "rule:user:style.md", "nope:x:y"]
    run("archive", *ids)
    run("move", "agent:user:planner", "skill:user:foo", "mcp:user:fake", "agent:project:~/code/mesell:scout", "--to", str(code / "partsB"))
    run("relocate", mesell, str(code / "moved"))
    run("relocate", mesell, str(code / "partsA"))
    run("relocate", str(code / "partsB"), str(code / "partsB2"))
    run("archive", *ids, "--apply")
    run("archives")
    run("move", "agent:user:planner", "mcp:user:fake", "--to", str(code / "partsB"), "--apply")
    run("relocate", str(code / "partsB"), str(code / "partsB2"), "--apply")
    out["tree after"] = sorted(str(p.relative_to(H)) for p in H.rglob("*")
                               if p.is_file() and ".stellaxis" not in p.parts and ".git" not in p.parts and "json-backups" not in p.parts)
    out["claude.json after"] = json.loads((H / ".claude.json").read_text())
    run("restore", "last")
    run("restore", "last", "--apply")
    run("restore", "last", "--apply")
    run("restore", "last", "--apply")
    run("archives")
    run("purge", "--older-than", "0s")
    run("restore", "nothing-here")
    out["tree restored"] = sorted(str(p.relative_to(H)) for p in H.rglob("*")
                                  if p.is_file() and ".stellaxis" not in p.parts and ".git" not in p.parts and "json-backups" not in p.parts)
    out["claude.json restored"] = json.loads((H / ".claude.json").read_text())
    return out


def plain(obj, H: Path, repo: Path):
    """The same object with everything that may differ between two runs replaced by a fixed word."""
    text = json.dumps(obj)
    main = str(repo).split("/.claude/worktrees/")[0]
    for real in dict.fromkeys([os.path.realpath(H), str(H)]):
        text = text.replace(enc(real), "<H->").replace(real, "<H>")
    for real in dict.fromkeys([str(repo), main]):
        text = text.replace(real, "<REPO>")
    text = re.sub(r"\d{8}-\d{6}(-\d+)?", "<STAMP>", text)
    text = re.sub(r'"elapsed_ms": \d+', '"elapsed_ms": 0', text)
    text = re.sub(r'"(pid|at)": [0-9.]+', r'"\1": 0', text)
    text = re.sub(r'"host": "[^"]*"', '"host": "<HOST>"', text)
    text = re.sub(r'"error": "fatal: [^"]*"', '"error": "<GIT>"', text)
    return json.loads(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collector", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    H = Path(os.environ["HOME"])
    collector = Path(a.collector).resolve()
    if os.environ.get("STELLAXIS_HOME") != str(H / ".stellaxis") or os.environ.get("CLAUDE_CONFIG_DIR") or any(H.iterdir()):
        sys.exit("run with HOME set to an empty folder, STELLAXIS_HOME to <HOME>/.stellaxis and no CLAUDE_CONFIG_DIR")
    os.environ.update({k: v for k, v in GIT_ENV.items() if k.startswith("GIT_CONFIG")})
    for k in ("REELS_HOME", "SKILLS_HOME", "STELLAXIS_DESKTOP_RUNNING"):
        os.environ.pop(k, None)
    os.environ["STELLAXIS_DESKTOP_RUNNING"] = "0"
    build(H)
    sys.path.insert(0, str(collector))
    import collect as C
    import prune
    import mcp_server
    core = C.the_core()
    centre = sys.modules.get("stx_project_management.centre")
    clock = Clock(C, centre)
    out, stx = {}, H / ".stellaxis"
    clock.set(T1)
    with contextlib.redirect_stdout(io.StringIO()):
        C.collect(probe=True)
    out["pass 1"] = files(stx)
    change(H)
    clock.set(T2)
    C.collect()
    out["pass 2"] = files(stx)
    C.collect()
    out["pass 3, nothing new"] = files(stx)
    out["mcp summary"] = mcp_server.view({"view": "summary"})
    out["mcp sessions of one folder"] = mcp_server.view({"view": "sessions", "project": "~/code/mesell", "limit": 2})
    out["prune"] = prune_calls(prune, H)
    out["after prune"] = files(stx)
    out["routes"] = http_calls(C, H)
    out["settings files after the saves"] = {
        str(p.relative_to(H)): p.read_text() for p in sorted([*(H / ".claude").glob("settings.json*"),
                                                              *(H / "code" / "mesell" / ".claude").glob("settings*")])}
    Path(a.out).write_text(json.dumps(plain(out, H, collector.parent), indent=1, sort_keys=False) + "\n")


if __name__ == "__main__":
    main()
