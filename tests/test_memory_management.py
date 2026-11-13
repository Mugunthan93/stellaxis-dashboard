"""Tests of the memory_management component (collector/components/memory_management).

  python3 -m unittest tests.test_memory_management
"""
import ast
import contextlib
import http.client
import inspect
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
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


class Listing:      # what walk.scandir returns: a context that gives the entries
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *a):
        return False


class FakeEntry:
    def __init__(self, entry, **st):
        self.name, self.path = entry.name, entry.path
        real = entry.stat(follow_symlinks=False)
        self.st = types.SimpleNamespace(**dict({k: getattr(real, k) for k in (
            "st_mode", "st_dev", "st_ino", "st_mtime", "st_blocks", "st_nlink", "st_size")}, **st))

    def stat(self, follow_symlinks=True):
        return self.st


def case_sensitive(path):
    return not os.path.exists(str(path).swapcase())


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
        self.assertEqual(r["skipped_count"], 2)
        self.assertIs(r["complete"], True)
        self.assertEqual({x["state"] for x in r["rows"]}, {"ok"})

    def test_a_guarded_sub_folder_in_another_case_is_not_opened(self):
        a = self.tree()
        if case_sensitive(a):
            self.skipTest("case-sensitive disk")
        put(a / "documents" / "secret", 1)
        calls, fake = recorder()
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a)], W.Guard([str(a / "Documents")]))
        self.assertNotIn(str(a / "documents"), calls)
        self.assertEqual(r["skipped"], [{"path": str(a / "documents"), "reason": "guarded by macOS"}])

    def test_a_guarded_folder_under_another_spelling_is_not_opened(self):
        a = self.tree()
        put(a / "Documents" / "secret", 1)
        put(a / "Library" / "Caches" / "com.apple.x" / "f", 1)
        put(a / "ok" / "f", 1)
        alias = self.tmp / "alias"
        os.symlink(a, alias)
        guard = W.Guard([str(alias / "Documents"), str(alias / "Library" / "Caches" / "com.apple.*")])
        calls, fake = recorder()
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a, depth=3)], guard)
        self.assertNotIn(str(a / "Documents"), calls)
        self.assertNotIn(str(a / "Library" / "Caches" / "com.apple.x"), calls)
        self.assertIn(str(a / "ok"), calls)
        for p in (a / "Documents", a / "Library" / "Caches" / "com.apple.x"):
            self.assertIn({"path": str(p), "reason": "guarded by macOS"}, r["skipped"])

    def test_a_place_inside_a_guarded_folder_under_another_spelling_is_refused(self):
        a = self.tree()
        put(a / "Documents" / "x" / "f", 1)
        alias = self.tmp / "alias"
        os.symlink(a, alias)
        calls, fake = recorder()
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a / "Documents" / "x")], W.Guard([str(alias / "Documents")]))
        self.assertEqual(calls, [])
        self.assertEqual([x["state"] for x in r["rows"]], ["refused"])
        self.assertEqual(r["skipped"], [{"path": str(a / "Documents" / "x"), "reason": "guarded by macOS"}])

    def test_a_folder_swapped_for_a_link_during_the_walk_is_not_opened(self):
        a, other = self.tree(), self.tree("other")
        put(other / "big", 50000)
        put(a / "sub" / "f", 10)
        real = os.scandir

        def swap(path):
            with real(path) as it:
                entries = list(it)
            if str(path) == str(a):
                for e in entries:
                    e.stat(follow_symlinks=False)     # the listing has seen sub as a folder
                shutil.rmtree(a / "sub")
                os.symlink(other, a / "sub")
            return Listing(entries)
        with mock.patch.object(W, "scandir", swap):
            r = run([place(a)])
        self.assertEqual(row(r, a)["logical_bytes"], 0)
        self.assertEqual(row(r, a)["state"], "partial")
        self.assertIn({"path": str(a / "sub"), "reason": "changed during the walk"}, r["skipped"])
        self.assertIs(r["complete"], False)

    def test_another_volume_is_not_entered(self):
        a = self.tree()
        put(a / "mnt" / "f", 1)
        put(a / "ok" / "f", 1)
        calls, real = [], os.scandir

        def fake(path):
            calls.append(str(path))
            with real(path) as it:
                entries = [FakeEntry(e, st_dev=os.lstat(a).st_dev + 1) if e.name == "mnt" else e for e in it]
            return Listing(entries)
        with mock.patch.object(W, "scandir", fake):
            r = run([place(a)])
        self.assertNotIn(str(a / "mnt"), calls)
        self.assertIn(str(a / "ok"), calls)
        self.assertIn({"path": str(a / "mnt"), "reason": "another volume"}, r["skipped"])
        self.assertEqual(row(r, a)["state"], "partial")
        self.assertIs(r["complete"], False)

    def test_one_place_that_fails_does_not_lose_the_others(self):
        p1, p2 = self.tree("p1"), self.tree("p2")
        put(p1 / "f", 10)
        put(p2 / "f", 20)
        real = os.scandir

        def fake(path):
            if str(path) == str(p1):
                raise ValueError("boom")
            return real(path)
        with mock.patch.object(W, "scandir", fake):
            r = run([place(p1, "one"), place(p2, "two")])
        bad = row(r, p1)
        self.assertEqual((bad["state"], bad["bytes"], bad["logical_bytes"], bad["files"]), ("failed", 0, 0, 0))
        self.assertEqual([x for x in r["rows"] if x["place"] == "one"], [bad])
        self.assertEqual((row(r, p2)["state"], row(r, p2)["logical_bytes"]), ("ok", 20))
        self.assertEqual(r["skipped"], [{"path": str(p1), "reason": "ValueError: boom"}])
        self.assertIs(r["complete"], False)

    def test_an_odd_change_time_gives_no_date(self):
        n = types.SimpleNamespace(bytes=1, logical=1, linked=0, files=1, changed=1e300)
        self.assertIsNone(W._row("/x", None, place("/x"), "folder", "ok", n)["changed_at"])

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

    def test_the_overlap_check_ignores_case(self):
        a = self.tree()
        if case_sensitive(a):
            self.skipTest("case-sensitive disk")
        (a / "proj" / "Sub").mkdir(parents=True)
        r = run([place(a / "proj", "one"), place(a / "PROJ" / "Sub", "two")])
        self.assertEqual([x["state"] for x in r["rows"] if x["kind"] == "place"], ["ok", "refused"])
        self.assertEqual(r["skipped"], [{"path": str(a / "PROJ" / "Sub"), "reason": "overlaps one"}])

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

    def test_last_reads_in_one_transaction(self):
        self.save(self.run_(), self.result("/p", "/p/a"))

        class Spy:
            def __init__(self, db):
                self.db, self.sql = db, []

            def execute(self, sql, *a):
                self.sql.append(sql.split()[0])
                return self.db.execute(sql, *a)

            def rollback(self):
                self.sql.append("ROLLBACK")
                self.db.rollback()
        with closing(self.db()) as db:
            spy = Spy(db)
            self.assertEqual(R.last(spy)["rows"][1]["path"], "/p/a")
            self.assertFalse(db.in_transaction)
        self.assertEqual(spy.sql, ["BEGIN", "SELECT", "SELECT", "SELECT", "ROLLBACK"])

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


ISO = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"
IDLE = ("no places to measure: the Stellaxis data folder is not .stellaxis in the account's home folder, "
        "and config names no places")
ALL_OFF = "no places to measure: config turned every place off"
KEYS = {"state", "measured_at", "started_at", "seconds", "complete", "note", "measuring", "disk", "places", "folders",
        "elsewhere_bytes", "not_measured", "not_measured_count", "idle", "error"}


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def inside(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(MM, "account_home", return_value=self.home)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_defaults_are_off_when_the_data_folder_is_outside_the_home_folder(self):
        s = MM.settings({}, self.home / "stx")
        self.assertEqual(s["places"], [])
        self.assertEqual(s["idle"], IDLE)
        self.assertEqual((s["every"], s["max_seconds"]), (21600, 1800))

    def test_defaults_are_off_anywhere_else_inside_the_home_folder(self):
        self.inside()
        for out in (self.home / "sub" / ".stellaxis", self.home / "stx", self.home):
            s = MM.settings({}, out)
            self.assertEqual((s["places"], s["idle"]), ([], IDLE))

    def test_every_place_turned_off_has_its_own_sentence(self):
        self.inside()
        cfg = {"memory": {"places": [{"name": p[0], "off": True} for p in MM.DEFAULT_PLACES]}}
        s = MM.settings(cfg, self.home / ".stellaxis")
        self.assertEqual((s["places"], s["idle"]), ([], ALL_OFF))

    def test_defaults_are_on_inside_the_home_folder(self):
        self.inside()
        s = MM.settings({}, self.home / ".stellaxis")
        names = [p["name"] for p in s["places"]]
        self.assertEqual(names[:4], ["code", "claude", "claude_app", "stellaxis"])
        self.assertEqual(names[names.index("homebrew") + 1], "applications")
        self.assertEqual(s["places"][names.index("applications")], {
            "name": "applications", "path": "/Applications", "owner": "none", "depth": 1, "repos": False})
        self.assertEqual(s["places"][0], {"name": "code", "path": str(self.home / "code"),
                                          "owner": "project_management", "depth": 1, "repos": True})
        self.assertEqual(s["places"][1]["path"], str(self.home / ".claude"))
        there = MM.settings({}, self.home / ".stellaxis", self.home / "cfg")    # the core says where Claude's folder is
        self.assertEqual(there["places"][1]["path"], str(self.home / "cfg"))
        self.assertEqual(s["places"][3]["path"], str(self.home / ".stellaxis"))
        self.assertIsNone(s["idle"])
        for p in (self.home / "Documents", self.home / "Library" / "Caches" / "com.apple.Safari", "/Volumes"):
            self.assertTrue(W.Guard(s["guarded"]).hit(str(p)))

    def test_config_places_add_replace_and_remove(self):
        self.inside()
        cfg = {"memory": {"places": [
            {"name": "chrome", "path": "~/Library/Application Support/Google"},
            {"name": "code", "path": "~/work", "depth": 2},
            {"name": "homebrew", "off": True}], "guarded": ["~/Private"]}}
        s = MM.settings(cfg, self.home / ".stellaxis")
        names = [p["name"] for p in s["places"]]
        self.assertEqual(names[0], "code")
        self.assertEqual(s["places"][0], {"name": "code", "path": str(self.home / "work"),
                                          "owner": "project_management", "depth": 2, "repos": True})
        self.assertEqual(s["places"][-1], {"name": "chrome", "path": str(self.home / "Library/Application Support/Google"),
                                           "owner": "none", "depth": 1, "repos": False})
        self.assertNotIn("homebrew", names)
        self.assertTrue(W.Guard(s["guarded"]).hit(str(self.home / "Private")))

    def test_bad_config_falls_back(self):
        out = self.home / "stx"
        for cfg in ({"memory": 5}, {"memory": {"every": "soon", "max_seconds": [], "places": "x", "guarded": 7}}):
            s = MM.settings(cfg, out)
            self.assertEqual((s["every"], s["max_seconds"], s["places"]), (21600, 1800, []))
        s = MM.settings({"memory": {"places": [3, {"name": "a"}, {"path": "/x"},
                                               {"name": "ok", "path": str(self.home), "depth": "x"}]}}, out)
        self.assertEqual([(p["name"], p["depth"]) for p in s["places"]], [("ok", 1)])

    def test_a_config_path_must_be_absolute(self):
        self.inside()
        bad = ["", "code", "~bob/x", "./x"]
        cfg = {"memory": {"places": [{"name": f"bad{i}", "path": p} for i, p in enumerate(bad)]
                          + [{"name": "code", "path": "work"}, {"name": "ok", "path": "~/ok"}],
                          "guarded": bad + ["/abs"]}}
        s = MM.settings(cfg, self.home / ".stellaxis")
        by = {p["name"]: p["path"] for p in s["places"]}
        self.assertEqual(by["ok"], str(self.home / "ok"))
        self.assertEqual(by["code"], str(self.home / "code"))
        self.assertFalse([n for n in by if n.startswith("bad")])
        self.assertTrue(all(os.path.isabs(p) for p in s["guarded"]))
        self.assertIn("/abs", s["guarded"])

    def test_the_guard_holds_both_spellings_of_the_home_folder(self):
        alias = self.home / "alias"
        os.symlink(self.home, alias)
        with mock.patch.object(MM, "account_home", return_value=alias):
            paths = MM.settings({}, self.home / "stx")["guarded"]
        for h in (alias, self.home):
            self.assertTrue(W.Guard(paths).hit(str(h / "Documents")))
        self.assertEqual(len(paths), len(set(paths)))

    def test_a_fake_HOME_does_not_turn_the_defaults_on(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            s = MM.settings({}, self.home / ".stellaxis")
        self.assertEqual((s["places"], s["idle"]), ([], IDLE))

    def test_durations(self):
        for text, want in (("90s", 90), ("15m", 900), ("6h", 21600), ("2d", 172800), (120, 120), ("x", 7), (None, 7)):
            self.assertEqual(MM.duration(text, 7), want)
        self.assertEqual(MM.settings({"memory": {"every": "1s"}}, self.home / "stx")["every"], 60)


CHILD_DIR = COMPS / "memory_management"
# The real child, with a walker that waits until a gate file exists. argv: the component folder, the gate file.
GATED = """import os, sys, time
sys.path.insert(0, sys.argv[1])
import walk
real = walk.scandir
def slow(p):
    while not os.path.exists(sys.argv[2]):
        time.sleep(0.02)
    return real(p)
walk.scandir = slow
walk.TICK = 0.05
walk.main()
"""
# A process that plays the service: one pass that starts a measurement with a gated child, then it says the child's pid.
STARTER = """import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from core import component
from core.app import Core
comps, home = Path(sys.argv[1]) / "components", Path(sys.argv[2])
MM = component.import_folder(comps / "memory_management")
MM.CHILD = [sys.executable, sys.argv[4], str(comps / "memory_management"), sys.argv[5]]
core = Core(home, home / ".claude", comps)
core.service = True
core.run_pass({"memory": {"places": [{"name": "t", "path": sys.argv[3]}]}})
job = MM._JOBS[str(home)]
while job["pid"] is None:
    time.sleep(0.01)
print(job["pid"], flush=True)
time.sleep(60)
"""


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def reap(proc):
    """End a process a test started and close its pipes."""
    proc.kill()
    proc.wait()
    for f in (proc.stdin, proc.stdout, proc.stderr):
        if f:
            f.close()


def until(test, seconds=10):
    end = time.monotonic() + seconds
    while not test() and time.monotonic() < end:
        time.sleep(0.02)
    return test()


class ChildTest(unittest.TestCase):
    """The measuring process itself (walk.py run as a script), run the way the component runs it."""

    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        put(self.base / "tree" / "f1", 1000)
        put(self.base / "tree" / "sub" / "f2", 5000)
        self.places = [place(self.base / "tree")]

    def child(self, python=sys.executable):
        proc = subprocess.Popen([python, str(CHILD_DIR / "walk.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.addCleanup(reap, proc)
        return proc

    def test_it_measures_what_it_is_told_and_says_the_same_as_the_walker(self):
        pythons = [sys.executable] + (["/usr/bin/python3"] if os.path.exists("/usr/bin/python3") else [])
        for python in pythons:
            with self.subTest(python=python):
                proc = self.child(python)
                proc.stdin.write((json.dumps({"places": self.places, "guarded": [], "max_seconds": 30}) + "\n").encode())
                proc.stdin.flush()
                said = json.loads(proc.stdout.readline())
                while "progress" in said:
                    said = json.loads(proc.stdout.readline())
                self.assertEqual(said, {"result": run(self.places)})
                proc.stdin.close()
                self.assertEqual((proc.wait(10), proc.stderr.read()), (0, b""))

    def test_a_bad_order_is_answered_with_an_error(self):
        for order in (b"not json\n", b'{"places": 5, "guarded": [], "max_seconds": 5}\n', b"{}\n"):
            proc = self.child()
            proc.stdin.write(order)
            proc.stdin.flush()
            said = json.loads(proc.stdout.readline())
            self.assertEqual(list(said), ["error"])
            self.assertRegex(said["error"], r"^\w+Error: ")
            proc.stdin.close()
            self.assertEqual((proc.wait(10), proc.stderr.read()), (0, b""))

    def test_it_ends_when_its_input_closes_before_or_during_a_walk(self):
        proc = self.child()
        proc.stdin.close()      # the starter went away before it said anything
        self.assertEqual((proc.wait(10), proc.stdout.read(), proc.stderr.read()), (0, b"", b""))
        gated = self.base / "gated.py"
        gated.write_text(GATED)
        proc = subprocess.Popen([sys.executable, str(gated), str(CHILD_DIR), str(self.base / "never")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(reap, proc)
        proc.stdin.write((json.dumps({"places": self.places, "guarded": [], "max_seconds": 30}) + "\n").encode())
        proc.stdin.flush()
        self.assertIn("progress", json.loads(proc.stdout.readline()))      # it is inside the walk now
        self.assertIsNone(proc.poll())
        proc.stdin.close()
        self.assertEqual((proc.wait(10), proc.stderr.read()), (0, b""))

    def test_it_runs_at_low_priority(self):
        code = ("import os, sys; sys.path.insert(0, sys.argv[1]); import walk; walk.lower(); "
                "print(os.getpriority(os.PRIO_DARWIN_PROCESS, 0) if hasattr(os, 'PRIO_DARWIN_BG') else os.nice(0))")
        got = subprocess.run([sys.executable, "-c", code, str(CHILD_DIR)], capture_output=True, text=True)
        self.assertEqual((got.stdout.strip(), got.stderr), ("1" if hasattr(os, "PRIO_DARWIN_BG") else "10", ""))


class ComponentTest(unittest.TestCase):
    def setUp(self):
        base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.base = base
        self.home = base / "stx"
        self.home.mkdir()
        self.tree = base / "tree"
        put(self.tree / "f1", 1000)
        put(self.tree / "sub" / "f2", 5000)
        self.cfg = {"memory": {"places": [{"name": "t", "path": str(self.tree)}]}}
        self.core = Core(self.home, self.home / ".claude", COMPS)
        self.core.service = True     # only the service measures
        self.addCleanup(MM._JOBS.pop, str(self.home), None)
        self.addCleanup(self.join)
        self.srv = None

    def job(self):
        return MM._JOBS[str(self.home)]

    def join(self):
        job = MM._JOBS.get(str(self.home))
        if job and job["thread"]:
            job["thread"].join(20)

    def wait(self):
        self.job()["thread"].join(20)
        self.assertFalse(self.job()["thread"].is_alive())

    def child(self, *command):
        patch = mock.patch.object(MM, "CHILD", [sys.executable, *map(str, command)])
        patch.start()
        self.addCleanup(patch.stop)

    def gate(self):
        """The real child, but its walker waits until the gate is opened. Returns the function that opens it."""
        script, gate = self.base / "gated.py", self.base / "open"
        script.write_text(GATED)
        self.child(script, CHILD_DIR, gate)
        self.addCleanup(gate.touch)    # runs before the join in setUp
        return gate.touch

    def fake(self, code):
        """A child that is not the real one: it does what `code` says."""
        script = self.base / "fake.py"
        script.write_text(code)
        self.child(script)

    def pid(self):
        self.assertTrue(until(lambda: self.job()["pid"] is not None))
        return self.job()["pid"]

    def serve(self, core):
        srv = server.serve(core, 0, self.cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def get(self, path, method="GET", core=None):
        if core is not None or self.srv is None:
            srv = self.serve(core or self.core)
            self.srv = srv if core is None else self.srv
        else:
            srv = self.srv
        with closing(http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)) as conn:
            conn.request(method, path)
            r = conn.getresponse()
            body = r.read()
            return r.status, json.loads(body) if r.getheader("Content-Type") == "application/json" else body

    def starts(self):
        """A list that grows by one each time a measurement starts."""
        starts, real = [], MM._watch
        patch = mock.patch.object(MM, "_watch", lambda *a: (starts.append(1), real(*a))[1])
        patch.start()
        self.addCleanup(patch.stop)
        return starts

    def runs(self):
        with closing(self.core.ctx("memory_management", {}).db()) as db:
            return db.execute("SELECT count(*) FROM memory_management_runs").fetchone()[0]

    def a_pass(self, cfg=None):
        return self.core.run_pass(dict(self.cfg if cfg is None else cfg))

    def test_the_component_loads_in_a_real_core_and_api_core_lists_it(self):
        want = {"name": "memory_management", "readers": 1, "actions": [], "offers": [], "reads": [], "error": None}
        self.assertIn(want, self.core.describe()["components"])
        status, body = self.get("/api/core")
        self.assertEqual(status, 200)
        self.assertIn(want, body["components"])
        self.assertIsNone(self.core.error)

    def test_a_pass_measures_in_a_child_process_and_the_route_shows_it(self):
        with mock.patch.object(W, "measure", side_effect=AssertionError("the walk ran inside this process")):
            res = self.a_pass()
            self.assertEqual(res["components"]["memory_management"], {"readers": 1, "errors": []})
            self.wait()
        self.assertNotIn(self.job()["pid"], (None, os.getpid()))
        self.assertFalse(alive(self.job()["pid"]))
        status, a = self.get("/api/memory")
        self.assertEqual((status, a["state"], a["complete"]), (200, "measured", True))
        [p] = a["places"]
        self.assertEqual((p["path"], p["logical_bytes"], p["files"], p["state"]), (str(self.tree), 6000, 2, "ok"))
        self.assertEqual([f["path"] for f in a["folders"]], [str(self.tree / "sub")])
        self.assertIsNone(a["measuring"])
        self.assertIsNone(a["error"])
        self.assertIsNone(a["idle"])
        self.assertEqual(a["disk"]["used"], a["disk"]["total"] - a["disk"]["free"])
        self.assertEqual(a["elsewhere_bytes"], a["disk"]["used"] - p["bytes"])
        self.assertEqual(a["not_measured"], [])
        self.assertRegex(a["measured_at"], ISO)
        self.assertEqual(set(a), KEYS)

    def test_a_slow_walk_does_not_block_the_pass_and_the_route_answers_before_it_is_ready(self):
        open_gate = self.gate()
        t = time.monotonic()
        self.a_pass()
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertTrue(self.job()["thread"].is_alive())
        status, a = self.get("/api/memory")
        self.assertEqual((status, a["state"], a["measured_at"], a["places"]), (200, "never", None, []))
        self.assertRegex(a["measuring"]["started_at"], ISO)
        self.assertEqual(a["measuring"]["files"], 0)
        self.assertTrue(alive(self.pid()))
        for _ in range(20):     # passes stay fast for as long as the child walks
            t = time.monotonic()
            self.a_pass()
            self.assertLess(time.monotonic() - t, 0.5)
        self.assertTrue(until(lambda: self.job()["progress"].get("place") == "t"))     # the child's progress arrives
        self.assertEqual(self.get("/api/memory")[1]["measuring"]["place"], "t")
        open_gate()
        self.wait()
        self.assertEqual(self.get("/api/memory")[1]["state"], "measured")

    def test_a_second_pass_does_not_start_a_second_measurement(self):
        open_gate = self.gate()
        starts = self.starts()
        for _ in range(3):
            self.a_pass()
        self.assertEqual(len(starts), 1)
        open_gate()
        self.wait()
        self.a_pass()
        self.assertEqual(len(starts), 1)
        self.assertEqual(self.runs(), 1)

    def test_it_measures_again_only_when_the_last_one_is_older_than_every(self):
        self.a_pass()
        self.wait()
        self.job()["last_try"] -= 5 * 3600
        self.a_pass()
        self.assertFalse(self.job()["thread"].is_alive())
        self.assertEqual(self.runs(), 1)
        self.job()["last_try"] -= 2 * 3600
        self.a_pass()
        self.wait()
        self.assertEqual(self.runs(), 2)

    def test_a_new_process_reads_the_age_from_the_store(self):
        self.a_pass()
        self.wait()
        MM._JOBS.pop(str(self.home))
        self.a_pass()
        self.assertIsNone(self.job()["thread"])
        self.assertLess(abs(self.job()["last_try"] - time.time()), 60)

    def test_only_the_service_measures(self):
        self.core.service = False       # collect.py run, prune.py, the desktop MCP tool: any process but the service
        starts = self.starts()
        for _ in range(3):
            res = self.a_pass()
        self.assertEqual(res["components"]["memory_management"], {"readers": 1, "errors": []})
        self.assertEqual((starts, str(self.home) in MM._JOBS, self.runs()), ([], False, 0))
        self.core.service = True
        self.a_pass()
        self.assertEqual(len(starts), 1)
        self.wait()
        self.assertEqual((self.job()["error"], self.runs()), (None, 1))

    def test_no_places_means_no_measurement_and_the_route_says_why(self):
        self.cfg = {}
        self.a_pass()
        self.assertTrue(str(self.home) not in MM._JOBS or MM._JOBS[str(self.home)]["thread"] is None)
        status, a = self.get("/api/memory")
        self.assertEqual((status, a["state"], a["idle"], a["measuring"]), (200, "never", IDLE, None))

    def test_a_measurement_that_fails_is_reported_and_not_retried_at_once(self):
        self.fake('import sys, time\nsys.stdin.readline()\nprint(\'{"error": "RuntimeError: boom"}\', flush=True)\ntime.sleep(30)\n')
        self.a_pass()
        self.wait()
        self.assertFalse(alive(self.job()["pid"]))      # it said its error and was not left to sleep
        self.assertEqual(self.get("/api/memory")[1]["error"], "RuntimeError: boom")
        starts = self.starts()
        self.a_pass()
        self.a_pass()
        self.assertEqual(len(starts), 0)

    def test_a_child_that_hangs_is_stopped_at_the_time_limit_and_the_last_measurement_stays(self):
        self.a_pass()
        self.wait()
        before = self.get("/api/memory")[1]
        self.job()["last_try"] -= 7 * 3600
        self.gate()                     # never opened: the walker hangs in its first folder
        self.cfg = {"memory": {"places": self.cfg["memory"]["places"], "max_seconds": "1s"}}
        with mock.patch.object(MM, "GRACE", 0.3):
            t = time.monotonic()
            self.a_pass()
            pid = self.pid()
            self.assertTrue(alive(pid))
            self.wait()
        self.assertLess(time.monotonic() - t, 5)
        self.assertFalse(alive(pid))
        a = self.get("/api/memory")[1]
        self.assertEqual(a["error"], "stopped at the time limit: no answer after 1 s, last in t")
        self.assertIsNone(a["measuring"])
        self.assertEqual(self.runs(), 1)
        self.assertEqual({k: a[k] for k in a if k != "error"}, {k: before[k] for k in before if k != "error"})

    def test_the_child_stops_its_own_walk_at_the_time_limit_and_what_it_has_is_kept(self):
        put(self.tree / "sub2" / "f3", 10)
        open_gate = self.gate()
        self.cfg = {"memory": {"places": self.cfg["memory"]["places"], "max_seconds": "1s"}}
        self.a_pass()
        self.assertTrue(until(lambda: self.job()["progress"].get("place") == "t"))     # its clock runs: it is in the walk
        time.sleep(1.2)                 # past the limit, inside the grace: the walker is still in its first folder
        open_gate()
        self.wait()
        a = self.get("/api/memory")[1]
        self.assertEqual((a["state"], a["complete"], a["note"], a["error"]), ("measured", False, "time limit", None))
        self.assertEqual(a["places"][0]["state"], "partial")
        self.assertIn({"path": str(self.tree), "reason": "time limit"}, a["not_measured"])

    def test_a_child_that_dies_cannot_start_or_talks_nonsense_is_reported(self):
        wait_for_order = "import sys\nsys.stdin.readline()\n"
        for name, code, want in (
                ("dies", wait_for_order + "sys.exit(3)\n", r"^the measuring process ended with no result \(code 3\)$"),
                ("is killed", wait_for_order + "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
                 r"^the measuring process ended with no result \(code -9\)$"),
                ("talks nonsense", wait_for_order + "print('hello', flush=True)\n",
                 r"^the measuring process ended with no result \(code 0\): hello$"),
                ("says nothing useful", wait_for_order + "print('{}', flush=True)\n",
                 r"^the measuring process ended with no result \(code 0\): \{\}$"),
                ("crashes", wait_for_order + "raise ValueError('boom')\n",
                 r"^the measuring process ended with no result \(code 1\): ValueError: boom$"),
                ("ends before it reads", "import sys\nsys.exit(4)\n",
                 r"^the measuring process ended with no result \(code 4\)$"),
                ("cannot start", None, r"^FileNotFoundError: ")):
            with self.subTest(name):
                if code is None:
                    patch = mock.patch.object(MM, "CHILD", [str(self.base / "no-such-python")])
                    patch.start()
                    self.addCleanup(patch.stop)
                else:
                    self.fake(code)
                if str(self.home) in MM._JOBS:
                    self.job()["last_try"] -= 7 * 3600
                self.a_pass()
                self.wait()
                a = self.get("/api/memory")[1]
                self.assertRegex(a["error"], want)
                self.assertEqual((a["state"], a["measuring"], self.runs()), ("never", None, 0))

    def test_the_home_is_removed_while_it_measures(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            open_gate = self.gate()
            self.a_pass()
            self.pid()
            shutil.rmtree(self.home)
            open_gate()
            self.wait()
        self.assertFalse(self.home.exists())
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_the_route_with_the_store_down(self):
        broken = self.home.parent / "broken"
        (broken / "stellaxis.db").mkdir(parents=True)
        self.addCleanup(lambda: MM._JOBS.pop(str(broken), None))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            core = Core(broken, broken / ".claude", COMPS)
            core.service = True
            self.assertIsNotNone(core.error)
            status, body = self.get("/api/memory", core=core)
            starts = self.starts()
            for _ in range(2):
                res = core.run_pass(dict(self.cfg))
        self.assertEqual(status, 503)
        self.assertIsInstance(body["error"], str)
        [errors] = [res["components"]["memory_management"]["errors"]]
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("StoreError"))
        self.assertEqual(starts, [])
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_pass_with_a_fake_HOME_starts_no_thread(self):
        home = self.home.parent / ".stellaxis"
        home.mkdir()
        self.addCleanup(MM._JOBS.pop, str(home), None)
        core = Core(home, home / ".claude", COMPS)
        core.service = True
        with mock.patch.dict(os.environ, {"HOME": str(home.parent)}):
            for _ in range(2):
                core.run_pass({})
        self.assertTrue(str(home) not in MM._JOBS or MM._JOBS[str(home)]["thread"] is None)

    def test_a_thread_that_cannot_start_is_shown(self):
        class NoStart(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")
        with mock.patch.object(MM.threading, "Thread", NoStart):
            res = self.a_pass()
        self.assertEqual(res["components"]["memory_management"]["errors"], [])
        a = self.get("/api/memory")[1]
        self.assertEqual((a["error"], a["measuring"], a["state"]), ("RuntimeError: can't start new thread", None, "never"))
        starts = self.starts()
        self.a_pass()
        self.assertEqual(starts, [])

    def test_a_save_that_fails_is_reported(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(R, "save", side_effect=RuntimeError("disk full")):
            self.a_pass()
            self.wait()
        a = self.get("/api/memory")[1]
        self.assertEqual((a["error"], a["state"]), ("RuntimeError: disk full", "never"))
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_measurement_changes_nothing_on_disk(self):
        t, base = self.tree, self.home.parent
        put(t / "r" / ".git" / "HEAD", 1)
        put(t / "r" / "wt" / ".git", text="gitdir: /x/r/.git/worktrees/wt\n")
        os.symlink(t / "f1", t / "sub" / "link")
        os.link(t / "f1", t / "sub" / "hard")
        put(t / "locked" / "f", 1)
        os.chmod(t / "locked", 0)
        self.addCleanup(os.chmod, t / "locked", 0o700)
        self.cfg = {"memory": {"places": [{"name": "t", "path": str(t), "depth": 3, "repos": True}]}}

        def look():
            got = {}
            for d, dirs, files in os.walk(base):
                dirs[:] = [x for x in dirs if Path(d, x) != self.home]
                for name in dirs + files:
                    st = os.lstat(os.path.join(d, name))
                    got[os.path.join(d, name)] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino, st.st_nlink)
            return got
        before = look()
        self.a_pass()
        self.wait()
        self.assertEqual(look(), before)
        self.assertEqual(len(before), 12)      # the file in the locked folder cannot be listed
        a = self.get("/api/memory")[1]
        self.assertIn(("worktree", str(t / "r" / "wt")), [(f["kind"], f["path"]) for f in a["folders"]])
        self.assertIn({"path": str(t / "locked"), "reason": "permission denied"}, a["not_measured"])

    def test_other_paths_and_methods_are_not_ours(self):
        self.assertEqual(self.get("/api/memoryx")[0], 404)
        self.assertEqual(self.get("/api/memory", method="POST")[0], 404)

    def test_the_component_imports_only_the_core_and_itself(self):
        for f in CHILD_DIR.glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                names = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                         else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.level == 0
                         else [])
                for n in names:
                    self.assertNotIn(n, {"collect", "unplaced", "prune"}, f.name)
                    if hasattr(sys, "stdlib_module_names"):
                        self.assertTrue(n in sys.stdlib_module_names or n == "core", f"{f.name}: {n}")

    def test_no_code_changes_a_file(self):
        for f in CHILD_DIR.glob("*.py"):
            text = f.read_text().replace("os.open(", "").replace("subprocess.Popen(", "subprocess.P(")
            for bad in ("os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "shutil", "os.chmod", "open(",
                        ".write_text", ".unlink(", ".mkdir(", "os.truncate", "os.utime", "os.symlink",
                        "os.link(", "os.mkdir", "os.makedirs", ".touch(", ".rename(", ".replace(", "O_WRONLY",
                        "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "shell=", "os.system", "os.exec", "os.spawn",
                        "os.popen"):
                self.assertNotIn(bad, text, f.name)
            # one process is ever started: the component's own child, by the watcher
            self.assertEqual(text.count("subprocess."), 4 if f.name == "__init__.py" else 0, f.name)
        self.assertEqual(MM.CHILD, [sys.executable, "-I", str(CHILD_DIR / "walk.py")])


class StarterGoneTest(unittest.TestCase):
    def test_the_child_ends_when_the_service_stops_however_it_stops(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(sig.name):
                base = Path(os.path.realpath(tempfile.mkdtemp()))
                self.addCleanup(shutil.rmtree, base, ignore_errors=True)
                (base / "stx").mkdir()
                put(base / "tree" / "f", 10)
                (base / "gated.py").write_text(GATED)
                (base / "starter.py").write_text(STARTER)
                starter = subprocess.Popen(
                    [sys.executable, str(base / "starter.py"), str(REPO / "collector"), str(base / "stx"),
                     str(base / "tree"), str(base / "gated.py"), str(base / "never")],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.addCleanup(reap, starter)
                line = starter.stdout.readline()
                self.assertTrue(line.strip().isdigit(), starter.stderr.read() if not line else line)
                pid = int(line)
                self.assertTrue(alive(pid))
                starter.send_signal(sig)
                starter.wait(10)
                self.assertTrue(until(lambda: not alive(pid)), "the child outlived its starter")


if __name__ == "__main__":
    unittest.main()
