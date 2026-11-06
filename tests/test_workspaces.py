"""Workspace assignment: which configured project a folder belongs to.

  python3 -m unittest discover -s tests -k test_workspaces
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import workspaces as W  # noqa: E402

WS = [{"id": "ph", "name": "PartHub", "folders": ["~/code/ph-admin", "~/code/ph-admin-ui"]}]

PROJECTS = [{"path": "~/code/x", "abs": "/nonexistent/code/x", "worktree_of": None},
            {"path": "~/code/x/.claude/worktrees/w", "abs": "/nonexistent/code/x/.claude/worktrees/w",
             "worktree_of": "~/code/x"}]
SESSIONS = [
    {"id": "old", "project": "~/code/x", "branch": "main", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T01:00:00Z", "cost": 1.0},
    {"id": "new", "project": "~/code/x", "branch": "main", "first_ts": "2026-11-03T00:00:00Z", "last_ts": "2026-11-03T01:00:00Z", "cost": 2.0},
    {"id": "wt", "project": "~/code/x/.claude/worktrees/w", "branch": "feat", "first_ts": "2026-11-05T00:00:00Z", "last_ts": "2026-11-05T01:00:00Z", "cost": 3.0},
]
GONE = [{"id": "gone", "project": "~/code/x/deleted", "branch": None, "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T01:00:00Z", "cost": 0.5}]
CFG = {"workspaces": [{"id": "x", "name": "X", "folders": ["~/code/x"]}]}


class IndexTest(unittest.TestCase):
    def test_groups_repo_branch_newest_first(self):
        i = W.build_index(SESSIONS, PROJECTS, {}, CFG)
        r = i["workspaces"][0]["repos"][0]
        self.assertEqual([b["name"] for b in r["branches"]], ["feat", "main"])
        self.assertEqual([s["id"] for s in r["branches"][1]["sessions"]], ["new", "old"])

    def test_worktree_session_joins_parent_repo(self):
        self.assertEqual(len(W.build_index(SESSIONS, PROJECTS, {}, CFG)["workspaces"][0]["repos"]), 1)

    def test_session_in_deleted_folder_stays_listed(self):
        r = W.build_index(GONE, PROJECTS, {}, CFG)["workspaces"][0]["repos"][0]
        self.assertFalse(r["exists"])
        self.assertEqual(r["branches"][0]["name"], "(no branch)")
        self.assertEqual(r["branches"][0]["sessions"][0]["id"], "gone")

    def test_app_title_is_joined_and_other_is_last(self):
        i = W.build_index(SESSIONS, PROJECTS, {"new": {"title": "T", "app_id": "local_1", "branch": "app-b"}}, CFG)
        self.assertEqual(i["workspaces"][-1]["id"], "other")
        self.assertEqual(i["workspaces"][-1]["name"], "Other")
        s = next(s for b in i["workspaces"][0]["repos"][0]["branches"] for s in b["sessions"] if s["id"] == "new")
        self.assertEqual((s["title"], s["app_id"]), ("T", "local_1"))
        self.assertIn("app-b", [b["name"] for b in i["workspaces"][0]["repos"][0]["branches"]])

    def test_cost_30d_counts_recent_sessions_only(self):
        now = "2026-11-06T00:00:00Z"
        i = W.build_index(SESSIONS + [{**SESSIONS[0], "id": "ancient", "last_ts": "2026-08-01T00:00:00Z", "cost": 50}],
                          PROJECTS, {}, CFG, now=now)
        self.assertEqual(i["workspaces"][0]["cost_30d"], 6.0)


class AssignTest(unittest.TestCase):
    def test_worktree_belongs_to_its_repo(self):
        self.assertEqual(W.assign("~/code/ph-admin/.claude/worktrees/x", WS), "ph")

    def test_name_prefix_is_not_a_match(self):
        self.assertEqual(W.assign("~/code/ph-admin-old", WS), "other")

    def test_absolute_and_tilde_paths_match(self):
        self.assertEqual(W.assign(os.path.expanduser("~/code/ph-admin-ui"), WS), "ph")


class AppRecordsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def write(self, rel, data):
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))

    def test_reads_app_record(self):
        self.write("a/b/local_1.json", {"sessionId": "local_1", "cliSessionId": "abc", "title": "T",
                                        "branch": "b", "sourceBranch": "main", "createdAt": 5,
                                        "isArchived": True})
        self.assertEqual(W.app_records(self.base)["abc"], {
            "app_id": "local_1", "title": "T", "branch": "b", "source_branch": "main",
            "created_ms": 5, "archived": True})

    def test_bad_json_and_missing_keys_are_skipped(self):
        (self.base / "bad.json").write_text("{")
        self.write("x.json", {"sessionId": "local_2"})
        self.write("y.json", ["not", "a", "record"])
        self.assertEqual(W.app_records(self.base), {})

    def test_missing_folder_is_empty(self):
        self.assertEqual(W.app_records(self.base / "nope"), {})


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fake(self, output: str) -> str:
        f = Path(self.tmp.name) / "claude"
        f.write_text("#!/bin/sh\ncat <<'EOF'\n" + output + "\nEOF\n")
        f.chmod(0o755)
        return str(f)

    def test_live_reads_fake_cli(self):
        self.assertEqual(W.live(self.fake('[{"sessionId":"a","status":"busy"}]'))[0]["sessionId"], "a")

    def test_live_is_none_when_binary_missing_or_output_bad(self):
        self.assertIsNone(W.live("/no/such/claude"))
        self.assertIsNone(W.live(self.fake("not json")))
        self.assertIsNone(W.live(self.fake('{"not":"a list"}')))

    def test_status_rules(self):
        self.assertEqual(W.status_of({"state": "blocked"}), "waiting")
        self.assertEqual(W.status_of({"status": "waiting"}), "waiting")
        self.assertEqual(W.status_of({"status": "busy"}), "running")
        self.assertEqual(W.status_of({"status": "idle", "state": "working"}), "open")
        self.assertEqual(W.status_of(None), "closed")


class ReadApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        cls.old = os.environ.get("STELLAXIS_HOME")
        os.environ["STELLAXIS_HOME"] = str(cls.home)
        cls.cfg = {"claude_bin": "/no/such/claude"}
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not W.handle(self, "GET", cls.cfg):
                    self.send_error(404)

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        if cls.old is None:
            os.environ.pop("STELLAXIS_HOME", None)
        else:
            os.environ["STELLAXIS_HOME"] = cls.old
        cls.tmp.cleanup()

    def get(self, path):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", path)
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if r.status == 200 else None)

    def test_missing_index_is_empty_list(self):
        (self.home / "workspaces.json").unlink(missing_ok=True)
        code, body = self.get("/api/workspaces")
        self.assertEqual((code, body["workspaces"], body["live_ok"]), (200, [], False))

    def test_binary_missing_gives_null_status(self):
        (self.home / "workspaces.json").write_text(json.dumps({"workspaces": [{"id": "x", "repos": [
            {"path": "p", "branches": [{"name": "b", "sessions": [{"id": "s1"}]}]}]}]}))
        code, body = self.get("/api/workspaces")
        s = body["workspaces"][0]["repos"][0]["branches"][0]["sessions"][0]
        self.assertEqual((code, body["live_ok"], s["status"]), (200, False, None))

    def test_other_paths_are_not_ours(self):
        self.assertEqual(self.get("/api/other")[0], 404)


if __name__ == "__main__":
    unittest.main()
