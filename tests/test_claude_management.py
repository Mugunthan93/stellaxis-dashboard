"""Tests of the claude_management component (collector/components/claude_management).

  python3 -m unittest tests.test_claude_management
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
sys.path.insert(0, str(REPO / "tests"))
from core import undo
from core.app import Core
from core.component import import_folder
from core.server import ApiError
from core.util import short_path

import test_hardening

C = test_hardening.C
COMPS = REPO / "collector" / "components"
CENTRE = import_folder(COMPS / "project_management").centre
CM = import_folder(COMPS / "claude_management")
PM = import_folder(COMPS / "project_management")
CFG = {"scan_roots": [], "claude_bin": "/no/claude", "pricing": {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2}}}
COST = (1000 * 4.0 + 100 * 20.0) / 1e6


class FakeHome(unittest.TestCase):
    """A fake home with one skill, one repo and one transcript; no tests of its own."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.stx, self.claude, self.app = root / "stx", root / "claude", root / "code" / "app"
        self.stx.mkdir()
        (self.claude / "skills" / "foo").mkdir(parents=True)
        (self.claude / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: Does foo\n---\nbody\n")
        self.app.mkdir(parents=True)
        (self.app / "CLAUDE.md").write_text("# app\n")
        self.transcript = self.claude / "projects" / "p" / "s1.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-11-10T09:00:05Z", "sessionId": "s1", "cwd": str(self.app),
            "gitBranch": "main", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {"input_tokens": 1000, "output_tokens": 100},
                                             "content": [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "foo"}}]}}) + "\n")
        test_hardening.fake_home(self, self.stx, self.claude)
        for patch in (mock.patch.object(CENTRE, "live", return_value=[]), mock.patch.object(CENTRE, "app_records", return_value={})):
            patch.start()
            self.addCleanup(patch.stop)

    def rows(self, table):
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db:
            return db.execute(f"SELECT * FROM {table}").fetchall()

    def status(self):
        return json.loads((self.stx / "status.json").read_text())


class ClaudeManagementTest(FakeHome):
    def test_the_component_loads(self):
        core = Core(self.stx, self.claude, COMPS)
        [c] = [c for c in core.components if c.name == "claude_management"]
        self.assertEqual((c.error, len(c.readers), c.lines is not None, sorted(c.offers)), (None, 2, True, ["items", "sessions"]))
        self.assertEqual([tuple(r) for r in c.reads], [("project_management", "repos")])

    def index_ids(self):
        idx = json.loads((self.stx / "projects.json").read_text())
        return [s["id"] for p in idx["projects"] for r in p["repos"] for b in r["branches"] for s in b["sessions"]]

    def test_the_items_of_a_repo_come_from_the_repos_project_management_found(self):
        snap = C.collect(dict(CFG))
        self.assertIn(f"claude_md:project:{short_path(self.app)}:CLAUDE.md", [it["id"] for it in snap["items"]])
        self.assertEqual([p["path"] for p in snap["projects"]], [short_path(self.app)])

    def test_without_project_management_only_the_macs_own_items_are_listed(self):
        [pm] = [c for c in C.the_core().components if c.name == "project_management"]
        def boom(ctx, **args):
            raise RuntimeError("gone")
        with mock.patch.dict(pm.offers, {"repos": boom}):
            snap = C.collect(dict(CFG))
        ids = [it["id"] for it in snap["items"]]
        self.assertIn("skill:user:foo", ids)
        self.assertFalse([i for i in ids if i.startswith("claude_md:project:")])
        self.assertEqual([s["id"] for s in snap["sessions"]], ["s1"])
        self.assertEqual([(p["path"], p["sessions"]) for p in snap["projects"]], [(short_path(self.app), 1)])

    def test_a_collection_writes_the_snapshot_and_the_rows(self):
        snap = C.collect(dict(CFG))
        items = {it["id"]: it for it in snap["items"]}
        self.assertEqual(items["skill:user:foo"]["usage"]["calls"], 1)
        [s] = snap["sessions"]
        self.assertEqual(s["id"], "s1")
        self.assertGreater(s["cost"], 0)
        self.assertEqual(len(self.rows("claude_management_sessions")), 1)
        self.assertEqual(len(self.rows("claude_management_items")), len(snap["items"]))
        core = C.the_core()
        [row] = core.read("claude_management", "sessions", CFG)
        self.assertEqual((row["id"], row["cost"], "hours" in row), ("s1", s["cost"], False))
        self.assertEqual(set(row), {"id", "project", "first_ts", "last_ts", "branch", "cost"})
        [row] = core.read("claude_management", "sessions", CFG, hours=True)
        [(hour, cost)] = row["hours"].items()
        self.assertEqual(hour, "2026-11-10T09")
        self.assertAlmostEqual(cost, s["cost"], delta=1e-6)
        self.assertAlmostEqual(cost, COST, delta=1e-9)
        self.assertIn("skill:user:foo", [it["id"] for it in core.read("claude_management", "items", CFG)])
        self.assertEqual([it["id"] for it in core.read("claude_management", "items", CFG)], [it["id"] for it in snap["items"]])
        self.assertIsNone(self.status()["index_error"])

    def test_a_second_collection_with_nothing_new_rewrites_no_session_row(self):
        C.collect(dict(CFG))
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            db.execute("UPDATE claude_management_sessions SET project = 'marker'")
        C.collect(dict(CFG))
        self.assertEqual([r[1] for r in self.rows("claude_management_sessions")], ["marker"])

    def test_a_session_that_is_gone_leaves_the_store(self):
        C.collect(dict(CFG))
        self.assertEqual(len(self.rows("claude_management_sessions")), 1)
        self.transcript.unlink()
        C.collect(dict(CFG))
        self.assertEqual(self.rows("claude_management_sessions"), [])

    def test_with_the_store_down_the_snapshot_is_still_written(self):
        (self.stx / "stellaxis.db").mkdir()
        snap = C.collect(dict(CFG))
        self.assertIn("skill:user:foo", [it["id"] for it in snap["items"]])
        self.assertIn(f"claude_md:project:{short_path(self.app)}:CLAUDE.md", [it["id"] for it in snap["items"]])
        st = self.status()
        self.assertTrue(st["core_error"])
        self.assertIsNone(st["index_error"])
        idx = json.loads((self.stx / "projects.json").read_text())
        [s] = idx["projects"][0]["repos"][0]["branches"][0]["sessions"]
        self.assertEqual((s["id"], s["cost"]), ("s1", snap["sessions"][0]["cost"]))
        self.assertGreater(s["cost"], 0)

    def test_a_pass_that_is_not_a_collection_reads_nothing(self):
        res = C.the_core().run_pass(dict(CFG))
        self.assertEqual(res["components"]["claude_management"]["errors"], [])
        self.assertFalse((self.stx / "snapshot.json").exists())
        self.assertFalse((self.stx / "cache.json").exists())

    def test_a_component_with_an_empty_config_does_the_least(self):
        with mock.patch.object(C, "load_config", return_value={}):
            snap = C.collect({})
        self.assertIsNone(self.status()["index_error"])
        self.assertIn("skill:user:foo", [it["id"] for it in snap["items"]])
        self.assertEqual(snap["sessions"][0]["cost"], 0)

    def test_every_components_error_is_in_the_status(self):
        with mock.patch.object(CENTRE, "build_index", side_effect=ValueError("boom")), \
                mock.patch.object(CM.records, "save_items", side_effect=KeyError("k")):
            C.collect(dict(CFG))
        self.assertEqual(self.status()["index_error"], "claude_management: KeyError: 'k'; project_management: ValueError: boom")
        self.assertEqual(C.last_error, self.status()["index_error"])

    def test_a_failed_collection_keeps_the_last_good_index(self):
        C.collect(dict(CFG))
        other = self.claude / "projects" / "p" / "s2.jsonl"
        other.write_text(self.transcript.read_text().replace('"s1"', '"s2"').replace('"m1"', '"m9"'))
        with mock.patch.object(PM.repos, "discover", side_effect=KeyError("k")):
            snap = C.collect(dict(CFG))
        self.assertEqual(self.index_ids(), ["s1"])                  # Project management's own round one failed
        self.assertEqual(self.status()["index_error"], "project_management: KeyError: 'k'")
        self.assertEqual(sorted(s["id"] for s in snap["sessions"]), ["s1", "s2"])
        self.assertIn(f"claude_md:project:{short_path(self.app)}:CLAUDE.md", [it["id"] for it in snap["items"]])   # the repos of the last good pass
        with mock.patch.object(CM.snapshot, "build", side_effect=KeyError("k")):
            C.collect(dict(CFG))
        self.assertEqual(sorted(self.index_ids()), ["s1", "s2"])    # Claude management's failure does not stop the index
        self.assertEqual(self.status()["index_error"], "claude_management: KeyError: 'k'")

    def test_a_failed_collection_right_after_a_good_one_returns_no_snapshot(self):
        good = C.collect(dict(CFG))
        self.assertTrue(good)
        with mock.patch.object(CM.snapshot, "build", side_effect=KeyError("k")):
            self.assertEqual(C.collect(dict(CFG)), {})
        st = self.status()   # the last snapshot is still the one on disk
        self.assertEqual((st["last_snapshot"], st["elapsed_ms"]), (good["generated_at"], good["meta"]["elapsed_ms"]))
        self.assertEqual(st["index_error"], "claude_management: KeyError: 'k'")

    def test_a_cut_last_line_in_the_history_counts_as_no_last_row(self):
        for cut in ('{"t": "2026-11-10T09:00:00Z", "glo', "12", '{"no_t": 1}'):
            with self.subTest(cut=cut):
                (self.stx / "history.jsonl").write_text('{"t": "2026-11-10T08:00:00Z"}\n' + cut)
                snap = C.collect(dict(CFG))
                self.assertIsNone(self.status()["index_error"])
                lines = (self.stx / "history.jsonl").read_text().splitlines()
                self.assertEqual(json.loads(lines[-1])["t"], snap["generated_at"])
                self.assertEqual(snap["history"][-1]["t"], snap["generated_at"])

    def test_the_desktop_tool_says_so_when_its_own_collection_wrote_nothing(self):
        import mcp_server
        (self.stx / "config.json").write_text(json.dumps({**CFG, "port": 1}))   # no service there: the tool collects inline
        call = {"id": 1, "method": "tools/call", "params": {"name": "stellaxis_snapshot", "arguments": {}}}
        with mock.patch.object(CM.snapshot, "build", side_effect=KeyError("k")):
            got = mcp_server.handle(call)
        self.assertEqual(got, {"content": [{"type": "text", "text": "stellaxis error: claude_management: KeyError: 'k'"}], "isError": True})
        with mock.patch.object(C, "collect", return_value={}), mock.patch.object(C, "last_error", None):
            self.assertEqual(mcp_server.handle(call)["content"][0]["text"], "stellaxis error: no snapshot was written")

    def test_a_failed_round_one_gives_one_error(self):
        with mock.patch.object(CM.usage, "sessions", side_effect=KeyError("u")):
            C.collect(dict(CFG))
        self.assertEqual(self.status()["index_error"], "claude_management: KeyError: 'u'")
        with mock.patch("core.transcripts.read", side_effect=OSError("disk")):
            C.collect(dict(CFG))
        self.assertEqual(self.status()["index_error"], "claude_management: OSError: disk; project_management: OSError: disk")

    def test_a_session_that_grows_is_rewritten_and_an_unchanged_one_is_not(self):
        other = self.claude / "projects" / "p" / "s2.jsonl"
        other.write_text(self.transcript.read_text().replace('"s1"', '"s2"').replace('"m1"', '"m9"'))
        C.collect(dict(CFG))
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            db.execute("UPDATE claude_management_sessions SET project = 'marker' WHERE id = 's2'")
            before = db.execute("SELECT cost, data, hours FROM claude_management_sessions WHERE id = 's1'").fetchone()
        line = json.loads(self.transcript.read_text())
        line.update(timestamp="2026-11-10T11:00:00Z", message={**line["message"], "id": "m2", "content": []})
        with open(self.transcript, "a") as f:
            f.write(json.dumps(line) + "\n")
        C.collect(dict(CFG))
        rows = {r[0]: r for r in self.rows("claude_management_sessions")}
        cost, data, hours = rows["s1"][5], rows["s1"][6], rows["s1"][7]
        self.assertAlmostEqual(cost, 2 * COST, delta=1e-9)
        self.assertNotEqual(data, before[1])
        self.assertEqual(sorted(json.loads(hours)), ["2026-11-10T09", "2026-11-10T11"])
        self.assertNotEqual(before[0], cost)
        self.assertEqual(rows["s2"][1], "marker")

    def test_an_item_that_changes_is_rewritten_and_an_unchanged_one_is_not(self):
        C.collect(dict(CFG))
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            db.execute("UPDATE claude_management_items SET name = 'marker'")
        (self.claude / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: Does foo, and more\n---\nbody\n")
        C.collect(dict(CFG))
        rows = {r[0]: r for r in self.rows("claude_management_items")}
        self.assertEqual(rows["skill:user:foo"][4], "foo")
        self.assertEqual(json.loads(rows["skill:user:foo"][5])["description"], "Does foo, and more")
        others = [r for i, r in rows.items() if i != "skill:user:foo"]
        self.assertTrue(others)
        self.assertEqual({r[4] for r in others}, {"marker"})


class SettingsActions(FakeHome):
    def setUp(self):
        super().setUp()
        (self.claude / "settings.json").write_text(json.dumps({"model": "opus"}))
        (self.stx / "snapshot.json").write_text(json.dumps({"projects": [{"path": str(self.app)}], "items": []}))
        self.cfg = dict(CFG)
        self.core = Core(self.stx, self.claude, COMPS)

    def restore(self, manifest, out=lambda _: None, apply=True):
        undo.restore(Path(manifest).parent.name, apply, self.claude / "_archive", handlers=self.core.undo_handlers(self.cfg), out=out)

    def save(self, body):
        return self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)

    def baks(self, folder):
        return sorted(folder.glob("*.bak-*"))

    def test_an_undo_after_a_hand_edit_keeps_the_edit_in_a_copy(self):
        f = self.claude / "settings.json"
        before = f.read_bytes()
        r = self.save({"scope": "user", "data": {"model": "sonnet"}, "base_sha": hashlib.sha256(before).hexdigest()})
        f.write_text('{"model": "edited by hand"}')
        said = []
        self.restore(r["manifest"], out=said.append, apply=False)
        self.assertEqual(f.read_text(), '{"model": "edited by hand"}')
        self.assertEqual(self.baks(self.claude), [Path(r["result"]["backup"])])
        self.assertTrue(any("would be kept" in line for line in said), said)
        said = []
        self.restore(r["manifest"], out=said.append)
        self.assertEqual(f.read_bytes(), before)
        [kept] = [b for b in self.baks(self.claude) if str(b) != r["result"]["backup"]]
        self.assertEqual(kept.read_text(), '{"model": "edited by hand"}')
        self.assertTrue(any(f"a copy is kept as {short_path(kept)}" in line for line in said), said)
        self.assertNotIn("edited by hand", "".join(said))

    def test_an_undo_of_a_made_file_after_a_hand_edit_keeps_the_edit_and_removes_the_file(self):
        f = self.app / ".claude" / "settings.local.json"
        r = self.save({"scope": "local", "project": str(self.app), "data": {"model": "haiku"}, "base_sha": None})
        f.write_text('{"model": "edited by hand"}')
        self.restore(r["manifest"], apply=False)
        self.assertTrue(f.is_file())
        self.assertEqual(self.baks(f.parent), [])
        self.restore(r["manifest"])
        self.assertFalse(f.exists())
        [kept] = self.baks(f.parent)
        self.assertEqual(kept.read_text(), '{"model": "edited by hand"}')

    def test_an_undo_of_an_untouched_file_keeps_no_copy(self):
        f = self.claude / "settings.json"
        r = self.save({"scope": "user", "data": {"model": "sonnet"}, "base_sha": hashlib.sha256(f.read_bytes()).hexdigest()})
        said = []
        self.restore(r["manifest"], out=said.append)
        self.assertEqual(self.baks(self.claude), [Path(r["result"]["backup"])])
        self.assertIn(f"  put {short_path(f)} back from {short_path(r['result']['backup'])}", said)

    def test_the_restored_file_gets_the_mode_it_had_before_the_save(self):
        f = self.claude / "settings.json"
        os.chmod(f, 0o600)
        r = self.save({"scope": "user", "data": {"model": "sonnet"}, "base_sha": hashlib.sha256(f.read_bytes()).hexdigest()})
        os.chmod(f, 0o644)
        self.restore(r["manifest"])
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)

    def test_a_save_can_be_undone_and_its_data_is_not_logged(self):
        f = self.claude / "settings.json"
        before = f.read_bytes()
        body = {"scope": "user", "data": {"model": "sonnet", "env": {"TOKEN": "s3cret-value"}}, "base_sha": hashlib.sha256(before).hexdigest()}
        r = self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)
        self.assertEqual(json.loads(f.read_text())["model"], "sonnet")
        self.assertTrue(Path(r["result"]["backup"]).is_file())
        self.assertNotIn("s3cret-value", (self.stx / "actions.jsonl").read_text())
        self.assertNotIn("s3cret-value", Path(r["manifest"]).read_text())
        said = []
        self.restore(r["manifest"], out=said.append)
        self.assertEqual(f.read_bytes(), before)
        self.assertIn(f"  put {short_path(f)} back from {short_path(r['result']['backup'])}", said)

    def test_undoing_the_save_of_a_new_file_removes_it(self):
        f = self.app / ".claude" / "settings.local.json"
        body = {"scope": "local", "project": str(self.app), "data": {"model": "haiku"}, "base_sha": None}
        r = self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)
        self.assertIsNone(r["result"]["backup"])
        self.assertTrue(f.is_file())
        self.restore(r["manifest"])
        self.assertFalse(f.exists())

    def test_a_missing_backup_stops_the_undo(self):
        f = self.claude / "settings.json"
        body = {"scope": "user", "data": {"model": "sonnet"}, "base_sha": hashlib.sha256(f.read_bytes()).hexdigest()}
        r = self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)
        Path(r["result"]["backup"]).unlink()
        with self.assertRaises(undo.RestoreError):
            self.restore(r["manifest"])
        self.assertEqual(json.loads(f.read_text())["model"], "sonnet")

    def test_a_preview_writes_nothing_and_logs_nothing(self):
        f = self.claude / "settings.json"
        before = f.read_bytes()
        r = self.core.run_action("claude_management", "save_settings", {"body": {"scope": "user", "data": {"model": "sonnet"}}}, False, self.cfg)
        self.assertIn('+{"model": "sonnet"}', r["result"]["diff"])
        self.assertEqual(f.read_bytes(), before)
        self.assertFalse((self.stx / "actions.jsonl").exists())
        self.assertFalse((self.claude / "_archive").exists())

    def test_a_refused_save_changes_nothing(self):
        f = self.claude / "settings.json"
        before = f.read_bytes()
        body = {"scope": "user", "data": {"model": "sonnet"}, "base_sha": "wrong"}
        with self.assertRaises(ApiError) as e:
            self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(f.read_bytes(), before)
        [row] = [json.loads(l) for l in (self.stx / "actions.jsonl").read_text().splitlines()]
        self.assertEqual((row["action"], row["ok"], row["args"], row["manifest"]), ("save_settings", False, {}, None))


class TrimActions(FakeHome):
    def setUp(self):
        super().setUp()
        self.cfg = dict(CFG)
        C.collect(self.cfg)
        self.skill = self.claude / "skills" / "foo"

    def archive(self, core, apply):
        return core.run_action("claude_management", "archive", {"ids": ["skill:user:foo", "nope:x:y"]}, apply, self.cfg)

    def test_a_dry_run_says_what_it_would_do_and_changes_nothing(self):
        r = self.archive(C.the_core(), False)
        self.assertEqual(len(r["steps"]), 1)
        self.assertTrue(r["steps"][0].startswith(f"move {short_path(self.skill)} -> "), r["steps"])
        self.assertEqual(r["notes"], ["  unknown id nope:x:y (run `prune.py list`)"])
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertFalse((self.claude / "_archive").exists())

    def test_an_archive_is_undone_by_restore(self):
        core = C.the_core()
        r = self.archive(core, True)
        self.assertFalse(self.skill.exists())
        undo.restore(Path(r["manifest"]).parent.name, True, self.claude / "_archive", handlers=core.undo_handlers(self.cfg), out=lambda _: None)
        self.assertTrue((self.skill / "SKILL.md").is_file())

    def test_with_the_store_down_the_items_come_from_the_snapshot(self):
        db = self.stx / "stellaxis.db"
        db.unlink()
        db.mkdir()
        core = Core(self.stx, self.claude, COMPS)
        self.assertIsNotNone(core.error)
        r = self.archive(core, False)
        self.assertEqual(len(r["steps"]), 1)
        self.assertEqual(r["notes"], ["  unknown id nope:x:y (run `prune.py list`)"])


class MoveAction(FakeHome):
    def setUp(self):
        super().setUp()
        self.cfg = dict(CFG)
        C.collect(self.cfg)
        self.skill = self.claude / "skills" / "foo"
        self.dest = self.app / ".claude" / "skills" / "foo"

    def move(self, apply):
        return C.the_core().run_action("claude_management", "move", {"ids": ["skill:user:foo", "nope:x:y"], "to": str(self.app)}, apply, self.cfg)

    def test_a_dry_run_writes_nothing(self):
        r = self.move(False)
        self.assertEqual(r["steps"], [f"move {short_path(self.skill)} -> {short_path(self.dest)}"])
        self.assertIn("  unknown id nope:x:y (run `prune.py list`)", r["notes"])
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.claude / "_archive").exists())

    def test_an_applied_move_writes_one_manifest_and_restore_puts_it_back(self):
        r = self.move(True)
        self.assertFalse(self.skill.exists())
        self.assertTrue((self.dest / "SKILL.md").is_file())
        self.assertEqual(len(list((self.claude / "_archive").glob("*/manifest.json"))), 1)
        undo.restore(Path(r["manifest"]).parent.name, True, self.claude / "_archive",
                     handlers=C.the_core().undo_handlers(self.cfg), out=lambda _: None)
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertFalse(self.dest.exists())


class RunCommandTest(unittest.TestCase):
    def test_a_failed_run_says_why_and_exits_with_1(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(os.path.realpath(t))
            stx = root / "stx"
            (stx / "snapshot.json").mkdir(parents=True)     # Claude management cannot write its snapshot
            (stx / "config.json").write_text(json.dumps({"scan_roots": [], "claude_bin": "/no/claude"}))
            (root / "home").mkdir()
            env = {**os.environ, "HOME": str(root / "home"), "STELLAXIS_HOME": str(stx), "CLAUDE_CONFIG_DIR": str(root / "claude"),
                   "REELS_HOME": str(root / "no-reels")}
            r = subprocess.run([sys.executable, str(REPO / "collector" / "collect.py"), "run"], env=env, capture_output=True,
                               text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertRegex(r.stderr.strip(), r"^collect failed: claude_management: \w+Error: ")


if __name__ == "__main__":
    unittest.main()
