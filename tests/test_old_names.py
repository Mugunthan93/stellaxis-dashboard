"""Tests of the alias layer (collector/components/project_management/old_names.py): the names from before
2026-11-11 (workspace, other) still answer as they did.

  python3 -m unittest tests.test_old_names
"""
import http.client
import http.server
import json
import os
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
sys.path.insert(0, str(REPO / "tests"))
from core import server
from core.app import Core
from core.component import import_folder

import test_hardening

C = test_hardening.C

COMPS = REPO / "collector" / "components"
PM = import_folder(COMPS / "project_management")
W = PM.centre
O = PM.old_names
OLD_CFG = {"claude_bin": "/no/claude", "workspaces": [{"id": "x", "name": "X", "folders": ["~/code/x"], "routines": ["r"]}],
           "projects": [{"name": "A card"}]}


class NamesTest(unittest.TestCase):
    def test_old_config_keys_are_read_as_the_new_ones(self):
        cfg = O.config(OLD_CFG)
        self.assertEqual(cfg["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "routines": ["r"]}])
        self.assertEqual(OLD_CFG["workspaces"][0]["folders"], ["~/code/x"])          # the argument is not changed
        new = {"projects": [{"id": "y", "repos": ["~/code/y"]}], "project_notes": []}
        self.assertIs(O.config(new), new)

    def test_old_paths_bodies_and_pages(self):
        self.assertEqual(O.path("/api/workspaces"), "/api/projects")
        self.assertEqual(O.path("/api/workspaces/other/tasks"), "/api/projects/unassigned/tasks")
        self.assertEqual(O.path("/api/workspaces/x/tests/run"), "/api/projects/x/tests/run")
        self.assertIsNone(O.path("/api/projects/x/tasks"))
        self.assertIsNone(O.path("/api/workspacesx"))
        self.assertEqual(O.body({"workspace": "other", "folder": "f"}), {"project": "unassigned", "folder": "f"})
        self.assertEqual(O.body({"project": "x"}), {"project": "x"})
        self.assertEqual(O.page("/workspace", "id=other&x=1"), "/project?id=unassigned&x=1")
        self.assertEqual(O.page("/workspace", "id=a%20b"), "/project?id=a%20b")
        self.assertEqual(O.page("/workspace", ""), "/project")
        self.assertEqual(O.page("/workspace.html", ""), "/project.html")
        self.assertIsNone(O.page("/project", "id=x"))

    def test_old_answers_keep_their_shape(self):
        new = {"generated_at": "g", "live_ok": True, "projects": [{"id": "x", "name": "X", "repos": []}, {"id": "unassigned", "name": "Unassigned", "repos": []}]}
        self.assertEqual(O.answer(new), {"generated_at": "g", "live_ok": True, "workspaces": [
            {"id": "x", "name": "X", "repos": []}, {"id": "other", "name": "Other", "repos": []}]})
        self.assertEqual(new["projects"][1]["id"], "unassigned")

    def test_the_rename_of_the_config_content(self):
        old = {"interval": "1m", "workspaces": [{"id": "x", "name": "X", "folders": ["~/code/x"], "tests": {"~/code/x": ["true"]}}, {"id": "y", "folders": []}],
               "projects": [{"name": "A card"}], "port": 7777}
        new = O.to_new(old)
        self.assertEqual(list(new), ["interval", "projects", "project_notes", "port"])
        self.assertEqual(new["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "tests": {"~/code/x": ["true"]}}, {"id": "y", "repos": []}])
        self.assertEqual(new["project_notes"], [{"name": "A card"}])
        self.assertEqual(O.to_old(new), old)
        self.assertEqual(list(O.to_old(new)), list(old))
        self.assertEqual(O.changes(old), ['config.json: "workspaces" becomes "projects" (2 entries)',
                                          'config.json: "folders" becomes "repos" in 2 entries',
                                          'config.json: "projects" becomes "project_notes" (1 notes cards)'])
        self.assertEqual(O.changes(new), [])
        self.assertEqual(O.to_new(new), new)
        self.assertEqual(O.changes({"projects": [{"name": "A card"}]}), ['config.json: "projects" becomes "project_notes" (1 notes cards)'])
        self.assertEqual(O.to_new({"projects": [{"name": "A card"}]}), {"projects": [], "project_notes": [{"name": "A card"}]})
        self.assertEqual(O.changes({}), [])


class ComponentTest(unittest.TestCase):
    """The component under the old config shape, through the core's own server."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"STELLAXIS_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                                           "HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.core = Core(self.home, self.home / ".claude", COMPS)
        self.srv = server.serve(self.core, 0, dict(OLD_CFG))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)) as conn:
            conn.request(method, path, None if body is None else json.dumps(body),
                         {} if body is None else {"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.getheader("Content-Type") == "application/json" else None), r.headers

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def run_pass(self):
        row = {"id": "s1", "project": "~/code/x", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z", "cost": 1, "branch": "main"}
        stray = {**row, "id": "s2", "project": "~/nowhere/y"}
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            return self.core.run_pass(dict(OLD_CFG), {"sessions": [row, stray], "folders": []})

    def test_a_pass_with_an_old_config_writes_both_index_files(self):
        res = self.run_pass()
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        new = json.loads((self.home / "projects.json").read_text())
        old = json.loads((self.home / "workspaces.json").read_text())
        self.assertEqual([p["id"] for p in new["projects"]], ["x", "unassigned"])
        self.assertEqual([(w["id"], w["name"]) for w in old["workspaces"]], [("x", "X"), ("other", "Other")])
        self.assertEqual(old["workspaces"][0]["repos"], new["projects"][0]["repos"])

    def test_the_old_index_file_is_read_until_the_first_pass(self):
        cfg = dict(OLD_CFG)
        self.assertEqual((self.get("/api/projects")[1]["projects"], self.get("/api/workspaces")[1]["workspaces"]), ([], []))
        (self.home / "workspaces.json").write_text(json.dumps({"generated_at": "g", "workspaces": [
            {"id": "x", "name": "X", "repos": [{"path": "~/code/x"}]}, {"id": "other", "name": "Other", "repos": [{"path": "~/y"}]}]}))
        self.assertEqual([p["id"] for p in self.get("/api/projects")[1]["projects"]], ["x", "unassigned"])
        self.assertEqual([p["id"] for p in self.get("/api/workspaces")[1]["workspaces"]], ["x", "other"])
        self.assertEqual(len(self.core.read("project_management", "index", cfg)), 2)
        self.assertFalse((self.home / "projects.json").exists())
        for bad in ("not json", "[]", json.dumps({"workspaces": "x"})):
            (self.home / "workspaces.json").write_text(bad)
            self.assertEqual(self.get("/api/projects")[1]["projects"], [])

    def test_old_routes_answer_as_before(self):
        self.run_pass()
        status, body, _ = self.get("/api/workspaces")
        self.assertEqual((status, [w["id"] for w in body["workspaces"]], "projects" in body), (200, ["x", "other"], False))
        self.assertEqual(self.get("/api/projects")[1]["projects"][1]["id"], "unassigned")
        self.assertEqual(self.post("/api/workspaces/other/tasks", {"add": "one"})[0], 200)
        self.assertEqual([t["text"] for t in self.get("/api/projects/unassigned/tasks")[1]], ["one"])
        self.assertEqual([t["text"] for t in self.get("/api/workspaces/other/tasks")[1]], ["one"])
        self.assertEqual(self.get("/api/workspaces/x/work")[0], 200)
        self.assertEqual(self.get("/api/workspaces/nope/tasks")[0], 404)
        self.assertEqual(self.get("/api/projects/other/tasks")[0], 404)          # the old id is an old name only

    def test_the_old_page_address_redirects(self):
        status, _, headers = self.get("/workspace?id=other")
        self.assertEqual((status, headers["Location"], headers["Content-Length"]), (302, "/project?id=unassigned", "0"))

    def test_the_old_task_file_is_used_until_it_is_moved(self):
        (self.home / "tasks").mkdir()
        (self.home / "tasks" / "other.md").write_text("- [ ] kept\n")
        self.assertEqual([t["text"] for t in W.read_tasks("unassigned")], ["kept"])
        W.write_task("unassigned", {"add": "more"})
        self.assertEqual((self.home / "tasks" / "other.md").read_text(), "- [ ] kept\n- [ ] more\n")
        self.assertFalse((self.home / "tasks" / "unassigned.md").exists())
        (self.home / "tasks" / "other.md").rename(self.home / "tasks" / "unassigned.md")
        self.assertEqual(len(W.read_tasks("unassigned")), 2)

    def test_a_new_session_takes_the_old_body_key(self):
        repo = self.home / "code" / "x"
        repo.mkdir(parents=True)
        (self.home / "projects.json").write_text(json.dumps({"projects": [{"id": "x", "name": "X", "repos": [
            {"path": str(repo), "abs": str(repo), "exists": True, "branches": []}]}]}))
        with mock.patch.object(W, "start", return_value={"id": "n"}):
            self.assertEqual(self.post("/api/sessions/new", {"workspace": "x", "folder": str(repo), "prompt": "hi"})[:2], (200, {"id": "n"}))
            self.assertEqual(self.post("/api/sessions/new", {"project": "x", "folder": str(repo), "prompt": "hi"})[:2], (200, {"id": "n"}))


class OldRoutes(test_hardening.RouteMatrix):
    """The same matrix under the old names: every old route answers our Host and refuses any other."""
    def config(self):
        new = super().config()
        return {**{k: v for k, v in new.items() if k != "projects"},
                "workspaces": [{**{k: v for k, v in p.items() if k != "repos"}, "folders": p["repos"]} for p in new["projects"]]}

    def routes(self):
        return [("GET", "/api/workspaces", None, 200), ("GET", "/api/workspaces/w/tasks", None, 200),
                ("GET", "/api/workspaces/other/tasks", None, 200), ("GET", "/api/workspaces/w/work", None, 200),
                ("GET", "/workspace?id=w", None, 302), ("GET", "/workspace.html", None, 302),
                ("POST", "/api/workspaces/w/tasks", {"add": "x"}, 200),
                ("POST", "/api/workspaces/w/tests/run", {"repo": self.repo}, 200),
                ("POST", "/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": "hi"}, 200)]


ROW = {"id": "s1", "project": "~/code/x", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z", "cost": 1, "branch": "main"}


class RenameTest(unittest.TestCase):
    """The action new_names through the real Handler, on a fake home; prune.py runs as a child process."""
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(os.path.realpath(t.name))
        self.home = self.root / "stx"
        claude = self.home / ".claude"
        (self.home / "tasks").mkdir(parents=True)
        claude.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        test_hardening.fake_home(self, self.home, claude)
        for patch in (mock.patch.object(C.settings_api, "STX_HOME", self.home), mock.patch.object(C.settings_api, "CLAUDE_DIR", claude),
                      mock.patch.object(C, "collect")):
            patch.start()
            self.addCleanup(patch.stop)
        self.cfg_file = self.home / "config.json"
        self.cfg_file.write_text(json.dumps({**OLD_CFG, "projects": [{"name": "A card", "summary": "\u00e9 \u2014 ok"}], "interval": "1m"}, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.home / "tasks" / "other.md").write_text("- [ ] kept\n")
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=20)) as conn:
            conn.request(method, path, None if body is None else json.dumps(body),
                         {} if body is None else {"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.getheader("Content-Type") == "application/json" else None), r.headers

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def listed(self):
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            self.assertIsNone(C.write_index(C.load_config(), {"sessions": [ROW], "projects": []}))
        return [p["id"] for p in self.get("/api/projects")[1]["projects"]]

    def prune(self, *args):
        env = {**os.environ, "HOME": str(self.root), "STELLAXIS_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}
        r = subprocess.run([sys.executable, str(REPO / "collector" / "prune.py"), *args], env=env, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise AssertionError(f"prune {args} failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def test_the_dry_run_shows_old_and_new_and_changes_nothing(self):
        before = self.cfg_file.read_bytes()
        status, body, _ = self.get("/api/projects/new-names")
        self.assertEqual((status, body["applied"], body["ok"], body["manifest"]), (200, False, True, None))
        self.assertEqual(body["steps"][:3], ['config.json: "workspaces" becomes "projects" (1 entries)',
                                             'config.json: "folders" becomes "repos" in 1 entries',
                                             'config.json: "projects" becomes "project_notes" (1 notes cards)'])
        self.assertTrue(body["steps"][3].startswith("move ") and body["steps"][3].endswith("unassigned.md"))
        self.assertEqual(self.post("/api/projects/new-names", {"apply": False})[1]["applied"], False)
        self.assertEqual(self.post("/api/projects/new-names", {"apply": "yes"})[0], 400)
        self.assertEqual(self.post("/api/projects/new-names", {})[0], 400)
        self.assertEqual(self.cfg_file.read_bytes(), before)
        self.assertTrue((self.home / "tasks" / "other.md").exists())
        self.assertFalse((self.home / ".claude" / "_archive").exists())

    def test_apply_renames_backs_up_and_the_service_reads_the_same_projects(self):
        before = self.cfg_file.read_bytes()
        seen = C.load_config()
        listed = self.listed()
        self.assertEqual(listed, ["x", "unassigned"])
        status, body, _ = self.post("/api/projects/new-names", {"apply": True})
        self.assertEqual((status, body["applied"], body["ok"]), (200, True, True))
        user = json.loads(self.cfg_file.read_text())
        self.assertEqual(user["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "routines": ["r"]}])
        self.assertEqual(user["project_notes"], [{"name": "A card", "summary": "\u00e9 \u2014 ok"}])
        self.assertIn("\u00e9 \u2014 ok", self.cfg_file.read_text(encoding="utf-8"))
        self.assertNotIn("\\u", self.cfg_file.read_text(encoding="utf-8"))
        self.assertNotIn("workspaces", user)
        self.assertIn(self.home.parent, Path(body["manifest"]).parents)          # under the temp folder, never the real ~/.claude
        man = json.loads(Path(body["manifest"]).read_text())
        self.assertEqual([e["op"] for e in man["entries"]], ["json_backup", "project_management.old_config_keys", "move"])
        self.assertEqual(Path(man["entries"][0]["backup"]).read_bytes(), before)
        self.assertEqual((self.home / "tasks" / "unassigned.md").read_text(), "- [ ] kept\n")
        self.assertEqual(self.listed(), listed)                      # the new keys are read as the old ones were
        self.assertEqual(C.project_notes(C.load_config()), C.project_notes(seen))
        self.assertEqual([n["name"] for n in C.project_notes(seen)], ["A card"])
        [row] = [json.loads(l) for l in (self.home / "actions.jsonl").read_text().splitlines()]
        self.assertEqual((row["component"], row["action"], row["ok"]), ("project_management", "new_names", True))
        again = self.post("/api/projects/new-names", {"apply": True})[1]
        self.assertEqual((again["steps"], again["manifest"]), (["nothing to rename: the new names are already in use"], None))

    def test_a_task_file_that_is_already_there_is_never_overwritten(self):
        (self.home / "tasks" / "unassigned.md").write_text("- [ ] new\n")
        body = self.post("/api/projects/new-names", {"apply": True})[1]
        self.assertEqual((self.home / "tasks" / "other.md").read_text(), "- [ ] kept\n")
        self.assertEqual((self.home / "tasks" / "unassigned.md").read_text(), "- [ ] new\n")
        man = json.loads(Path(body["manifest"]).read_text())
        self.assertNotIn("move", [e["op"] for e in man["entries"]])
        self.assertFalse(any(s.startswith("move ") for s in body["steps"]))
        self.assertIn("tasks/other.md is left where it is: tasks/unassigned.md already exists", [s.replace(str(self.home) + "/", "") for s in body["steps"]])

    def test_prune_restore_undoes_it_and_keeps_a_later_edit(self):
        name = Path(self.post("/api/projects/new-names", {"apply": True})[1]["manifest"]).parent.name
        user = json.loads(self.cfg_file.read_text())
        self.cfg_file.write_text(json.dumps({**user, "interval": "5m"}))
        out = self.prune("restore", name, "--apply")       # prune.py as a child process, on the fake home
        self.assertIn("put the old keys back in", out)
        back = json.loads(self.cfg_file.read_text())
        self.assertEqual((back["workspaces"], back["interval"]), (OLD_CFG["workspaces"], "5m"))
        self.assertEqual(back["projects"][0]["summary"], "\u00e9 \u2014 ok")
        self.assertIn("\u00e9 \u2014 ok", self.cfg_file.read_text(encoding="utf-8"))
        self.assertNotIn("\\u", self.cfg_file.read_text(encoding="utf-8"))
        self.assertNotIn("project_notes", back)
        self.assertEqual((self.home / "tasks" / "other.md").read_text(), "- [ ] kept\n")
        self.assertFalse((self.home / "tasks" / "unassigned.md").exists())

    def test_restore_stops_when_the_config_is_gone(self):
        man = Path(self.post("/api/projects/new-names", {"apply": True})[1]["manifest"])
        self.cfg_file.unlink()
        env = {**os.environ, "HOME": str(self.root), "STELLAXIS_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}
        r = subprocess.run([sys.executable, str(REPO / "collector" / "prune.py"), "restore", man.parent.name, "--apply"], env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("config.json is missing or is not JSON: the old keys were not put back", r.stdout)
        self.assertTrue(man.exists())
        self.assertFalse((man.parent / "manifest.restored.json").exists())


if __name__ == "__main__":
    unittest.main()
