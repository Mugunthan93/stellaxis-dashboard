"""End-to-end test: builds a fake HOME with a Mac-like Claude setup, runs the collector and prune.

  python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COLLECT = REPO / "collector" / "collect.py"
PRUNE = REPO / "collector" / "prune.py"
MCP = REPO / "collector" / "mcp_server.py"

FAKE_MCP = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "initialize":
            r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"},
                 "instructions": "Use fake tools wisely."}
        elif m.get("method") == "tools/list":
            r = {"tools": [{"name": f"t{i}", "description": "x" * 400, "inputSchema": {"type": "object"}} for i in range(5)]}
        else:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')


def w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip())


def jl(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        H = cls.home = Path(cls.tmp.name)
        C = H / ".claude"
        proj = H / "code" / "mesell"
        cls.proj = proj
        w(C / "CLAUDE.md", "# Global\nAlways be brief.\n@~/.claude/extra.md\n")
        w(C / "extra.md", "Extra imported rules " * 20)
        w(C / "skills" / "foo" / "SKILL.md", "---\nname: foo\ndescription: Does foo things\n---\nbody " * 1)
        w(C / "skills" / "stale" / "SKILL.md", "---\nname: stale\ndescription: Never used skill\n---\nbody\n")
        w(C / "agents" / "planner.md", "---\nname: planner\ndescription: Plans work\ntools: Read, Grep\n---\nYou plan.\n")
        w(C / "commands" / "deploy.md", "---\ndescription: Deploy the app\n---\nDeploy.\n")
        w(H / "fake_mcp.py", FAKE_MCP)
        (H / ".claude.json").write_text(json.dumps({
            "mcpServers": {"fake": {"type": "stdio", "command": sys.executable, "args": [str(H / "fake_mcp.py")]},
                           "unused-http": {"type": "http", "url": "https://example.invalid/mcp"}},
            "projects": {str(proj): {"mcpServers": {"localsrv": {"command": "true"}}}},
        }))
        plug = C / "plugins" / "cache" / "mkt" / "toolkit" / "1.0.0"
        w(plug / ".claude-plugin" / "plugin.json", '{"name": "toolkit", "version": "1.0.0", "description": "kit"}')
        w(plug / "skills" / "lint" / "SKILL.md", "---\nname: lint\ndescription: Lint code\n---\nLint.\n")
        w(plug / "agents" / "reviewer.md", "---\nname: reviewer\ndescription: Reviews code\n---\nReview.\n")
        w(plug / ".mcp.json", '{"mcpServers": {"kitsrv": {"command": "true"}}}')
        (C / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"toolkit@mkt": [{"scope": "user", "installPath": str(plug), "version": "1.0.0"}]}}))
        (C / "settings.json").write_text(json.dumps({"enabledPlugins": {"toolkit@mkt": True}}))
        w(proj / "CLAUDE.md", "# mesell\nFleet of 19 agents.\n")
        w(proj / ".claude" / "agents" / "planner.md", "---\nname: planner\ndescription: Project planner\n---\nPlan.\n")
        w(proj / ".claude" / "agents" / "fleet" / "scout.md", "---\nname: scout\ndescription: Scouts\n---\nScout.\n")
        w(proj / "api" / "CLAUDE.md", "API notes\n")
        (proj / ".git").mkdir()
        wt = proj / ".claude" / "worktrees" / "agent-1"
        w(wt / ".claude" / "agents" / "planner.md", "---\nname: planner\ndescription: copy\n---\nPlan.\n")
        w(wt / ".git", f"gitdir: {proj}/.git/worktrees/agent-1\n")
        enc = "-" + str(proj).strip("/").replace("/", "-")
        sid = "11111111-2222-3333-4444-555555555555"
        base = {"sessionId": sid, "cwd": str(proj), "version": "2.1.300", "entrypoint": "cli", "gitBranch": "main"}

        def asst(mid, ts, blocks, usage):
            return {**base, "type": "assistant", "timestamp": ts, "isSidechain": False,
                    "message": {"id": mid, "model": "claude-opus-5-5", "content": blocks, "usage": usage}}
        u1 = {"input_tokens": 10, "cache_creation_input_tokens": 20000, "cache_read_input_tokens": 0, "output_tokens": 100,
              "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20000}}
        u2 = {"input_tokens": 5, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 20000, "output_tokens": 50}
        rows = [
            {**base, "type": "user", "timestamp": "2026-10-01T10:00:00Z", "origin": {"kind": "human"},
             "message": {"role": "user", "content": "<command-name>/deploy</command-name> go"}},
            asst("m1", "2026-10-01T10:00:05Z", [{"type": "tool_use", "id": "a", "name": "Skill", "input": {"skill": "foo"}}], u1),
            asst("m1", "2026-10-01T10:00:05Z", [{"type": "tool_use", "id": "b", "name": "Agent",
                                                  "input": {"subagent_type": "planner", "prompt": "x"}}], u1),
            {**base, "type": "user", "timestamp": "2026-10-01T10:01:00Z",
             "toolUseResult": {"agentId": "ag1", "totalTokens": 4321},
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "done"}]}},
            asst("m2", "2026-10-01T10:02:00Z", [{"type": "tool_use", "id": "c", "name": "mcp__localsrv__search", "input": {}},
                                                 {"type": "tool_use", "id": "d", "name": "Skill", "input": {"skill": "toolkit:lint"}}], u2),
        ]
        w(C / "projects" / enc / f"{sid}.jsonl", jl(rows))
        sub = [{**base, "type": "assistant", "timestamp": "2026-10-01T10:00:30Z", "isSidechain": True,
                "message": {"id": "s1", "model": "claude-sonnet-5-5", "content": [], "usage": u2}}]
        w(C / "projects" / enc / sid / "subagents" / "agent-ag1.jsonl", jl(sub))
        wenc = "-" + str(wt).strip("/").replace("/", "-").replace(".", "-")
        w(C / "projects" / wenc / "w1.jsonl", jl([{**base, "cwd": str(wt), "sessionId": "w1", "type": "user",
                                                   "timestamp": "2026-10-02T10:00:00Z",
                                                   "message": {"role": "user", "content": "hi"}}]))
        cls.env = {**os.environ, "HOME": str(H), "STELLAXIS_HOME": str(H / ".stellaxis")}
        cls.env.pop("CLAUDE_CONFIG_DIR", None)
        (H / ".stellaxis").mkdir()
        (H / ".stellaxis" / "config.json").write_text(json.dumps({"scan_roots": [str(H / "code")]}))
        cls.run_py(COLLECT, "run", "--probe")
        cls.snap = json.loads((H / ".stellaxis" / "snapshot.json").read_text())
        cls.items = {i["id"]: i for i in cls.snap["items"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_py(cls, script, *args, inp=None):
        r = subprocess.run([sys.executable, str(script), *args], env=cls.env, capture_output=True, text=True, input=inp,
                           timeout=120)
        if r.returncode:
            raise AssertionError(f"{script.name} {args} failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def test_inventory(self):
        ids = set(self.items)
        for want in ["claude_md:user:~/.claude/CLAUDE.md", "skill:user:foo", "skill:user:stale", "agent:user:planner",
                     "command:user:deploy", "mcp:user:fake", "plugin:user:toolkit@mkt", "skill:user:toolkit@mkt:toolkit:lint",
                     "agent:project:~/code/mesell:planner", "agent:project:~/code/mesell:scout",
                     "mcp:local:~/code/mesell:localsrv", "claude_md:project:~/code/mesell:CLAUDE.md"]:
            self.assertIn(want, ids)
        self.assertNotIn("claude_md:project:~/code/mesell/api:CLAUDE.md", ids, "nested CLAUDE.md is not a project")
        self.assertTrue(self.items["claude_md:project:~/code/mesell:api/CLAUDE.md"]["conditional"])
        cm = self.items["claude_md:user:~/.claude/CLAUDE.md"]
        self.assertGreater(cm["bytes_always"], 400, "imports should be counted")

    def test_worktree(self):
        p = next(p for p in self.snap["projects"] if p["path"].endswith("worktrees/agent-1"))
        self.assertEqual(p["worktree_of"], "~/code/mesell")
        self.assertFalse(any("worktrees" in i["id"] for i in self.snap["items"]))

    def test_status_file(self):
        st = json.loads((self.home / ".stellaxis" / "status.json").read_text())
        self.assertEqual(st["phase"], "idle")
        self.assertIn("last_snapshot", st)

    def test_usage(self):
        self.assertEqual(self.items["skill:user:foo"]["usage"]["calls"], 1)
        self.assertEqual(self.items["skill:user:stale"]["usage"]["calls"], 0)
        self.assertEqual(self.items["agent:user:planner"]["usage"]["agent_tokens"], 4321)
        self.assertEqual(self.items["command:user:deploy"]["usage"]["calls"], 1)
        self.assertEqual(self.items["mcp:local:~/code/mesell:localsrv"]["usage"]["calls"], 1)
        self.assertEqual(self.items["skill:user:toolkit@mkt:toolkit:lint"]["usage"]["calls"], 1)
        s = next(x for x in self.snap["sessions"] if x["id"].startswith("1111"))
        self.assertEqual(s["baseline_tokens"], 20010)
        self.assertEqual(s["subagent_files"], 1)
        # m1 duplicated line must be counted once: 2 opus msgs + 1 sonnet subagent msg
        self.assertEqual(s["tokens"]["output"], 100 + 50 + 50)
        self.assertGreater(s["cost"], 0)

    def test_probe_and_duplicates(self):
        fake = self.items["mcp:user:fake"]
        self.assertEqual(fake["source"], "probe")
        self.assertGreater(fake["tokens_always"], 400)
        names = {(d["kind"], d["name"]) for d in self.snap["duplicates"]}
        self.assertIn(("agent", "planner"), names)
        proj = next(p for p in self.snap["projects"] if p["path"] == "~/code/mesell")
        self.assertEqual(proj["sessions"], 1)

    def test_mcp_server(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "stellaxis_snapshot",
                                                                               "arguments": {"view": "summary"}}}]
        out = self.run_py(MCP, inp="\n".join(json.dumps(m) for m in msgs) + "\n")
        res = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual([r["id"] for r in res], [1, 2, 3])
        payload = json.loads(res[2]["result"]["content"][0]["text"])
        self.assertIn("totals", payload)

    def test_zz_prune_roundtrip(self):
        C = self.home / ".claude"
        out = self.run_py(PRUNE, "archive", "skill:user:stale", "mcp:user:unused-http")
        self.assertIn("dry-run", out)
        self.assertTrue((C / "skills" / "stale").exists())
        self.run_py(PRUNE, "archive", "skill:user:stale", "mcp:user:unused-http", "plugin:user:toolkit@mkt", "--apply")
        self.assertFalse((C / "skills" / "stale").exists())
        self.assertNotIn("unused-http", json.loads((self.home / ".claude.json").read_text())["mcpServers"])
        self.assertFalse(json.loads((C / "settings.json").read_text())["enabledPlugins"]["toolkit@mkt"])
        self.run_py(PRUNE, "move", "agent:user:planner", "--to", str(self.home / "code" / "other"), "--apply")
        self.assertTrue((self.home / "code" / "other" / ".claude" / "agents" / "planner.md").exists())
        self.run_py(PRUNE, "restore", "last", "--apply")
        self.assertTrue((C / "agents" / "planner.md").exists())
        self.run_py(PRUNE, "restore", "last", "--apply")
        self.assertTrue((C / "skills" / "stale" / "SKILL.md").exists())
        self.assertIn("unused-http", json.loads((self.home / ".claude.json").read_text())["mcpServers"])
        self.assertTrue(json.loads((C / "settings.json").read_text())["enabledPlugins"]["toolkit@mkt"])


if __name__ == "__main__":
    unittest.main()
