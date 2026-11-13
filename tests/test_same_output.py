"""The present code gives what the code before step 3 gave: snapshot, project index, cache, status, history, the
routes and every prune command, on one fake home (see same_output_harness.py for what may differ and is masked).

  python3 -m unittest tests.test_same_output
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def leaves(obj, path=""):
    """Every value of a JSON object with the path that leads to it, so a difference names its place."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, obj


class SameOutput(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(os.path.realpath(cls.tmp.name)) / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "REELS_HOME", "SKILLS_HOME", "STELLAXIS_NOW")}
        env.update(HOME=str(home), STELLAXIS_HOME=str(home / ".stellaxis"))
        out = home.parent / "result.json"
        r = subprocess.run([sys.executable, str(HERE / "same_output_harness.py"), "--collector", str(REPO / "collector"), "--out", str(out)],
                           env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(f"the harness failed:\n{r.stdout}\n{r.stderr}")
        cls.got = json.loads(out.read_text())
        cls.want = json.loads((HERE / "same_output_expected.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def same(self, *keys):
        got, want = self.got, self.want
        for k in keys:
            got, want = got[k], want[k]
        g, w = list(leaves(got)), list(leaves(want))
        self.assertEqual([x for x in g if x not in w][:12], [], "in the new output only")
        self.assertEqual([x for x in w if x not in g][:12], [], "in the old output only")
        self.assertEqual(json.dumps(got), json.dumps(want), "same values, another order")

    def test_the_sections_are_the_ones_of_the_reference(self):
        self.assertEqual(list(self.got), list(self.want))

    def test_snapshot(self):
        for name in ("pass 1", "pass 2", "pass 3, nothing new", "after prune"):
            with self.subTest(name):
                self.same(name, "snapshot.json")

    def test_project_index_under_both_names(self):
        for name in ("pass 1", "pass 2", "pass 3, nothing new", "after prune"):
            for f in ("projects.json", "work" + "spaces.json"):
                with self.subTest(f"{name} {f}"):
                    self.same(name, f)

    def test_status_history_and_cache(self):
        for name in ("pass 1", "pass 2", "pass 3, nothing new", "after prune"):
            for f in ("status.json", "history.jsonl", "cache.json"):
                with self.subTest(f"{name} {f}"):
                    self.same(name, f)

    def test_the_desktop_tool(self):
        self.same("mcp summary")
        self.same("mcp sessions of one folder")

    def test_every_prune_command_prints_the_same(self):
        self.assertEqual(list(self.got["prune"]), list(self.want["prune"]))
        for name in self.want["prune"]:
            with self.subTest(name):
                self.assertEqual(self.got["prune"][name], self.want["prune"][name])

    def test_routes(self):
        self.assertEqual(list(self.got["routes"]), list(self.want["routes"]))
        for name in self.want["routes"]:
            with self.subTest(name):
                self.same("routes", name)

    def test_settings_files_after_the_saves(self):
        self.same("settings files after the saves")


if __name__ == "__main__":
    unittest.main()
