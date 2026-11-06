"""Workspace assignment: which configured project a folder belongs to.

  python3 -m unittest discover -s tests -k test_workspaces
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_session_in_deleted_worktree_joins_parent_repo(self):
        with tempfile.TemporaryDirectory() as t:
            dead = t + "/.claude/worktrees/dead"
            cfg = {"workspaces": [{"id": "x", "name": "X", "folders": [t]}]}
            sess = [{"id": "d", "project": dead, "branch": "b", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T01:00:00Z", "cost": 1}]
            repos = W.build_index(sess, [{"path": dead, "abs": dead, "worktree_of": None}], {}, cfg)["workspaces"][0]["repos"]
            self.assertEqual([r["path"] for r in repos], [t])
            self.assertTrue(repos[0]["exists"])
            self.assertEqual(repos[0]["branches"][0]["sessions"][0]["id"], "d")

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


class RepoStateTest(unittest.TestCase):
    def git(self, path, *args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    def repo(self, name):
        d = Path(self.tmp.name) / name
        d.mkdir()
        self.git(d, "init", "-q", "-b", "main")
        self.git(d, "config", "user.name", "t")
        self.git(d, "config", "user.email", "t@example.com")
        return d

    def commit(self, d, name):
        (d / name).write_text(name)
        self.git(d, "add", name)
        self.git(d, "commit", "-q", "-m", name)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        W._STATE_CACHE.clear()

    def test_git_runs_without_optional_locks(self):
        d = self.repo("a")
        real = subprocess.run
        with mock.patch.object(W.subprocess, "run", wraps=real) as run:
            W.repo_state(str(d))
        self.assertTrue(run.call_args_list)
        for c in run.call_args_list:
            self.assertEqual(c.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_repo_states_caches_errors_too_until_cleared(self):
        gone = str(Path(self.tmp.name) / "nope")
        with mock.patch.object(W, "repo_state", wraps=W.repo_state) as rs:
            a = W.repo_states([gone, gone])
            W.repo_states([gone])
            self.assertEqual(rs.call_count, 1)
            W._STATE_CACHE.clear()
            W.repo_states([gone])
            self.assertEqual(rs.call_count, 2)
        self.assertIn("error", a[gone])

    def test_no_remote(self):
        d = self.repo("a")
        self.commit(d, "f")
        self.assertEqual(W.repo_state(str(d)), {"remote": False, "ahead": 0, "dirty": 0})

    def test_uncommitted_file_is_dirty(self):
        d = self.repo("a")
        self.commit(d, "f")
        (d / "new").write_text("x")
        self.assertEqual(W.repo_state(str(d))["dirty"], 1)

    def test_clone_with_local_commit_is_ahead(self):
        src = self.repo("src")
        self.commit(src, "f")
        clone = Path(self.tmp.name) / "clone"
        self.git(self.tmp.name, "clone", "-q", str(src), str(clone))
        self.git(clone, "config", "user.name", "t")
        self.git(clone, "config", "user.email", "t@example.com")
        self.assertEqual(W.repo_state(str(clone)), {"remote": True, "ahead": 0, "dirty": 0})
        self.commit(clone, "g")
        self.assertEqual(W.repo_state(str(clone)), {"remote": True, "ahead": 1, "dirty": 0})

    def test_missing_folder_is_error(self):
        self.assertIn("error", W.repo_state(str(Path(self.tmp.name) / "nope")))

    def test_build_index_fills_state(self):
        d = self.repo("a")
        self.commit(d, "f")
        projects = [{"path": str(d), "abs": str(d), "worktree_of": None}]
        sessions = [{"id": "s", "project": str(d), "branch": "main", "first_ts": "2026-11-01T00:00:00Z",
                     "last_ts": "2026-11-01T01:00:00Z", "cost": 0}]
        idx = W.build_index(sessions, projects, {}, {}, now="2026-11-06T00:00:00Z")
        self.assertEqual(idx["workspaces"][-1]["repos"][0]["state"], {"remote": False, "ahead": 0, "dirty": 0})


BG_OUT = "Starting background service…\nbackgrounded · abcd1234 · fz-probe\n"
UID = "11111111-2222-3333-4444-555555555555"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
beh = json.load(open(d + "/behaviour.json"))
a = sys.argv[1:]
kind = "agents" if a[:1] == ["agents"] else "relay" if "-p" in a else "bg"
stdin = sys.stdin.read() if kind == "relay" else None
open(d + "/calls.jsonl", "a").write(json.dumps({"kind": kind, "argv": a, "cwd": os.getcwd(), "stdin": stdin,
                                                  "tool_search": os.environ.get("ENABLE_TOOL_SEARCH")}) + "\n")
b = beh[kind]
if kind == "relay":  # stream-json: calls x (SendMessage tool_use + tool_result), then a final result row
    rows = [{"type": "system"}]
    for n in range(b.get("calls", 1)):
        rows.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}, {
            "type": "tool_use", "id": "toolu_%d" % n, "name": "SendMessage", "input": {"to": b.get("to", "uds:" + a[a.index("--append-system-prompt") + 1].split("uds:")[1].split(" ")[0]),
                                                                  "message": b.get("send", stdin)}}]}})
        if b.get("tool", True) is not None:
            tr = b.get("tool") or {"success": True, "message": "sent"}
            txt = tr if isinstance(tr, str) else json.dumps(tr)
            rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": b.get("tool_id", "toolu_%d" % n),
                                                                  "content": [{"type": "text", "text": txt}]}]}})
    rows.insert(1, {"no_type": 1})
    rows.append({"type": "result", "result": b.get("result", "DELIVERED")})
    sys.stdout.write("junk\n" + "\n".join(json.dumps(x) for x in rows) + "\n")
sys.stdout.write(b.get("out", ""))
sys.stderr.write(b.get("err", ""))
sys.exit(b.get("code", 0))
'''


class ActionApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import http.server
        import threading
        cls.old = {k: os.environ.get(k) for k in ("STELLAXIS_HOME", "CLAUDE_CONFIG_DIR")}
        cls.srv_cfg = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not W.handle(self, "GET", cls.srv_cfg):
                    self.send_error(404)

            def do_POST(self):
                if not W.handle(self, "POST", cls.srv_cfg):
                    self.send_error(404)

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k, v in cls.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.repo = str(self.dir / "repo")
        os.mkdir(self.repo)
        os.environ["STELLAXIS_HOME"] = str(self.dir / "stx")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.dir / "claude")
        (self.dir / "stx").mkdir()
        (self.dir / "claude" / "sessions").mkdir(parents=True)
        (self.dir / "claude" / "projects" / "p").mkdir(parents=True)
        self.bin = self.dir / "bin" / "claude"
        self.bin.parent.mkdir()
        self.bin.write_text(FAKE)
        self.bin.chmod(0o755)
        self.cfg = {"claude_bin": str(self.bin)}
        self.__class__.srv_cfg = self.cfg
        self.behave(agents={"out": "[]"}, bg={"out": BG_OUT}, relay={})
        self.index(sessions=[{"id": UID, "path": self.repo}])

    def behave(self, **kw):
        p = self.dir / "bin" / "behaviour.json"
        cur = json.loads(p.read_text()) if p.exists() else {}
        cur.update(kw)
        p.write_text(json.dumps(cur))

    def index(self, sessions=()):
        (self.dir / "stx" / "workspaces.json").write_text(json.dumps({"workspaces": [
            {"id": "w", "name": "W", "repos": [{"path": self.repo, "exists": True, "branches": [
                {"name": "b", "sessions": list(sessions)}]}]}]}))

    def calls(self, kind=None):
        p = self.dir / "bin" / "calls.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if r["kind"] != "agents" and (kind is None or r["kind"] == kind)]

    def fake_args(self):
        return self.calls("bg")[-1]["argv"]

    def post(self, path, body, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", path, json.dumps(body), {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def get(self, path):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def listed(self, socket=True):
        self.behave(agents={"out": json.dumps([{"sessionId": UID, "status": "idle"}])})
        if socket:
            (self.dir / "claude" / "sessions" / "123.json").write_text(
                json.dumps({"sessionId": UID, "messagingSocketPath": "/tmp/s.sock"}))

    def test_prompt_is_one_literal_argument(self):
        p = '--dangerously-skip-permissions "x"\n$(rm)'
        W.start(self.repo, p, self.cfg)
        self.assertEqual(self.fake_args()[-2:], ["--", p])
        a = self.fake_args()
        self.assertEqual(a[:2], ["--bg", "--name=--dangerously-skip-permissions \"x\" $(rm)"])
        self.assertEqual(a.index("--"), 2)
        self.assertEqual(self.calls("bg")[-1]["cwd"], os.path.realpath(self.repo))

    def test_prompt_never_a_token_before_double_dash(self):
        p = "--dangerously-skip-permissions"
        W.start(self.repo, p, self.cfg)
        a = self.fake_args()
        self.assertNotIn(p, a[:a.index("--")])
        self.assertEqual(a[-2:], ["--", p])

    def test_id_found_on_stderr_and_missing_id_is_502(self):
        self.behave(bg={"out": "", "err": "backgrounded · feed1234 · x\n"})
        self.assertEqual(W.start(self.repo, "hi", self.cfg), {"id": "feed1234"})
        self.behave(bg={"out": "nothing useful\n"})
        with self.assertRaises(W.ApiError) as c:
            W.start(self.repo, "hi", self.cfg)
        self.assertEqual(c.exception.status, 502)

    def test_unsendable_prompts_are_400(self):
        for bad in ("a\x00b", "a\ud800b"):
            self.assertEqual(self.post("/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": bad})[0], 400)
        self.assertEqual(self.calls(), [])

    def test_bad_content_length_is_400(self):
        import http.client
        for n in ("abc", "-5"):
            c = http.client.HTTPConnection("127.0.0.1", self.port)
            c.putrequest("POST", "/api/sessions/new")
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", n)
            c.endheaders()
            self.assertEqual(c.getresponse().status, 400)

    def test_resume_that_started_a_copy_is_409_with_copy_id(self):
        self.behave(bg={"out": "note: session a55c6b8f is already running in the background, so this started a copy as 5952a744.\n"
                               "backgrounded · 5952a744 · x\n"})
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "go"})
        self.assertEqual(code, 409)
        self.assertIn("5952a744", body["error"])
        self.assertEqual(body["copy_id"], "5952a744")

    def test_relay_timeout_and_nonzero_exit_are_502(self):
        self.listed()
        real = subprocess.run

        def slow_relay(args, **kw):
            if "-p" in args:
                raise subprocess.TimeoutExpired(args, 90)
            return real(args, **kw)
        with mock.patch("workspaces.subprocess.run", side_effect=slow_relay):
            self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})[0], 502)
        self.behave(relay={"code": 1, "tool": None})
        self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})[0], 502)

    def test_foreign_origin_is_403(self):
        body = {"workspace": "w", "folder": self.repo, "prompt": "hi"}
        self.assertEqual(self.post("/api/sessions/new", body, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.calls(), [])

    def test_start_returns_id_from_output(self):
        self.assertEqual(W.start(self.repo, "hi", self.cfg), {"id": "abcd1234"})

    def test_new_session_endpoint_and_prompt_limits(self):
        self.assertEqual(self.post("/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": "hi  \n"}),
                         (200, {"id": "abcd1234"}))
        self.assertEqual(self.fake_args()[-1], "hi")
        for bad in ("", "   ", "x" * 4001, 5):
            self.assertEqual(self.post("/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": bad})[0], 400)
        self.assertEqual(len(self.calls("bg")), 1)

    def test_new_session_refuses_folder_outside_workspace(self):
        other = self.dir / "other"
        other.mkdir()
        code, _ = self.post("/api/sessions/new", {"workspace": "w", "folder": str(other), "prompt": "hi"})
        self.assertEqual(code, 403)
        self.assertEqual(self.post("/api/sessions/new", {"workspace": "nope", "folder": self.repo, "prompt": "hi"})[0], 404)
        self.assertEqual(self.calls(), [])

    def test_cli_refusal_reaches_the_page(self):
        self.behave(bg={"err": "Workspace not trusted\n", "code": 1})
        code, body = self.post("/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": "hi"})
        self.assertEqual(code, 422)
        self.assertIn("Workspace not trusted", body["error"])

    def test_listed_session_uses_relay_never_resume(self):
        self.listed()
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "hello there\n"})
        self.assertEqual((code, body), (200, {"via": "relay"}))
        self.assertEqual(self.calls("bg"), [])
        r = self.calls("relay")[0]
        self.assertEqual(r["stdin"], "hello there" + W.RELAY_NOTE)
        self.assertTrue(r["stdin"].endswith("\n\n(Sent from the Stellaxis dashboard. Reply here in this session, not to the sender.)"))
        self.assertNotIn("hello there", " ".join(r["argv"]))
        self.assertIn("uds:/tmp/s.sock", " ".join(r["argv"]))

    def test_relay_arguments_allow_only_send_message(self):
        self.listed()
        self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
        c = self.calls("relay")[0]
        a = c["argv"]
        self.assertEqual(a[a.index("--tools") + 1], "SendMessage")
        self.assertEqual(a[a.index("--allowedTools") + 1], "SendMessage")
        self.assertIn("--strict-mcp-config", a)
        self.assertNotIn("--dangerously-skip-permissions", a)
        self.assertEqual(c["tool_search"], "false")

    def test_relay_wrong_text_two_calls_or_no_result_are_502(self):
        self.listed()
        for beh, word in (({"send": "something else"}, "different text"), ({"calls": 2}, "2 SendMessage"),
                          ({"tool": None}, "did not confirm"), ({"tool_id": "toolu_other"}, "did not confirm"),
                          ({"tool": {"success": False, "message": "Failed to send: ENOENT"}}, "ENOENT")):
            self.behave(relay=beh)
            code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
            self.assertEqual(code, 502, beh)
            self.assertIn(word, body["error"], beh)

    def test_relay_success_ignores_final_result_text(self):
        self.listed()
        self.behave(relay={"result": "Thanks, the other session replied something else"})
        self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": "x"}), (200, {"via": "relay"}))

    def test_relay_wrong_recipient_is_502(self):
        self.listed()
        self.behave(relay={"to": "uds:/tmp/other.sock"})
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
        self.assertEqual(code, 502)
        self.assertIn("wrong recipient", body["error"])

    def test_relay_plain_text_tool_result_keeps_its_text(self):
        self.listed()
        self.behave(relay={"tool": "Error: socket closed"})
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
        self.assertEqual(code, 502)
        self.assertIn("socket closed", body["error"])

    def test_relay_trailing_whitespace_difference_is_fine(self):
        self.listed()
        self.behave(relay={"send": "x" + W.RELAY_NOTE + "\n"})
        self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})[0], 200)

    def test_listed_session_without_socket_is_refused(self):
        self.listed(socket=False)
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
        self.assertEqual(code, 409)
        self.assertIn("open", body["error"])
        self.assertEqual(self.calls(), [])

    def test_unlisted_session_uses_resume(self):
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "go on"})
        self.assertEqual((code, body), (200, {"via": "resume"}))
        c = self.calls("bg")[0]
        self.assertEqual(c["argv"], ["--bg", "--resume", UID, "--", "go on"])
        self.assertEqual(c["cwd"], os.path.realpath(self.repo))
        self.assertEqual(self.calls("relay"), [])

    def test_message_refused_when_live_list_unavailable(self):
        self.behave(agents={"out": "garbage"})
        self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})[0], 503)
        self.assertEqual(self.calls(), [])

    def test_relay_failure_reaches_the_page(self):
        self.listed()
        self.behave(relay={"tool": {"success": False, "message": "connect ECONNREFUSED /tmp/s.sock"}})
        code, body = self.post(f"/api/sessions/{UID}/message", {"prompt": "x"})
        self.assertEqual(code, 502)
        self.assertIn("ECONNREFUSED", body["error"])

    def test_message_rejects_non_uuid_id_and_bad_prompt(self):
        self.assertEqual(self.post("/api/sessions/not-a-uuid/message", {"prompt": "x"})[0], 400)
        self.assertEqual(self.post(f"/api/sessions/{UID}/message", {"prompt": " "})[0], 400)
        self.assertEqual(self.calls(), [])

    def test_foreign_host_and_non_json_are_refused(self):
        body = {"workspace": "w", "folder": self.repo, "prompt": "hi"}
        self.assertEqual(self.post("/api/sessions/new", body, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post("/api/sessions/new", body, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.calls(), [])

    def test_socket_of_tolerates_bad_files(self):
        d = self.dir / "claude" / "sessions"
        (d / "a.json").write_text("{not json")
        (d / "b.json").write_text("[1]")
        (d / "c.json").write_text(json.dumps({"sessionId": UID, "messagingSocketPath": "/s"}))
        self.assertEqual(W.socket_of(UID, d), "/s")
        self.assertIsNone(W.socket_of("other", d))

    def test_last_reply_is_newest_assistant_text_from_tail(self):
        def rec(t, text, ts):
            return json.dumps({"type": t, "timestamp": ts, "message": {"content": [{"type": "text", "text": text}]}})
        f = self.dir / "claude" / "projects" / "p" / f"{UID}.jsonl"
        f.write_text("\n".join([rec("assistant", "old" + "x" * 600000, "t1"), rec("assistant", "new", "t2"),
                                rec("user", "q", "t3")]) + "\n")
        self.assertEqual(self.get(f"/api/sessions/{UID}/reply"), (200, {"text": "new", "ts": "t2"}))
        self.assertEqual(self.get("/api/sessions/11111111-2222-3333-4444-000000000000/reply")[0], 404)
        self.assertEqual(self.get("/api/sessions/zzz/reply")[0], 400)


if __name__ == "__main__":
    unittest.main()
