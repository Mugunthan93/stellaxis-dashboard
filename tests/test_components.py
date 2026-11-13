"""Checks 2 and 3 of the core spec: with a component removed the rest still runs, and no component reaches another.

  python3 -m unittest tests.test_components
"""
import ast
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
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
       "codes": {p: get(p)[0] for p in ("/overview", "/api/projects", "/api/settings", "/api/memory", "/api/status", "/api/share")},
       "status": json.loads(get("/api/status")[1])}
out["share"] = json.loads(get("/api/share")[1]) if out["codes"]["/api/share"] == 200 else None
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
        self.assertEqual(out["codes"]["/api/share"], 404)
        self.assertEqual((out["snapshot"], out["index_sessions"]), (False, {"s1": 0}))

    def test_without_project_management(self):
        out = self.child("project_management")
        self.assertEqual(out["components"], {"claude_management": None, "memory_management": None})
        self.assertEqual((out["status"]["phase"], out["status"]["index_error"]), ("idle", None))
        self.assertEqual((out["codes"]["/overview"], out["codes"]["/api/settings"], out["codes"]["/api/memory"]), (200, 200, 200))
        self.assertEqual(out["codes"]["/api/projects"], 404)
        self.assertEqual(out["codes"]["/api/share"], 200)
        self.assertEqual((out["share"]["projects"], out["share"]["unassigned"], out["share"]["budget"]), ([], None, None))
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
        self.assertEqual(out["codes"]["/api/share"], 200)
        self.assertEqual([l["id"] for l in out["share"]["projects"]], [])
        self.assertGreater(out["share"]["unassigned"]["use"], 0)

    def test_with_no_component_at_all(self):
        cwd, env = self.copy(*NAMES)
        r = subprocess.run([sys.executable, "-c", self.CHILD], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["components"], {})
        self.assertEqual(out["codes"]["/overview"], 200)
        self.assertEqual((out["snapshot"], out["index"]), (False, False))

    def prune_says(self, removed: str, *args: str) -> str:
        """The one plain line `prune.py <args>` prints, and exits 1, with only `removed` gone."""
        cwd, env = self.copy(removed)
        r = subprocess.run([sys.executable, "prune.py", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 1, args)
        self.assertEqual(r.stdout, "", args)
        self.assertNotIn("Traceback", r.stderr, args)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, (args, r.stderr))
        return r.stderr.strip()

    def test_prune_says_so_when_claude_management_is_gone(self):
        for args in (["list"], ["archive", "skill:user:foo"]):
            self.assertIn("Claude management", self.prune_says("claude_management", *args), args)

    def test_share_says_so_when_claude_management_is_gone(self):
        cwd, env = self.copy("claude_management")
        r = subprocess.run([sys.executable, "share.py", "show"], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", "Claude management is not loaded\n"))

    def test_prune_relocate_says_so_when_project_management_is_gone(self):
        self.assertEqual(self.prune_says("project_management", "relocate", "~/code/app", "~/code/app2"),   # ~ is the fake home
                         "Project management is not loaded")


class BrokenOneTest(unittest.TestCase):
    """A component that is there but fails to load is not a removed one: the component that reads it fails too, and the
    last good files stay as they were."""
    copy = RemoveOneTest.copy

    def two_passes(self, broken: str, file: str) -> tuple[Path, dict, dict]:
        """One good pass, then a syntax error in <broken>/<file>, then one more pass. The files and items before and after."""
        cwd, env = self.copy()
        stx = Path(env["STELLAXIS_HOME"])

        def state():
            files = {n: (stx / n).read_bytes() for n in ("snapshot.json", "history.jsonl", "projects.json")}
            with closing(sqlite3.connect(stx / "stellaxis.db")) as db:
                files["items"] = db.execute("SELECT * FROM claude_management_items ORDER BY id").fetchall()
                for t in ("weeks", "use"):
                    files[t] = db.execute(f"SELECT * FROM claude_management_share_{t} ORDER BY 1, 2").fetchall()
            return files

        run = lambda: subprocess.run([sys.executable, "collect.py", "run"], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        r = run()
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        before = state()
        with open(cwd / "components" / broken / file, "a") as f:
            f.write("\ndef (:\n")
        run()
        return stx, before, state()

    def test_project_management_broken_keeps_the_last_snapshot_history_and_items(self):
        stx, before, after = self.two_passes("project_management", "repos.py")
        self.assertTrue(before["items"])
        for key in ("snapshot.json", "history.jsonl", "items"):
            self.assertEqual(after[key], before[key], key)
        err = json.loads((stx / "status.json").read_text())["index_error"]
        self.assertIn("project_management: SyntaxError", err)
        self.assertIn("claude_management: RuntimeError: project_management did not load", err)

    def test_project_management_broken_keeps_the_weeks_and_the_use_of_the_share(self):
        stx, before, after = self.two_passes("project_management", "repos.py")
        self.assertTrue(before["weeks"] and before["use"])
        for key in ("weeks", "use"):
            self.assertEqual(after[key], before[key], key)

    def test_collect_run_names_a_failed_component_also_when_a_snapshot_was_written(self):
        cwd, env = self.copy()
        with open(cwd / "components" / "memory_management" / "__init__.py", "a") as f:
            f.write("\ndef (:\n")
        r = subprocess.run([sys.executable, "collect.py", "run"], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0)
        self.assertIn("\nsnapshot: ", r.stdout)
        [line] = r.stderr.splitlines()
        self.assertTrue(line.startswith("collect: memory_management: SyntaxError: "), line)

    def test_claude_management_broken_keeps_the_last_index(self):
        stx, before, after = self.two_passes("claude_management", "setup.py")
        self.assertIn(b'"cost": 0.', before["projects.json"])
        self.assertEqual(after["projects.json"], before["projects.json"])
        err = json.loads((stx / "status.json").read_text())["index_error"]
        self.assertIn("claude_management: SyntaxError", err)
        self.assertIn("project_management: RuntimeError: claude_management did not load", err)


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
