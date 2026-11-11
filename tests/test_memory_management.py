"""Tests of the memory_management component (collector/components/memory_management).

  python3 -m unittest tests.test_memory_management
"""
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
from core import component, server
from core.app import Core

COMPS = REPO / "collector" / "components"
MM = component.import_folder(COMPS / "memory_management")
W = MM.walk
R = MM.records


def place(path, name="t", depth=1, repos=False, owner="none"):
    return {"name": name, "path": str(path), "owner": owner, "depth": depth, "repos": repos}


def run(places, guard=None, seconds=30):
    return W.measure(places, guard or W.Guard([]), time.monotonic() + seconds, {})


def row(result, path):
    return next(r for r in result["rows"] if r["path"] == str(path))


def du(path):   # macOS du: 512-byte blocks, hard links once, symlinks not followed
    return int(subprocess.run(["du", "-s", str(path)], capture_output=True, text=True, check=True).stdout.split()[0]) * 512


def put(path, size=0, text=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode() if text is not None else b"x" * size)
    return path


def recorder():
    calls, real = [], os.scandir

    def scandir(path):
        calls.append(str(path))
        return real(path)
    return calls, scandir


class WalkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(self.clean)

    def clean(self):
        for dirpath, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(dirpath, d), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tree(self, name="a"):
        a = self.tmp / name
        a.mkdir()
        return a

    def test_sizes_match_du_and_the_exact_logical_size(self):
        a = self.tree()
        put(a / "f1", 1000)
        put(a / "sub" / "f2", 5000)
        put(a / "sub" / "deep" / "f3", 70000)
        r = run([place(a)])
        top = row(r, a)
        self.assertEqual(top["bytes"], du(a))
        self.assertEqual(top["logical_bytes"], 76000)
        self.assertEqual(top["files"], 3)
        self.assertEqual(top["kind"], "place")
        self.assertEqual(top["state"], "ok")
        self.assertIsNone(top["parent"])
        self.assertRegex(top["changed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        sub = row(r, a / "sub")
        self.assertEqual(sub["logical_bytes"], 75000)
        self.assertEqual(sub["kind"], "folder")
        self.assertEqual(sub["parent"], str(a))
        self.assertFalse([x for x in r["rows"] if x["path"] == str(a / "sub" / "deep")])
        self.assertIs(r["complete"], True)
        self.assertEqual(r["files"], 3)
        self.assertEqual(r["skipped"], [])

    def test_a_hard_linked_file_is_counted_once(self):
        a = self.tree()
        f = put(a / "x" / "f", 8192)
        (a / "y").mkdir()
        os.link(f, a / "y" / "g")
        r = run([place(a)])
        blocks = os.lstat(f).st_blocks * 512
        top = row(r, a)
        self.assertEqual(top["logical_bytes"], 8192)
        self.assertEqual(top["files"], 2)
        self.assertEqual(top["bytes"], du(a))
        self.assertEqual(row(r, a / "x")["logical_bytes"], 8192)
        self.assertEqual(row(r, a / "y")["logical_bytes"], 0)
        self.assertEqual(row(r, a / "x")["linked_bytes"], blocks)
        self.assertEqual(row(r, a / "y")["linked_bytes"], blocks)
        self.assertEqual(top["linked_bytes"], 2 * blocks)

    def test_two_places_share_one_set_of_hard_links(self):
        p1, p2 = self.tree("p1"), self.tree("p2")
        os.link(put(p1 / "f", 4096), p2 / "g")
        r = run([place(p1, "one"), place(p2, "two")])
        self.assertEqual(row(r, p1)["logical_bytes"], 4096)
        self.assertEqual(row(r, p2)["logical_bytes"], 0)

    def test_a_symlink_loop_and_a_link_out_are_not_followed(self):
        a, other = self.tree(), self.tree("other")
        put(other / "big", 50000)
        put(a / "f", 10)
        (a / "sub").mkdir()
        os.symlink(a, a / "sub" / "loop")
        os.symlink(other, a / "out")
        r = run([place(a, depth=3)])
        top = row(r, a)
        self.assertEqual(top["files"], 3)
        self.assertEqual(top["logical_bytes"], 10 + os.lstat(a / "sub" / "loop").st_size + os.lstat(a / "out").st_size)
        self.assertFalse([x for x in r["rows"] if "other" in x["path"]])
        self.assertIs(r["complete"], True)

    def test_an_unreadable_folder_is_listed_and_the_rest_is_measured(self):
        a = self.tree()
        put(a / "ok" / "f", 100)
        put(a / "locked" / "f", 100)
        os.chmod(a / "locked", 0)
        r = run([place(a)])
        self.assertIn({"path": str(a / "locked"), "reason": "permission denied"}, r["skipped"])
        self.assertEqual(r["skipped_count"], 1)
        self.assertEqual(row(r, a)["state"], "partial")
        self.assertEqual(row(r, a / "ok")["state"], "ok")
        self.assertEqual(row(r, a / "ok")["logical_bytes"], 100)
        self.assertIs(r["complete"], False)
        self.assertIsNone(r["note"])

    def test_a_guarded_folder_is_never_opened(self):
        a = self.tree()
        put(a / "Documents" / "secret", 1)
        put(a / "Library" / "Caches" / "com.apple.x" / "f", 1)
        put(a / "Library" / "Caches" / "pip" / "f", 1)
        guard = W.Guard([str(a / "Documents"), str(a / "Library" / "Caches" / "com.apple.*")])
        calls, fake = recorder()
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a, depth=3)], guard)
        self.assertNotIn(str(a / "Documents"), calls)
        self.assertNotIn(str(a / "Library" / "Caches" / "com.apple.x"), calls)
        self.assertIn(str(a / "Library" / "Caches" / "pip"), calls)
        for p in (a / "Documents", a / "Library" / "Caches" / "com.apple.x"):
            self.assertIn({"path": str(p), "reason": "guarded by macOS"}, r["skipped"])

    def test_a_place_that_is_guarded_by_another_spelling_is_refused(self):
        a = self.tree()
        put(a / "Documents" / "x", 1)
        os.symlink(a / "Documents", a / "link")
        places = [place(a / "link", "link")]
        if os.path.exists(a / "documents"):
            places.append(place(a / "documents" / "x", "lower"))
        else:
            self.skipTest("case-sensitive disk")
        calls, fake = recorder()
        with mock.patch.object(W, "scandir", fake):
            r = run(places, W.Guard([str(a / "Documents")]))
        self.assertEqual(calls, [])
        self.assertEqual([x["state"] for x in r["rows"]], ["refused", "refused"])
        self.assertEqual([x["bytes"] for x in r["rows"]], [0, 0])
        self.assertEqual([x["reason"] for x in r["skipped"]], ["guarded by macOS"] * 2)

    def test_missing_not_a_folder_and_overlapping_places(self):
        a = self.tree()
        (a / "sub").mkdir()
        put(a / "f1", 1)
        r = run([place(a, "one"), place(a / "sub", "two"), place(a / "nope", "three"), place(a / "f1", "four")])
        self.assertEqual([(x["place"], x["kind"], x["state"]) for x in r["rows"] if x["kind"] == "place"],
                         [("one", "place", "ok"), ("two", "place", "refused"), ("three", "place", "missing"),
                          ("four", "place", "refused")])
        self.assertEqual(sorted(x["reason"] for x in r["skipped"]), ["not a folder", "overlaps one"])

    def test_kinds_in_a_repos_place(self):
        code = self.tree("code")
        r1 = code / "r1"
        put(r1 / ".git" / "HEAD", 1)
        put(r1 / "src" / "a.py", 1)
        put(r1 / "node_modules" / "p" / "node_modules" / "q" / "f", 1)
        put(r1 / "node_modules" / "p" / "dist" / "f", 1)
        put(r1 / ".venv" / "pyvenv.cfg", 1)
        put(r1 / "web" / ".next" / "f", 1)
        w1 = r1 / ".claude" / "worktrees" / "w1"
        put(w1 / ".git", text="gitdir: /x/r1/.git/worktrees/w1\n")
        put(w1 / "build" / "f", 1)
        put(code / "group" / "r2" / ".git" / "HEAD", 1)
        put(code / "plain" / "f", 1)
        r = run([place(code, depth=1, repos=True, owner="project_management")])
        got = {(os.path.relpath(x["path"], code), x["kind"]) for x in r["rows"]}
        self.assertEqual(got, {(".", "place"), ("r1", "repo"), ("r1/.git", "git"), ("r1/node_modules", "deps"),
                               ("r1/.venv", "venv"), ("r1/web/.next", "build"),
                               ("r1/.claude/worktrees/w1", "worktree"), ("r1/.claude/worktrees/w1/build", "build"),
                               ("group", "folder"), ("group/r2", "repo"), ("group/r2/.git", "git"),
                               ("plain", "folder")})
        self.assertEqual(row(r, r1 / "node_modules")["parent"], str(r1))
        self.assertEqual(row(r, w1 / "build")["parent"], str(w1))
        self.assertEqual({x["owner"] for x in r["rows"]}, {"project_management"})

    def test_the_same_names_are_plain_folders_without_repos(self):
        a = self.tree()
        put(a / "node_modules" / "f", 1)
        put(a / "x" / ".git" / "HEAD", 1)
        r = run([place(a, depth=1)])
        self.assertEqual(sorted(x["kind"] for x in r["rows"]), ["folder", "folder", "place"])

    def test_the_time_limit_stops_the_walk_and_keeps_what_it_has(self):
        p1, p2 = self.tree("p1"), self.tree("p2")
        put(p1 / "s1" / "f", 1)
        put(p1 / "s2" / "f", 1)
        put(p2 / "f", 1)
        calls, real = [], os.scandir

        def slow(path):
            calls.append(path)
            if len(calls) == 2:
                time.sleep(0.4)
            return real(path)
        with mock.patch.object(W, "scandir", slow):
            r = W.measure([place(p1, "one"), place(p2, "two")], W.Guard([]), time.monotonic() + 0.2, {})
        self.assertIs(r["complete"], False)
        self.assertEqual(r["note"], "time limit")
        self.assertEqual(row(r, p1)["state"], "partial")
        self.assertEqual(row(r, p1 / "s1")["files"], 1)
        self.assertEqual(row(r, p2)["state"], "not_reached")
        self.assertEqual(row(r, p2)["bytes"], 0)
        self.assertIn({"path": str(p1), "reason": "time limit"}, r["skipped"])
        self.assertEqual(len(calls), 2)

    def test_a_folder_that_vanishes_is_not_an_error(self):
        a = self.tree()
        put(a / "gone" / "f", 1)
        real = os.scandir

        def fake(path):
            if str(path) == str(a / "gone"):
                raise FileNotFoundError(path)
            return real(path)
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a)])
        self.assertEqual(r["skipped"], [])
        self.assertIs(r["complete"], True)
        self.assertEqual(row(r, a)["state"], "ok")

    def test_a_very_deep_tree_needs_no_recursion(self):
        a = self.tree()
        cur = a
        for _ in range(400):
            cur = cur / "d"
            os.mkdir(cur)
        put(cur / "f", 1)
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(len(inspect.stack()) + 150)
        try:
            r = run([place(a)])
        finally:
            sys.setrecursionlimit(old)
        self.assertEqual(row(r, a)["state"], "ok")
        self.assertEqual(row(r, a)["files"], 1)

    def test_more_skips_than_the_cap_are_counted_not_listed(self):
        a = self.tree()
        for n in "123":
            put(a / n / "f", 1)
            os.chmod(a / n, 0)
        with mock.patch.object(W, "MAX_SKIPPED", 2):
            r = run([place(a)])
        self.assertEqual(len(r["skipped"]), 2)
        self.assertEqual(r["skipped_count"], 3)

    def test_progress_is_reported(self):
        a = self.tree()
        put(a / "f1", 1)
        put(a / "s" / "f2", 1)
        put(a / "s" / "f3", 1)
        p = {}
        W.measure([place(a)], W.Guard([]), time.monotonic() + 30, p)
        self.assertEqual(p, {"place": "t", "files": 3})


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = Path(self.tmp)
        self.core = Core(home, home / ".claude", COMPS)
        self.assertIsNone(self.core.error)
        self.db = lambda: self.core.ctx("memory_management", {}).db()

    def row(self, path, parent=None, n=1):
        return {"path": path, "parent": parent, "place": "t", "kind": "folder", "owner": "none", "state": "ok",
                "bytes": n, "logical_bytes": n + 1, "linked_bytes": 0, "files": n, "changed_at": "2026-01-01"}

    def result(self, *paths):
        rows = [self.row(p, None if i == 0 else paths[0], i + 1) for i, p in enumerate(paths)]
        return {"rows": rows, "skipped": [{"path": "/s", "reason": "guarded"}], "skipped_count": 5, "files": 7,
                "complete": False, "note": "time limit"}

    def run_(self, ts=1000.5, disk=None):
        return {"started_at": "a", "finished_at": "b", "finished_ts": ts, "seconds": 2.5, "disk": disk}

    def save(self, run, result):
        with closing(self.db()) as db, db:
            R.save(db, run, result)

    def last(self):
        with closing(self.db()) as db:
            return R.last(db)

    def test_the_component_creates_its_three_tables(self):
        with closing(self.db()) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'memory_management_%'")}
        self.assertEqual(names, {"memory_management_runs", "memory_management_folders", "memory_management_skipped"})
        self.assertEqual([c.error for c in self.core.components if c.name == "memory_management"], [None])

    def test_nothing_saved_gives_none(self):
        with closing(self.db()) as db:
            self.assertIsNone(R.last(db))
            self.assertIsNone(R.last_finished_ts(db))

    def test_save_then_last_gives_the_same_measurement(self):
        result = self.result("/p", "/p/a")
        self.save(self.run_(disk={"path": "/x", "total": 100, "free": 40}), result)
        got = self.last()
        self.assertEqual(got["rows"], result["rows"])
        self.assertEqual(got["skipped"], result["skipped"])
        self.assertEqual(got["run"], {"started_at": "a", "finished_at": "b", "seconds": 2.5, "complete": False,
                                      "note": "time limit", "disk": {"path": "/x", "total": 100, "free": 40},
                                      "files": 7, "skipped": 5})
        with closing(self.db()) as db:
            self.assertEqual(R.last_finished_ts(db), 1000.5)

    def test_a_run_without_disk_numbers(self):
        self.save(self.run_(disk=None), self.result("/p"))
        self.assertIsNone(self.last()["run"]["disk"])

    def test_only_the_last_runs_folders_are_kept_but_every_run_row(self):
        self.save(self.run_(1.0), self.result("/p", "/p/a"))
        second = self.result("/q")
        self.save(self.run_(2.0), second)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM memory_management_runs").fetchone()[0], 2)
            self.assertEqual([r[0] for r in db.execute("SELECT path FROM memory_management_folders")], ["/q"])
            self.assertEqual(db.execute("SELECT count(*) FROM memory_management_skipped").fetchone()[0], 1)
        self.assertEqual(self.last()["rows"], second["rows"])

    def test_a_failed_save_writes_nothing(self):
        result = self.result("/p", "/p/a")
        del result["rows"][1]["bytes"]
        with self.assertRaises(KeyError):
            self.save(self.run_(), result)
        self.assertIsNone(self.last())


if __name__ == "__main__":
    unittest.main()
