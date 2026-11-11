"""Tests of the project_management component (collector/components/project_management).

  python3 -m unittest tests.test_project_management
"""
import ast
import http.client
import json
import os
import shutil
import sqlite3
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
from core.util import short_path
from core.app import Core

COMPS = REPO / "collector" / "components"
PM = component.import_folder(COMPS / "project_management")
W = PM.centre
CFG = {"projects": [], "claude_bin": "/no/claude"}
COLLECTOR = {"collector": {"version": "t", "probe": False, "reels": None, "project_notes": []}}


class ProjectManagementTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"STELLAXIS_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        env.start()
        self.addCleanup(env.stop)
        self.app = self.home / "code" / "app"
        self.app.mkdir(parents=True)
        t = self.home / ".claude" / "projects" / "p" / "s1.jsonl"
        t.parent.mkdir(parents=True)
        t.write_text(json.dumps({"type": "user", "sessionId": "s1", "cwd": str(self.app), "gitBranch": "main",
                                 "timestamp": "2026-11-01T00:00:00Z", "message": {"role": "user", "content": "hi"}}) + "\n")
        self.core = Core(self.home, self.home / ".claude", COMPS)

    def collect(self, cfg=CFG, core=None):
        """One collection pass on the fake home, with the CLI and the desktop app's records patched."""
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            return (core or self.core).run_pass({**cfg, "scan_roots": []}, COLLECTOR)

    def index(self, home=None):
        return json.loads(((home or self.home) / "projects.json").read_text())

    def get(self, srv, path):
        with closing(http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)) as conn:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read()

    def test_the_component_loads(self):
        [c] = [c for c in self.core.components if c.name == "project_management"]
        self.assertEqual((c.error, len(c.readers), c.handle is not None), (None, 2, True))
        self.assertEqual(sorted(c.offers), ["approvals", "index", "live", "projects", "repos", "sessions"])
        self.assertEqual(sorted(c.actions), ["new_names", "relocate"])
        self.assertEqual([tuple(r) for r in c.reads], [("claude_management", "sessions")])

    def test_nothing_outside_the_core_imports_the_component(self):
        self.assertFalse((REPO / "collector" / ("work" + "spaces.py")).exists())
        for name in ("collect.py", "unplaced/jarvis.py", "prune.py", "mcp_server.py"):
            src = (REPO / "collector" / name).read_text()
            self.assertNotIn("import " + "work" + "spaces", src, name)
            self.assertNotIn("project_management import", src, name)
            self.assertNotIn("claude_management import", src, name)

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

    def test_a_collection_writes_the_index_from_the_transcripts(self):
        res = self.collect()
        self.assertEqual({name: c["errors"] for name, c in res["components"].items()},
                         {name: [] for name in res["components"]})
        idx = self.index()
        self.assertEqual([w["id"] for w in idx["projects"]], ["unassigned"])
        [repo] = idx["projects"][0]["repos"]
        self.assertEqual(repo["path"], short_path(self.app))
        [branch] = repo["branches"]
        self.assertEqual((branch["name"], [s["id"] for s in branch["sessions"]]), ("main", ["s1"]))

    def test_the_index_built_from_the_rows_is_the_index_built_in_memory(self):
        a, b, lone = self.home / "a", self.home / "b", self.home / "lone"
        for d in (a, b, lone):
            d.mkdir()
        cfg = {"projects": [{"id": "pa", "name": "A", "repos": [str(a), str(lone)]}, {"id": "pb", "repos": [str(b)]}]}
        wt = f"{a}/.claude/worktrees/w1"
        def s(sid, project, first, last, cost, branch):
            return {"id": sid, "project": project, "first_ts": first, "last_ts": last, "cost": cost, "branch": branch}
        sessions = [s("s1", str(a), "2026-11-10T08:00:00Z", "2026-11-10T10:00:00Z", 0.1, "main"),
                    s("s2", str(a), "2026-11-10T08:00:00Z", "2026-11-10T10:00:00Z", 0.2, "feature"),
                    s("s3", wt, "2026-11-09T08:00:00Z", "2026-11-09T09:00:00Z", 0.3, "w1"),
                    s("s4", str(a), "2026-11-10T08:00:00Z", "2026-11-10T10:00:00Z", 0, "main"),
                    s("s5", str(b), "2026-11-08T08:00:00Z", "2026-11-08T09:00:00Z", 1.005, None),
                    s("s6", str(b), "2026-09-01T08:00:00Z", "2026-09-01T09:00:00Z", 7.25, "old"),
                    s("s7", "~/nowhere/x", None, None, 0.7, "main"),
                    s("s8", "(unknown)", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z", 2.5, None)]
        folders = [{"path": str(a), "abs": str(a), "worktree_of": None}, {"path": wt, "abs": wt, "worktree_of": str(a)},
                   {"path": str(b), "abs": str(b), "worktree_of": None}]
        app = {"s2": {"app_id": "app-2", "title": "Archived one", "branch": None, "archived": True},
               "s5": {"app_id": "app-5", "title": "A title", "branch": "from-app", "archived": False}}
        index = W.build_index(sessions, folders, app, cfg, now="2026-11-11T12:00:00Z")
        ctx = self.core.ctx("project_management", {})
        PM.records.save_index(ctx, index, {r["id"]: i for i, r in enumerate(sessions)})
        costs = {r["id"]: r["cost"] for r in sessions}
        self.assertEqual(json.dumps(PM.records.index(ctx, costs)), json.dumps(index))
        self.assertEqual([[r["path"] for r in p["repos"]] for p in index["projects"]], [[str(a), str(lone)], [str(b)], ["~/nowhere/x", "(unknown)"]])

    def test_the_repos_and_sessions_offers(self):
        self.collect()
        [row] = self.core.read("project_management", "repos", CFG)
        self.assertEqual(row, {"path": short_path(self.app), "abs": str(self.app), "raw": str(self.app), "has_git": False,
                               "worktree_of": None})
        self.assertEqual(self.core.read("project_management", "sessions", CFG),
                         [{"id": "s1", "project": "unassigned", "repo": short_path(self.app), "branch": "main",
                           "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z"}])

    def test_a_repeated_project_id_or_the_id_unassigned_still_gives_the_index(self):
        for projects in ([{"id": "p", "name": "P", "repos": [str(self.app)]}, {"id": "p", "name": "P2", "repos": [str(self.app)]}],
                         [{"id": "unassigned", "name": "U", "repos": [str(self.app)]}]):
            with self.subTest(projects=projects):
                cfg = {**CFG, "projects": projects}
                built, real = [], W.build_index
                with mock.patch.object(W, "build_index", side_effect=lambda *a, **k: built.append(real(*a, **k)) or built[-1]):
                    res = self.collect(cfg)
                self.assertEqual(res["components"]["project_management"]["errors"], [])
                [index] = built
                self.assertEqual(json.dumps(self.index()), json.dumps(index))
                costs = {r["id"]: r["cost"] for r in self.core.read("claude_management", "sessions", cfg)}
                ctx = self.core.ctx("project_management", {})
                self.assertEqual(json.dumps(PM.records.index(ctx, costs)), json.dumps(index))
                self.assertEqual(sum(1 for p in index["projects"] for r in p["repos"] for b in r["branches"] for x in b["sessions"]), 2)
                self.assertEqual([(r["id"], r["project"]) for r in self.core.read("project_management", "sessions", cfg)],
                                 [("s1", projects[0]["id"])])

    def test_a_store_that_fails_still_gets_the_index_files(self):
        with mock.patch.object(PM.records, "save_index", side_effect=sqlite3.OperationalError("database is locked")), \
                mock.patch.object(PM.records, "index") as index:
            res = self.collect()
        index.assert_not_called()
        self.assertEqual(res["components"]["project_management"]["errors"], ["OperationalError: database is locked"])
        self.assertEqual([s["id"] for p in self.index()["projects"] for r in p["repos"] for b in r["branches"] for s in b["sessions"]], ["s1"])
        self.assertTrue((self.home / ("work" + "spaces.json")).is_file())
        for f in (self.home / "projects.json", self.home / ("work" + "spaces.json")):
            f.unlink()
        app = {"s1": {"app_id": "a1", "title": "\ud800", "branch": None, "archived": False}}
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value=app):
            res = self.core.run_pass({**CFG, "scan_roots": []}, COLLECTOR)
        [err] = res["components"]["project_management"]["errors"]
        self.assertTrue(err.startswith("UnicodeEncodeError"), err)
        [s] = self.index()["projects"][0]["repos"][0]["branches"][0]["sessions"]
        self.assertEqual((s["id"], s["title"]), ("s1", "\ud800"))
        self.assertTrue((self.home / ("work" + "spaces.json")).is_file())

    def test_with_the_store_down_the_index_is_still_written(self):
        home = self.home / "broken"
        (home / "stellaxis.db").mkdir(parents=True)
        core = Core(home, self.home / ".claude", COMPS)
        self.assertTrue(core.error)
        res = self.collect(core=core)
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        idx = self.index(home)
        self.assertEqual([s["id"] for p in idx["projects"] for r in p["repos"] for b in r["branches"] for s in b["sessions"]], ["s1"])

    def test_without_claude_management_the_cost_is_zero(self):
        [cm] = [c for c in self.core.components if c.name == "claude_management"]
        def boom(ctx, **args):
            raise RuntimeError("gone")
        with mock.patch.dict(cm.offers, {"sessions": boom}):
            res = self.collect()
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        [s] = self.index()["projects"][0]["repos"][0]["branches"][0]["sessions"]
        self.assertEqual((s["id"], s["cost"]), ("s1", 0))

    def test_notes_cards_are_found_under_either_key(self):
        card = {"name": "Reel Brain", "summary": "s"}
        self.assertEqual([n["name"] for n in C.project_notes({"projects": [card], "project_notes": []})], ["Reel Brain"])
        self.assertEqual([n["name"] for n in C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": [card]})], ["Reel Brain"])
        self.assertEqual(C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": []}), [])
        self.assertNotIn("work" + "spaces", C.DEFAULT_CONFIG)

    def test_a_pass_that_is_not_a_collection_leaves_the_index_alone(self):
        res = self.core.run_pass(dict(CFG))
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        self.assertFalse((self.home / "projects.json").exists())

    def test_the_reader_writes_to_the_cores_home_not_the_environments(self):
        elsewhere = self.home / "elsewhere"
        core = Core(elsewhere, self.home / ".claude", COMPS)
        res = self.collect(core=core)
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

    def test_a_pass_reports_a_reader_error_and_a_load_error(self):
        cfg = {**CFG, "scan_roots": []}
        res = self.collect()
        self.assertIsNone(C.pass_error(self.core, res))
        with mock.patch.object(W, "live", side_effect=ValueError("boom")), mock.patch.object(W, "app_records", return_value={}):
            res = self.core.run_pass(cfg, COLLECTOR)
        self.assertEqual(res["components"]["project_management"]["errors"], ["ValueError: boom"])
        self.assertEqual(C.pass_error(self.core, res), "project_management: ValueError: boom")
        [c] = [c for c in self.core.components if c.name == "project_management"]
        with mock.patch.object(c, "error", "ImportError: gone"):
            res = self.collect()
            self.assertNotIn("project_management", res["components"])
            self.assertEqual(C.pass_error(self.core, res), "project_management: ImportError: gone")
        core = Core(self.home, self.home / ".claude", self.home / "no-components")
        self.assertIsNone(C.pass_error(core, core.run_pass(cfg, COLLECTOR)))


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
out = {"core": json.loads(get("/api/core")[1]), "overview": get("/overview")[0], "jarvis": get("/api/jarvis")[0]}
collect.collect({"scan_roots": []})
out["index_error"] = json.loads((collect.STX_HOME / "status.json").read_text())["index_error"]
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
        self.assertEqual(out["index_error"], "project_management: RuntimeError: boom")


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
