"""Tests of the claude_management component (collector/components/claude_management).

  python3 -m unittest tests.test_claude_management
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
sys.path.insert(0, str(REPO / "tests"))
from core.app import Core
from core.component import import_folder

import test_hardening

C = test_hardening.C
COMPS = REPO / "collector" / "components"
CENTRE = import_folder(COMPS / "project_management").centre
CM = import_folder(COMPS / "claude_management")
CFG = {"scan_roots": [], "claude_bin": "/no/claude", "pricing": {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2}}}
COST = (1000 * 4.0 + 100 * 20.0) / 1e6


class ClaudeManagementTest(unittest.TestCase):
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

    def test_the_component_loads(self):
        core = Core(self.stx, self.claude, COMPS)
        [c] = [c for c in core.components if c.name == "claude_management"]
        self.assertEqual((c.error, len(c.readers), c.lines is not None, sorted(c.offers)), (None, 2, True, ["items", "pass", "sessions"]))

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
        st = self.status()
        self.assertTrue(st["core_error"])
        self.assertIsNone(st["index_error"])

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
                mock.patch.object(CM.snapshot, "build", side_effect=KeyError("k")):
            self.assertEqual(C.collect(dict(CFG)), {})
        self.assertEqual(self.status()["index_error"], "claude_management: KeyError: 'k'; project_management: ValueError: boom")


if __name__ == "__main__":
    unittest.main()
