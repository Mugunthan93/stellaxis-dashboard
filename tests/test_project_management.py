"""Tests of the project_management component (collector/components/project_management).

  python3 -m unittest tests.test_project_management
"""
import ast
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
import collect as C
from core import component, server
from core.app import Core

COMPS = REPO / "collector" / "components"
W = component.import_folder(COMPS / "project_management").centre
CFG = {"projects": [], "claude_bin": "/no/claude"}
ROW = {"id": "s1", "project": "~/nowhere/x", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z",
       "cost": 1.5, "branch": "main"}


class ProjectManagementTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"STELLAXIS_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        env.start()
        self.addCleanup(env.stop)
        self.core = Core(self.home, self.home / ".claude", COMPS)

    def get(self, srv, path):
        with closing(http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)) as conn:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read()

    def test_the_component_loads(self):
        [c] = [c for c in self.core.components if c.name == "project_management"]
        self.assertEqual((c.error, len(c.readers), c.handle is not None), (None, 1, True))

    def test_nothing_outside_the_core_imports_the_component(self):
        self.assertFalse((REPO / "collector" / ("work" + "spaces.py")).exists())
        for name in ("collect.py", "unplaced/jarvis.py", "prune.py", "mcp_server.py"):
            src = (REPO / "collector" / name).read_text()
            self.assertNotIn("import " + "work" + "spaces", src, name)
            self.assertNotIn("project_management import", src, name)

    def test_ways_in_read_the_offers(self):
        (self.home / "projects.json").write_text(json.dumps({"projects": [{"id": "x", "name": "X", "repos": []}]}))
        with mock.patch.object(W, "live", return_value=None), mock.patch.object(W, "pending", return_value=[{"id": "r"}]):
            self.assertEqual([g["id"] for g in self.core.read("project_management", "index", CFG)], ["x"])
            self.assertEqual(self.core.read("project_management", "live", CFG), [])
            self.assertEqual(self.core.read("project_management", "approvals", CFG), [{"id": "r"}])
        cfg = {**CFG, "projects": [{"id": "p", "name": "P"}, {"name": "no id"}]}
        self.assertEqual([p["id"] for p in self.core.read("project_management", "projects", cfg)], ["p"])
        (self.home / "projects.json").unlink()
        self.assertEqual(self.core.read("project_management", "index", CFG), [])

    def test_a_pass_writes_the_index_from_its_inputs(self):
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            res = self.core.run_pass(dict(CFG), {"sessions": [ROW], "folders": []})
        self.assertEqual(res["components"]["project_management"], {"readers": 1, "errors": []})
        idx = json.loads((self.home / "projects.json").read_text())
        self.assertEqual([w["id"] for w in idx["projects"]], ["unassigned"])
        self.assertEqual(idx["projects"][0]["repos"][0]["branches"][0]["sessions"][0]["id"], "s1")

    def test_notes_cards_are_found_under_either_key(self):
        card = {"name": "Reel Brain", "summary": "s"}
        self.assertEqual([n["name"] for n in C.project_notes({"projects": [card], "project_notes": []})], ["Reel Brain"])
        self.assertEqual([n["name"] for n in C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": [card]})], ["Reel Brain"])
        self.assertEqual(C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": []}), [])
        self.assertNotIn("work" + "spaces", C.DEFAULT_CONFIG)

    def test_a_pass_without_inputs_leaves_the_index_alone(self):
        res = self.core.run_pass(dict(CFG))
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        self.assertFalse((self.home / "projects.json").exists())

    def test_the_reader_writes_to_the_cores_home_not_the_environments(self):
        elsewhere = self.home / "elsewhere"
        core = Core(elsewhere, self.home / ".claude", COMPS)
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            res = core.run_pass(dict(CFG), {"sessions": [ROW], "folders": []})
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        self.assertTrue((elsewhere / "projects.json").is_file())
        self.assertFalse((self.home / "projects.json").exists())

    def test_outside_a_component_call_the_centre_reads_the_environment(self):
        self.assertEqual(W._home(), self.home)
        class Ctx:
            home, claude_dir = self.home / "a", self.home / "b"
        with W.at(Ctx):
            self.assertEqual((W._home(), W._claude_dir()), (self.home / "a", self.home / "b"))
        self.assertEqual(W._claude_dir(), self.home / ".claude")

    def test_routes_are_served_through_the_core_also_with_the_store_down(self):
        (self.home / "broken").mkdir()
        (self.home / "broken" / "stellaxis.db").mkdir()
        core = Core(self.home / "broken", self.home / ".claude", COMPS)
        self.assertTrue(core.error)
        srv = server.serve(core, 0, dict(CFG))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with mock.patch.object(W, "live", return_value=[]):
            status, body = self.get(srv, "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"], [])

    def test_write_index_reports_a_reader_error_and_a_load_error(self):
        snap = {"sessions": [ROW], "projects": []}
        with mock.patch.object(C, "_core", self.core):
            with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
                self.assertIsNone(C.write_index(dict(CFG), snap))
            with mock.patch.object(W, "live", side_effect=ValueError("boom")):
                self.assertEqual(C.write_index(dict(CFG), snap), "ValueError: boom")
            [c] = [c for c in self.core.components if c.name == "project_management"]
            with mock.patch.object(c, "error", "ImportError: gone"):
                self.assertEqual(C.write_index(dict(CFG), snap), "ImportError: gone")
        with mock.patch.object(C, "_core", Core(self.home, self.home / ".claude", self.home / "no-components")):
            self.assertEqual(C.write_index(dict(CFG), snap), "the project_management component is missing")

    def test_the_uncapped_session_list_reaches_the_reader(self):
        cfg = {**CFG, "_sessions_all": [ROW, {**ROW, "id": "s2"}]}
        with mock.patch.object(C, "_core", self.core), mock.patch.object(W, "live", return_value=[]), \
                mock.patch.object(W, "app_records", return_value={}):
            self.assertIsNone(C.write_index(cfg, {"sessions": [ROW], "projects": []}))
        self.assertNotIn("_sessions_all", cfg)
        ids = [s["id"] for s in json.loads((self.home / "projects.json").read_text())["projects"][0]["repos"][0]["branches"][0]["sessions"]]
        self.assertEqual(sorted(ids), ["s1", "s2"])


class BrokenComponentTest(unittest.TestCase):
    """The spec's anti-goal: a core that needs a component to start."""
    CHILD = '''
import http.client, http.server, json, sys, threading
import collect, prune, mcp_server          # each must import with the component broken
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), collect.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request("GET", path); r = c.getresponse(); return r.status, r.read().decode()
out = {"core": json.loads(get("/api/core")[1]), "overview": get("/overview")[0], "jarvis": get("/api/jarvis")[0],
       "index_error": collect.write_index({}, {"sessions": [], "projects": []})}
srv.shutdown(); print(json.dumps(out))
'''

    def child(self) -> dict:
        """Runs CHILD in a copy of collector/ and dashboard/ whose project_management component raises on import."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO / "collector", root / "collector", ignore=ignore)
        shutil.copytree(REPO / "dashboard", root / "dashboard", ignore=ignore)
        (root / "collector" / "components" / "project_management" / "__init__.py").write_text('raise RuntimeError("boom")\n')
        home = root / "home"
        (home / ".claude").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in ("STELLAXIS_HOME", "CLAUDE_CONFIG_DIR", "REELS_HOME")}
        env.update(HOME=str(home), STELLAXIS_HOME=str(home / ".stellaxis"), CLAUDE_CONFIG_DIR=str(home / ".claude"))
        r = subprocess.run([sys.executable, "-c", self.CHILD], cwd=root / "collector", env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_service_starts_and_serves_with_the_component_broken(self):
        out = self.child()
        [c] = [c for c in out["core"]["components"] if c["name"] == "project_management"]
        self.assertEqual(c["error"], "RuntimeError: boom")
        self.assertEqual((out["overview"], out["jarvis"]), (200, 200))
        self.assertEqual(out["index_error"], "RuntimeError: boom")


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


class OldWordTest(unittest.TestCase):
    """Check 5 of the core spec: the old word remains only where "Names during the move" allows."""
    WORD = "work" + "space"
    ALLOWED = {"collector/components/project_management/old_names.py", "tests/test_old_names.py", "CONTEXT.md",
               "tests/same_output_expected.json"}   # the recorded output of commit dfb604b, which still had the old word

    def test_the_old_word_remains_only_in_the_alias_layer_and_the_documents(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertGreater(len(tracked), 50)
        found = []
        for name in tracked:
            if name.startswith("docs/") or name in self.ALLOWED:
                continue
            if self.WORD in name.lower():
                found.append(name)
                continue
            try:
                text = (REPO / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            found += [f"{name}:{i}" for i, line in enumerate(text.splitlines(), 1)
                      if self.WORD in line.lower().replace("nexus-" + self.WORD, "")]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
