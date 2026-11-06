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


if __name__ == "__main__":
    unittest.main()
