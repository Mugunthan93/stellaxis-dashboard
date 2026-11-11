"""Checks 2 and 3 of the core spec: with a component removed the rest still runs, and no component reaches another.

  python3 -m unittest tests.test_components
"""
import ast
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPS = REPO / "collector" / "components"
NAMES = ("claude_management", "memory_management", "project_management")


class RemoveOneTest(unittest.TestCase):
    """Check 2: with one component folder removed, the core and the others still collect and serve."""
    CHILD = '''
import http.client, http.server, json, sys, threading
import collect, prune, mcp_server          # each must import with the component gone
collect.collect()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), collect.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request("GET", path); r = c.getresponse(); return r.status, r.read().decode()
core = json.loads(get("/api/core")[1])
out = {"components": {c["name"]: c["error"] for c in core["components"]},
       "codes": {p: get(p)[0] for p in ("/overview", "/api/projects", "/api/settings", "/api/memory", "/api/status")},
       "status": json.loads(get("/api/status")[1])}
snap_f, index_f = collect.STX_HOME / "snapshot.json", collect.STX_HOME / "projects.json"
out["snapshot"], out["index"] = snap_f.exists(), index_f.exists()
snap = json.loads(snap_f.read_text()) if out["snapshot"] else {}
index = json.loads(index_f.read_text()) if out["index"] else {}
out["items"] = [it["id"] for it in snap.get("items", [])]
out["snapshot_sessions"] = {s["id"]: s["cost"] for s in snap.get("sessions", [])}
out["index_sessions"] = {s["id"]: s["cost"] for p in index.get("projects", []) for r in p["repos"]
                         for b in r["branches"] for s in b["sessions"]}
srv.shutdown(); print(json.dumps(out))
'''

    def copy(self, *removed: str) -> tuple[Path, dict]:
        """A copy of collector/ and dashboard/ without the named component folders, and the env of a fake home."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO / "collector", root / "collector", ignore=ignore)
        shutil.copytree(REPO / "dashboard", root / "dashboard", ignore=ignore)
        for name in removed:
            shutil.rmtree(root / "collector" / "components" / name)
        home = root / "home"
        (home / ".claude" / "skills" / "foo").mkdir(parents=True)
        (home / ".claude" / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: a test skill\n---\nfoo\n")
        app = home / "code" / "app"
        app.mkdir(parents=True)
        (app / "CLAUDE.md").write_text("# app\n")
        (home / ".claude" / "projects" / "p").mkdir(parents=True)
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        turn = {"type": "assistant", "sessionId": "s1", "cwd": str(app), "timestamp": ts, "uuid": "u1",
                "message": {"id": "msg_1", "model": "claude-opus-5-5", "role": "assistant", "content": [],
                            "usage": {"input_tokens": 100000, "output_tokens": 10000}}}
        (home / ".claude" / "projects" / "p" / "s1.jsonl").write_text(json.dumps(turn) + "\n")
        (home / ".stellaxis").mkdir()
        (home / ".stellaxis" / "config.json").write_text(json.dumps(
            {"scan_roots": [], "claude_bin": "/usr/bin/false", "port": 1}))
        env = {k: v for k, v in os.environ.items() if k not in ("STELLAXIS_HOME", "CLAUDE_CONFIG_DIR", "REELS_HOME", "SKILLS_HOME")}
        env.update(HOME=str(home), STELLAXIS_HOME=str(home / ".stellaxis"))
        return root / "collector", env

    def child(self, removed: str) -> dict:
        cwd, env = self.copy(removed)
        r = subprocess.run([sys.executable, "-c", self.CHILD], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_without_claude_management(self):
        out = self.child("claude_management")
        self.assertEqual(out["components"], {"memory_management": None, "project_management": None})
        self.assertEqual((out["status"]["phase"], out["status"]["index_error"]), ("idle", None))
        self.assertEqual((out["codes"]["/overview"], out["codes"]["/api/projects"], out["codes"]["/api/memory"]), (200, 200, 200))
        self.assertEqual(out["codes"]["/api/settings"], 404)
        self.assertEqual((out["snapshot"], out["index_sessions"]), (False, {"s1": 0}))

    def test_without_project_management(self):
        out = self.child("project_management")
        self.assertEqual(out["components"], {"claude_management": None, "memory_management": None})
        self.assertEqual((out["status"]["phase"], out["status"]["index_error"]), ("idle", None))
        self.assertEqual((out["codes"]["/overview"], out["codes"]["/api/settings"], out["codes"]["/api/memory"]), (200, 200, 200))
        self.assertEqual(out["codes"]["/api/projects"], 404)
        self.assertIn("skill:user:foo", out["items"])
        self.assertGreater(out["snapshot_sessions"]["s1"], 0)
        self.assertFalse(out["index"])

    def test_without_memory_management(self):
        out = self.child("memory_management")
        self.assertEqual(out["components"], {"claude_management": None, "project_management": None})
        self.assertEqual(out["status"]["index_error"], None)
        self.assertEqual((out["codes"]["/api/projects"], out["codes"]["/api/settings"], out["codes"]["/api/memory"]), (200, 200, 404))
        self.assertTrue(out["snapshot"] and out["index"])
        self.assertGreater(out["index_sessions"]["s1"], 0)

    def test_with_no_component_at_all(self):
        cwd, env = self.copy(*NAMES)
        r = subprocess.run([sys.executable, "-c", self.CHILD], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["components"], {})
        self.assertEqual(out["codes"]["/overview"], 200)
        self.assertEqual((out["snapshot"], out["index"]), (False, False))

    def test_prune_says_so_when_claude_management_is_gone(self):
        cwd, env = self.copy(*NAMES)
        for args in (["list"], ["archive", "skill:user:foo"]):
            r = subprocess.run([sys.executable, "prune.py", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 1, args)
            self.assertEqual(r.stdout, "", args)
            self.assertNotIn("Traceback", r.stderr, args)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, (args, r.stderr))


class IndependenceTest(unittest.TestCase):
    """Check 3 of the core spec: a component reaches another one only through the core."""
    def files(self):
        out = [f for f in COMPS.rglob("*.py")]
        self.assertTrue(out)
        return out

    def test_a_component_imports_only_the_standard_library_the_core_and_itself(self):
        ok = set(sys.stdlib_module_names) | {"core"}
        for f in self.files():
            src = f.read_text()
            self.assertNotIn("import_folder", src, f.name)
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                    if (node.module or "").split(".")[0] == "core":
                        for a in node.names:
                            self.assertFalse(a.name.startswith("_"), f"{f.relative_to(REPO)} imports the private name {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 1, f"{f.relative_to(REPO)} imports from above its own folder")
                    continue
                else:
                    continue
                for n in names:
                    self.assertIn(n.split(".")[0], ok, f"{f.relative_to(REPO)} imports {n}")
                    self.assertNotEqual(n.split(".")[0], "importlib", f"{f.relative_to(REPO)} imports importlib")

    def test_no_component_names_the_tables_or_the_module_of_another(self):
        names = sorted(p.name for p in COMPS.iterdir() if (p / "__init__.py").is_file())
        self.assertGreaterEqual(len(names), 2)
        for f in self.files():
            mine, src = f.relative_to(COMPS).parts[0], f.read_text()
            for other in names:
                if other != mine:
                    self.assertNotIn(other + "_", src, f"{f.relative_to(REPO)} names a table of {other}")
                    self.assertNotIn("stx_" + other, src, f"{f.relative_to(REPO)} names the module of {other}")


if __name__ == "__main__":
    unittest.main()
