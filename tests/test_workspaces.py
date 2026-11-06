"""Workspace assignment: which configured project a folder belongs to.

  python3 -m unittest discover -s tests -k test_workspaces
"""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
