"""Tests of the core package (collector/core) and its component loader.

  python3 -m unittest discover -s tests -k test_core
"""
import ast
import contextlib
import datetime as dt
import http.client
import http.server
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
import collect as C
from core import component, store, undo, server, transcripts
from core.app import Core, StoreError


def put(root: Path, name: str, init: str, **files: str) -> None:
    """Write a component folder: <root>/<name>/__init__.py plus <key>.py for each extra file."""
    d = root / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(init)
    for stem, text in files.items():
        (d / f"{stem}.py").write_text(text)


class LoadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def test_no_component_may_be_named_core(self):
        put(self.comps, "core", "from core.component import Component\nCOMPONENT = Component(name='core')\n")
        [c] = component.load(self.comps)
        self.assertIn("core", c.error)

    def test_missing_or_empty_folder_gives_no_components(self):
        self.assertEqual(component.load(self.comps / "absent"), [])
        self.comps.mkdir()
        (self.comps / "__pycache__").mkdir()
        self.assertEqual(component.load(self.comps), [])

    def test_loads_components_in_name_order(self):
        for n in ("beta", "alpha"):
            put(self.comps, n, f"from core.component import Component\nCOMPONENT = Component(name='{n}')\n")
        a, b = component.load(self.comps)
        self.assertEqual((a.name, a.error, a.readers, a.actions, a.offers, a.reads, a.handle), ("alpha", None, [], {}, {}, [], None))
        self.assertEqual(b.name, "beta")

    def test_a_component_can_import_its_own_files(self):
        put(self.comps, "alpha", "from core.component import Component\nfrom .readers import r\nCOMPONENT = Component(name='alpha', readers=[r])\n",
            readers="def r(ctx):\n    pass\n")
        [c] = component.load(self.comps)
        self.assertEqual((c.error, len(c.readers)), (None, 1))

    def test_a_broken_component_is_reported_not_raised(self):
        ok = "from core.component import Component\nCOMPONENT = Component(name='{}')\n"
        put(self.comps, "beta", "raise RuntimeError('boom')\n")
        put(self.comps, "gamma", "COMPONENT = 1\n")
        put(self.comps, "delta", ok.format("other"))
        put(self.comps, "Bad-Name", ok.format("Bad-Name"))
        put(self.comps, "alpha", ok.format("alpha"))
        by = {c.name: c for c in component.load(self.comps)}
        self.assertEqual(set(by), {"alpha", "beta", "gamma", "delta", "Bad-Name"})
        self.assertIsNone(by["alpha"].error)
        self.assertIn("RuntimeError: boom", by["beta"].error)
        for n in ("gamma", "delta", "Bad-Name"):
            self.assertTrue(by[n].error)
            self.assertEqual((by[n].readers, by[n].actions, by[n].offers), ([], {}, {}))

    def test_a_component_folder_is_imported_once(self):
        put(self.comps, "alpha", "from core.component import Component\nSTATE = []\nCOMPONENT = Component(name='alpha')\n")
        m1 = component.import_folder(self.comps / "alpha")
        [c] = component.load(self.comps)
        m2 = component.import_folder(self.comps / "alpha")
        self.assertIs(m1, m2)
        self.assertIsNot(c, m1.COMPONENT)
        self.assertEqual(c.name, "alpha")
        self.assertIs(sys.modules["stx_alpha"], m1)

    def test_a_same_named_folder_elsewhere_replaces_it(self):
        other = self.home / "elsewhere"
        for root in (self.comps, other):
            put(root, "alpha", "from core.component import Component\nCOMPONENT = Component(name='alpha')\n")
        m1 = component.import_folder(self.comps / "alpha")
        m2 = component.import_folder(other / "alpha")
        self.assertIsNot(m1, m2)
        self.assertIs(sys.modules["stx_alpha"], m2)

    def test_a_failed_import_leaves_nothing_behind(self):
        put(self.comps, "beta", "from . import helper\nraise RuntimeError('boom')\n", helper="X = 1\n")
        [c] = component.load(self.comps)
        self.assertIn("RuntimeError: boom", c.error)
        self.assertEqual([k for k in sys.modules if k == "stx_beta" or k.startswith("stx_beta.")], [])

    def test_a_failed_import_can_be_fixed_and_loaded_again(self):
        put(self.comps, "beta", "raise RuntimeError('boom')\n")
        self.assertTrue(component.load(self.comps)[0].error)
        (self.comps / "beta" / "__init__.py").write_text("from core.component import Component\nCOMPONENT = Component(name='beta')\n")
        [c] = component.load(self.comps)
        self.assertIsNone(c.error)

    def test_threads_importing_one_folder_get_one_finished_module(self):
        put(self.comps, "slow", "import time\ntime.sleep(0.05)\nLOCK = object()\n")
        got = []
        def run():
            m = component.import_folder(self.comps / "slow")
            got.append((m, hasattr(m, "LOCK")))
        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(got), 8)
        self.assertTrue(all(m is got[0][0] and ok for m, ok in got))

    def test_an_undo_step_must_carry_the_component_name(self):
        put(self.comps, "alpha", "from core.component import Component\nCOMPONENT = Component(name='alpha', undo={'alpha.back': print})\n")
        put(self.comps, "beta", "from core.component import Component\nCOMPONENT = Component(name='beta', undo={'back': print})\n")
        a, b = component.load(self.comps)
        self.assertEqual((a.error, list(a.undo)), (None, ["alpha.back"]))
        self.assertIn("ValueError", b.error)
        self.assertIn("beta.", b.error)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def q(self, s, sql):
        with closing(s.connect()) as db:
            return db.execute(sql).fetchall()

    def test_a_new_store_is_an_empty_database(self):
        s = store.Store(self.home / "deep" / "stellaxis.db")
        self.assertTrue(s.path.is_file())
        self.assertEqual(self.q(s, "SELECT count(*) FROM sqlite_master")[0][0], 0)

    def test_ensure_creates_only_the_components_own_tables(self):
        s = store.Store(self.home / "stellaxis.db")
        own = ["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)",
               "create index if not exists alpha_things_i ON alpha_things (id)"]
        s.ensure("alpha", own)
        s.ensure("alpha", own)
        with closing(s.connect()) as db, db:
            db.execute("INSERT INTO alpha_things VALUES ('a')")
        self.assertEqual(self.q(s, "SELECT id FROM alpha_things")[0]["id"], "a")
        for bad in ("CREATE TABLE IF NOT EXISTS beta_things (id TEXT)", "CREATE TABLE alpha_more (id TEXT)",
                    "DROP TABLE alpha_things", "CREATE TABLE IF NOT EXISTS alphathings (id TEXT)"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_ok (id TEXT)", bad])
        names = {r["name"] for r in self.q(s, "SELECT name FROM sqlite_master")}
        self.assertNotIn("alpha_ok", names)   # a rejected list creates nothing

    V1 = ["CREATE TABLE IF NOT EXISTS alpha_t (id TEXT)"]
    V2 = ["CREATE TABLE IF NOT EXISTS alpha_t (id TEXT, more TEXT)"]
    ADD = [["ALTER TABLE alpha_t ADD COLUMN more TEXT"]]

    def cols(self, s):
        with closing(s.connect()) as db:
            return [r[1] for r in db.execute("PRAGMA table_info(alpha_t)")]

    def version(self, s, name="alpha"):
        with closing(s.connect()) as db:
            row = db.execute("SELECT version FROM core_schema WHERE component = ?", (name,)).fetchone()
            return row and row[0]

    def test_a_new_store_gets_the_present_shape_and_no_change_is_run(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual((self.cols(s), self.version(s)), (["id", "more"], 1))

    def test_an_older_store_gets_each_change_one_time(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", self.V1)
        self.assertEqual(self.version(s), 0)
        s.ensure("alpha", self.V2, self.ADD)
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual((self.cols(s), self.version(s)), (["id", "more"], 1))

    def test_a_store_from_before_the_rule_counts_as_version_zero(self):
        s = store.Store(self.home / "stellaxis.db")
        with closing(s.connect()) as db, db:
            db.execute(self.V1[0])
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual(self.cols(s), ["id", "more"])

    def test_a_change_that_fails_leaves_the_store_as_it_was(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", self.V1)
        with self.assertRaises(sqlite3.Error):
            s.ensure("alpha", self.V2, [["ALTER TABLE alpha_t ADD COLUMN more TEXT", "ALTER TABLE alpha_t ADD COLUMN more TEXT"]])
        self.assertEqual((self.cols(s), self.version(s)), (["id"], 0))

    def test_a_change_may_touch_only_its_own_tables(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("beta", ["CREATE TABLE IF NOT EXISTS beta_t (id TEXT)"])
        for bad in ("DROP TABLE IF EXISTS beta_t", "ALTER TABLE beta_t ADD COLUMN x", "DELETE FROM core_schema", "PRAGMA writable_schema = 1"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", self.V1, [[bad]])
        self.assertIsNone(self.version(s, "alpha"))

    def test_a_change_may_not_rename_a_table_out_of_its_names(self):
        s = store.Store(self.home / "stellaxis.db")
        with self.assertRaises(ValueError):
            s.ensure("alpha", self.V1, [["ALTER TABLE alpha_t RENAME TO zzz"]])
        s.ensure("alpha", self.V1, [["ALTER TABLE alpha_t RENAME TO alpha_u"], ["ALTER TABLE alpha_u RENAME COLUMN id TO key"]])

    def test_the_name_check_cases(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("beta", ["CREATE TABLE IF NOT EXISTS beta_t (id TEXT)"])
        s.ensure("alpha", self.V1, [["\n  alter table alpha_t add column more text;  "]])
        for bad in ('ALTER TABLE "beta_t" ADD COLUMN x', 'ALTER TABLE "alpha_t" ADD COLUMN x',
                    "DELETE FROM alpha_t; DROP TABLE beta_t", "CREATE INDEX IF NOT EXISTS alpha_i ON beta_t (id)"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", self.V1, [[bad]])
        for bad in ("CREATE TABLE IF NOT EXISTS alpha_a (id TEXT); CREATE TABLE IF NOT EXISTS alpha_b (id TEXT)",
                    "CREATE INDEX IF NOT EXISTS alpha_i ON beta_t (id)"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", [bad])

    def test_an_older_code_does_not_lower_the_version(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", self.V2, self.ADD + [["UPDATE alpha_t SET more = 'x'"]])
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual(self.version(s), 2)

    def test_two_threads_use_the_store_at_once(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_n (n INTEGER)"])
        errors = []

        def write():
            try:
                for n in range(200):
                    with closing(s.connect()) as db, db:
                        db.execute("INSERT INTO alpha_n VALUES (?)", (n,))
            except Exception as e:
                errors.append(e)
        t = threading.Thread(target=write)
        t.start()
        for _ in range(200):
            self.q(s, "SELECT count(*) FROM alpha_n")
        t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.q(s, "SELECT count(*) FROM alpha_n")[0][0], 200)


ALPHA = '''
from contextlib import closing
from core.component import Component
def fill(ctx):
    with closing(ctx.db()) as db, db:
        db.execute("INSERT OR REPLACE INTO alpha_things VALUES (?)", (str(ctx.cfg.get("x")),))
def things(ctx, prefix=""):
    with closing(ctx.db()) as db:
        return [dict(r) for r in db.execute("SELECT id FROM alpha_things") if r["id"].startswith(prefix)]
COMPONENT = Component(name="alpha", records=["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)"],
                      readers=[fill], offers={"things": things})
'''
BETA = '''
from contextlib import closing
from core.component import Component
def bad(ctx):
    raise ValueError("bad")
def good(ctx):
    with closing(ctx.db()) as db, db:
        db.execute("INSERT INTO beta_seen VALUES (1)")
COMPONENT = Component(name="beta", records=["CREATE TABLE IF NOT EXISTS beta_seen (n INTEGER)"],
                      readers=[bad, good], reads=[("alpha", "things"), ("ghost", "things"), ("broken", "things")])
'''
SEES = '''
from core.component import Component
SEEN = []
def look(ctx):
    SEEN.append(ctx.inputs)
COMPONENT = Component(name="sees", readers=[look])
'''


class CoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def core(self):
        return Core(self.home, self.home / ".claude", self.comps)

    def test_a_component_sees_the_folders_and_the_service_flag(self):
        put(self.comps, "alpha", "from core.component import Component\nSEEN = []\n"
            "def r(ctx):\n    SEEN.append((ctx.home, ctx.claude_dir, ctx.claude_json, ctx.service))\n    ctx.status('working', force=False, path='x')\n"
            "COMPONENT = Component(name='alpha', readers=[r])\n")
        core = Core(self.home, self.home / ".claude", self.comps)
        said = []
        core.on_status = lambda phase, force=True, **detail: said.append((phase, force, detail))
        core.run_pass({})
        core.service = True
        core.run_pass({})
        seen = sys.modules["stx_alpha"].SEEN
        self.assertEqual(seen[0], (self.home, self.home / ".claude", self.home / ".claude" / ".claude.json", False))
        self.assertTrue(seen[1][3])
        self.assertEqual(said[0], ("working", False, {"path": "x"}))
        self.assertEqual(Core(self.home, self.home / ".claude", self.comps, claude_json=self.home / "cj").claude_json, self.home / "cj")

    def test_an_offer_that_raises_gives_nothing_and_is_reported(self):
        put(self.comps, "alpha", "from core.component import Component\nBAD = [True]\n"
            "def o(ctx):\n    if BAD[0]:\n        raise ValueError('boom')\n    return [1]\n"
            "COMPONENT = Component(name='alpha', offers={'o': o})\n")
        put(self.comps, "beta", "from core.component import Component\nGOT = []\n"
            "def r(ctx):\n    GOT.append(ctx.read('alpha', 'o'))\n"
            "COMPONENT = Component(name='beta', readers=[r], reads=[('alpha', 'o')])\n")
        core = Core(self.home, self.home / ".claude", self.comps)
        self.assertEqual(core.read("alpha", "o", {}), [])
        self.assertEqual(core.run_pass({})["components"]["beta"]["errors"], [])
        self.assertEqual(sys.modules["stx_beta"].GOT, [[]])
        self.assertEqual(core.describe()["offer_errors"], {"alpha.o": "ValueError: boom"})
        sys.modules["stx_alpha"].BAD[0] = False
        self.assertEqual(core.read("alpha", "o", {}), [1])
        self.assertEqual(core.describe()["offer_errors"], {})

    def test_starts_with_zero_components(self):
        c = self.core()
        self.assertEqual((c.components, c.error, c.last_pass), ([], None, None))
        self.assertTrue((self.home / "stellaxis.db").is_file())
        r = c.run_pass({})
        self.assertEqual(r["components"], {})
        self.assertRegex(r["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(c.last_pass, r)
        self.assertEqual(c.describe(), {"components": [], "store": str(self.home / "stellaxis.db"), "error": None, "offer_errors": {}, "last_pass": r})

    def test_a_reader_that_raises_does_not_stop_the_others(self):
        put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core()
        r = c.run_pass({"x": 7})
        self.assertEqual(r["components"], {"alpha": {"readers": 1, "errors": []}, "beta": {"readers": 2, "errors": ["ValueError: bad"]}})
        ctx = c.ctx("alpha", {"k": 1})
        self.assertEqual((ctx.name, ctx.cfg, ctx.home), ("alpha", {"k": 1}, self.home))
        with closing(c.store.connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM beta_seen").fetchone()[0], 1)

    def test_reads_go_through_declared_offers_only(self):
        put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core(); c.run_pass({"x": 7})
        beta = c.ctx("beta", {})
        self.assertEqual(beta.read("alpha", "things"), [{"id": "7"}])
        self.assertEqual(beta.read("alpha", "things", prefix="z"), [])
        self.assertEqual(beta.read("ghost", "things"), [])      # the owner is not installed
        with self.assertRaisesRegex(RuntimeError, r"^broken did not load: RuntimeError: boom$"):
            beta.read("broken", "things")                       # the owner is there but failed to load
        with self.assertRaises(LookupError):
            beta.read("alpha", "other")                         # never declared

    def test_a_reader_that_reads_a_component_that_failed_to_load_fails_and_the_others_run(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        put(self.comps, "asker", "from core.component import Component\nGOT = []\n"
            "def r(ctx):\n    GOT.append(ctx.read('ghost', 'things'))\n    GOT.append(ctx.read('broken', 'things'))\n"
            "COMPONENT = Component(name='asker', readers=[r], reads=[('ghost', 'things'), ('broken', 'things')])\n")
        c = self.core()
        r = c.run_pass({"x": 7})
        self.assertEqual(r["components"]["asker"]["errors"], ["RuntimeError: broken did not load: RuntimeError: boom"])
        self.assertEqual(r["components"]["alpha"]["errors"], [])
        self.assertEqual(sys.modules["stx_asker"].GOT, [[]])   # the absent owner gave [], the broken one raised
        self.assertEqual(c.read("broken", "things", {}), [])    # a way in still gets []
        with closing(c.store.connect()) as db:
            self.assertEqual([tuple(x) for x in db.execute("SELECT id FROM alpha_things")], [("7",)])

    def test_a_way_in_reads_an_offer_without_declaring_it(self):
        put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core(); c.run_pass({"x": 7})
        self.assertEqual(c.read("alpha", "things", {}), [{"id": "7"}])
        self.assertEqual(c.read("alpha", "things", {}, prefix="z"), [])
        self.assertEqual(c.read("alpha", "no_such_offer", {}), [])
        self.assertEqual(c.read("ghost", "things", {}), [])
        self.assertEqual(c.read("broken", "things", {}), [])

    def test_bad_records_stop_only_that_component(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "thief", "from core.component import Component\nCOMPONENT = Component(name='thief', records=['CREATE TABLE IF NOT EXISTS alpha_x (id TEXT)'])\n")
        c = self.core()
        by = {x["name"]: x for x in c.describe()["components"]}
        self.assertIn("ValueError", by["thief"]["error"])
        self.assertEqual(by["alpha"], {"name": "alpha", "readers": 1, "actions": [], "offers": ["things"], "reads": [], "error": None})
        self.assertEqual(set(c.run_pass({})["components"]), {"alpha"})

    def test_a_change_that_fails_stops_only_that_component(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "sick", "from core.component import Component\nCOMPONENT = Component(name='sick', records=['CREATE TABLE IF NOT EXISTS sick_t (id TEXT)'], changes=[['ALTER TABLE sick_t ADD COLUMN x TEXT', 'ALTER TABLE sick_t ADD COLUMN x TEXT']])\n")
        with closing(sqlite3.connect(self.home / "stellaxis.db")) as db, db:
            db.execute("CREATE TABLE sick_t (id TEXT)")   # an older store, so the changes run
        c = self.core()
        by = {x["name"]: x for x in c.describe()["components"]}
        self.assertIn("OperationalError", by["sick"]["error"])
        self.assertIsNone(by["alpha"]["error"])
        self.assertEqual(set(c.run_pass({})["components"]), {"alpha"})

    def test_records_that_fail_in_sqlite_stop_only_that_component(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "sick", "from core.component import Component\nCOMPONENT = Component(name='sick', records=['CREATE TABLE IF NOT EXISTS sick_t (id TEXT)', 'CREATE INDEX IF NOT EXISTS sick_i ON sick_t (missing_column)'])\n")
        c = self.core()
        by = {x["name"]: x for x in c.describe()["components"]}
        self.assertIn("OperationalError", by["sick"]["error"])
        self.assertIsNone(by["alpha"]["error"])
        self.assertEqual(set(c.run_pass({})["components"]), {"alpha"})

    def test_a_store_locked_while_the_core_is_made_heals_at_the_next_pass(self):
        put(self.comps, "alpha", ALPHA)
        store.Store(self.home / "stellaxis.db")
        real = sqlite3.connect
        with closing(real(self.home / "stellaxis.db", isolation_level=None)) as lock, \
                mock.patch.object(store.sqlite3, "connect", lambda path, timeout=5: real(path, timeout=0.1)):
            lock.execute("BEGIN IMMEDIATE")   # another writer holds the store
            c = self.core()
            [alpha] = c.components
            self.assertIn("database is locked", alpha.error)
            self.assertEqual(c.run_pass({"x": 1})["components"], {})   # still locked: still off
            self.assertIn("database is locked", alpha.error)
            lock.execute("ROLLBACK")
        r = c.run_pass({"x": 7})
        self.assertEqual(r["components"], {"alpha": {"readers": 1, "errors": []}})
        self.assertIsNone(alpha.error)
        self.assertEqual(c.read("alpha", "things", {}), [{"id": "7"}])

    def test_a_component_that_failed_to_import_is_not_tried_again(self):
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core()
        c.run_pass({})
        self.assertEqual([x.error for x in c.components], ["RuntimeError: boom"])

    def test_a_store_that_cannot_open_is_reported_not_raised(self):
        (self.home / "stellaxis.db").mkdir()
        put(self.comps, "alpha", ALPHA)
        c = self.core()
        self.assertTrue(c.error)
        self.assertIsNone(c.store)
        [e] = c.run_pass({})["components"]["alpha"]["errors"]
        self.assertTrue(e.startswith("StoreError: "))
        self.assertEqual(c.run_pass({})["components"], {"alpha": {"readers": 1, "errors": [e]}})
        self.assertEqual(c.describe()["error"], c.error)

    def test_a_core_never_writes_to_the_module_component(self):
        put(self.comps, "alpha", ALPHA)
        first = self.core()
        first.components[0].error = "ensure failed"
        self.home = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.home, True)
        second = Core(self.home, self.home / ".claude", self.comps)
        self.assertIsNone(second.components[0].error)
        self.assertEqual(set(second.run_pass({})["components"]), {"alpha"})
        self.assertIsNone(component.import_folder(self.comps / "alpha").COMPONENT.error)

    def test_readers_get_the_inputs_of_the_pass(self):
        put(self.comps, "sees", SEES)
        c = self.core()
        c.run_pass({}, {"sessions": [1, 2]})
        c.run_pass({})
        seen = component.import_folder(self.comps / "sees").SEEN
        self.assertEqual(seen, [{"sessions": [1, 2]}, {}])
        self.assertEqual(c.ctx("sees", {}).inputs, {})

    ROUNDS = ("from core.component import Component\nfrom pathlib import Path\n"
              "def log(ctx, what):\n    with open(Path(ctx.home) / 'order.txt', 'a') as f:\n        f.write(what + ' ')\n"
              "def one(ctx):\n    log(ctx, '{n}1')\n    {boom}\n"
              "def two(ctx):\n    log(ctx, '{n}2')\n"
              "COMPONENT = Component(name='{n}', readers=[one, two])\n")

    def test_a_pass_runs_in_rounds(self):
        put(self.comps, "alpha", self.ROUNDS.format(n="alpha", boom="raise ValueError('x')"))
        put(self.comps, "beta", self.ROUNDS.format(n="beta", boom="pass"))
        res = Core(self.home, self.home / ".claude", self.comps).run_pass({})
        self.assertEqual((self.home / "order.txt").read_text().split(), ["alpha1", "beta1", "alpha2", "beta2"])
        self.assertEqual(res["components"]["alpha"], {"readers": 2, "errors": ["ValueError: x"]})
        self.assertEqual(res["components"]["beta"], {"readers": 2, "errors": []})

    def test_the_pass_cache_is_loaded_one_time_and_written_at_the_end(self):
        put(self.comps, "alpha", "from core.component import Component\nSEEN = []\n"
            "def r(ctx):\n    ctx.cache['k'] = 1\n    SEEN.append(ctx.cache)\n"
            "COMPONENT = Component(name='alpha', readers=[r])\n")
        put(self.comps, "beta", "from core.component import Component\nSEEN = []\n"
            "def r(ctx):\n    SEEN.append(ctx.cache)\n"
            "COMPONENT = Component(name='beta', readers=[r])\n")
        (self.home / "cache.json").write_text('{"old": 2}')
        c = self.core()
        c.run_pass({})
        a = component.import_folder(self.comps / "alpha").SEEN
        b = component.import_folder(self.comps / "beta").SEEN
        self.assertIs(a[0], b[0])
        self.assertEqual(json.loads((self.home / "cache.json").read_text()), {"old": 2, "k": 1})
        (self.home / "cache.json").write_text("[1]")   # not an object: the pass starts from an empty cache
        c.run_pass({})
        self.assertEqual(json.loads((self.home / "cache.json").read_text()), {"k": 1})
        self.assertIsNot(a[1], a[0])

    def test_a_pass_that_never_asks_for_the_cache_writes_none(self):
        put(self.comps, "alpha", ALPHA)
        self.core().run_pass({"x": 1})
        self.assertFalse((self.home / "cache.json").exists())

    def test_the_cache_and_the_transcripts_exist_only_during_a_pass(self):
        put(self.comps, "alpha", ALPHA)
        c = self.core()
        for _ in range(2):
            ctx = c.ctx("alpha", {})
            with self.assertRaisesRegex(RuntimeError, "only during a pass"):
                ctx.cache
            with self.assertRaisesRegex(RuntimeError, "only during a pass"):
                ctx.transcripts()
            c.run_pass({})

    LINES = ("from core.component import Component\nfrom core.transcripts import Consumer\nGOT = []\n"
             "def line(st, d, ids, owner):\n    st['{n}'] += 1\n"
             "def r(ctx):\n    GOT.append(ctx.transcripts())\n    GOT.append(ctx.transcripts())\n"
             "COMPONENT = Component(name='{n}', readers=[r], lines=Consumer(fresh=lambda: {{'{n}': 0}}, line=line))\n")

    def test_every_component_with_lines_gets_every_line_of_one_reading(self):
        put(self.comps, "alpha", self.LINES.format(n="alpha"))
        put(self.comps, "beta", self.LINES.format(n="beta"))
        p = self.home / ".claude" / "projects" / "p" / "s.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("".join(json.dumps({"type": "user", "sessionId": "s", "n": i}) + "\n" for i in range(3)))
        self.core().run_pass({})
        a = component.import_folder(self.comps / "alpha").GOT
        b = component.import_folder(self.comps / "beta").GOT
        self.assertTrue(a[0] is a[1] is b[0] is b[1])
        st = a[0][str(p)]
        self.assertEqual((st["alpha"], st["beta"], st["session"]), (3, 3, "s"))
        self.assertEqual(json.loads((self.home / "cache.json").read_text())["files"][str(p)]["alpha"], 3)

    def test_a_memo_belongs_to_one_component_and_lives_for_one_pass(self):
        put(self.comps, "alpha", "from core.component import Component\nBEFORE = []\n"
            "def r(ctx):\n    BEFORE.append(dict(ctx.memo))\n    ctx.memo['x'] = 1\n"
            "def m(ctx):\n    return [dict(ctx.memo)]\n"
            "COMPONENT = Component(name='alpha', readers=[r], offers={'m': m})\n")
        put(self.comps, "beta", "from core.component import Component\nGOT = []\n"
            "def r(ctx):\n    GOT.append(ctx.read('alpha', 'm'))\n    GOT.append(dict(ctx.memo))\n"
            "COMPONENT = Component(name='beta', readers=[r], reads=[('alpha', 'm')])\n")
        c = self.core()
        c.run_pass({})
        self.assertEqual(component.import_folder(self.comps / "beta").GOT, [[{"x": 1}], {}])
        self.assertEqual(c.ctx("alpha", {}).memo, {})
        c.run_pass({})
        self.assertEqual(component.import_folder(self.comps / "alpha").BEFORE, [{}, {}])

    POISON = ("from core.component import Component\nfrom core.transcripts import Consumer\nBAD = [{bad}]\n"
              "def line(st, d, ids, owner):\n    st['{n}'] += 1\n"
              "    if BAD[0] and st['{n}'] == 3:\n        raise KeyError('third')\n"
              "def r(ctx):\n    ctx.transcripts()\n"
              "COMPONENT = Component(name='{n}', readers=[r, r], lines=Consumer(fresh=lambda: {{'{n}': 0}}, line=line))\n")

    def test_a_failed_transcript_read_poisons_the_pass_and_saves_nothing(self):
        put(self.comps, "alpha", self.POISON.format(n="alpha", bad="False"))
        put(self.comps, "beta", self.POISON.format(n="beta", bad="True"))
        p = self.home / ".claude" / "projects" / "p" / "s.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("".join(json.dumps({"type": "user", "n": i}) + "\n" for i in range(4)))
        (self.home / "cache.json").write_text('{"old": 1}')
        c = self.core()
        res = c.run_pass({})
        self.assertEqual(res["components"]["alpha"]["errors"], ["KeyError: 'third'"] * 2)
        self.assertEqual(res["components"]["beta"]["errors"], ["KeyError: 'third'"] * 2)
        self.assertEqual((self.home / "cache.json").read_text(), '{"old": 1}')
        component.import_folder(self.comps / "beta").BAD[0] = False
        res = c.run_pass({})
        self.assertEqual(res["components"]["beta"]["errors"], [])
        saved = json.loads((self.home / "cache.json").read_text())
        self.assertEqual((saved["old"], saved["files"][str(p)]["alpha"], saved["files"][str(p)]["beta"]), (1, 4, 4))

    def test_the_pass_state_belongs_to_the_thread_that_runs_the_pass(self):
        put(self.comps, "alpha", "from core.component import Component\nimport threading\nGOT = []\n"
            "def ask(ctx):\n"
            "    for f in (lambda: ctx.cache, ctx.transcripts):\n"
            "        try:\n            f()\n            GOT.append('no error')\n"
            "        except RuntimeError as e:\n            GOT.append(str(e))\n"
            "    ctx.memo['x'] = 1\n    GOT.append(ctx.memo)\n"
            "def r(ctx):\n    t = threading.Thread(target=ask, args=(ctx,))\n    t.start()\n    t.join()\n"
            "    GOT.append(dict(ctx.memo))\n"
            "COMPONENT = Component(name='alpha', readers=[r])\n")
        self.assertEqual(self.core().run_pass({})["components"]["alpha"]["errors"], [])
        self.assertEqual(component.import_folder(self.comps / "alpha").GOT,
                         ["only during a pass", "only during a pass", {}, {}])
        self.assertFalse((self.home / "cache.json").exists())


def count(st, line, ids, owner):
    st["n"] += 1
    st["seen"].append(line["type"])
    if "k" in line:
        ids[line["k"]] = owner


def boom(st, line, ids, owner):
    raise TypeError("boom")


COUNT = transcripts.Consumer(fresh=lambda: {"n": 0, "seen": []}, line=count)
HEAD = ["path", "offset", "size", "session", "cwd", "first_ts", "last_ts", "entrypoint", "version", "branch", "sidechain"]


class TranscriptsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "projects"
        self.cache = {}

    def write(self, name, rows, mode="w"):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, mode) as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        return str(p)

    def read(self, *consumers):
        return transcripts.read(self.dir, self.cache, list(consumers))

    def test_a_new_file_gets_the_state_keys_in_order(self):
        a = self.write("p/a.jsonl", [{"type": "user"}])
        side = self.write("p/a/subagents/x.jsonl", [{"type": "user"}])
        files = self.read(COUNT)
        self.assertIs(files, self.cache["files"])
        self.assertEqual(list(files[a]), HEAD + ["n", "seen", "v"])
        self.assertEqual((files[a]["v"], transcripts.VERSION), ("0.2.2", "0.2.2"))
        self.assertEqual((files[a]["sidechain"], files[side]["sidechain"]), (False, True))

    def test_each_line_is_handed_one_time(self):
        a = self.write("p/a.jsonl", [{"type": "a"}, {"type": "b"}, {"type": "c"}])
        self.read(COUNT)
        self.assertEqual(self.cache["files"][a]["n"], 3)
        before = json.dumps(self.cache)
        self.read(COUNT)
        self.assertEqual(json.dumps(self.cache), before)
        self.write("p/a.jsonl", [{"type": "d"}, {"type": "e"}], mode="a")
        self.read(COUNT)
        self.assertEqual(self.cache["files"][a]["seen"], ["a", "b", "c", "d", "e"])

    def test_a_last_line_without_a_newline_waits_until_it_is_whole(self):
        a = self.write("p/a.jsonl", [{"type": "a"}])
        with open(a, "a") as f:
            f.write('{"type": "b"')
        self.read(COUNT)
        self.assertEqual(self.cache["files"][a]["seen"], ["a"])
        with open(a, "a") as f:
            f.write("}\n")
        self.read(COUNT)
        self.assertEqual(self.cache["files"][a]["seen"], ["a", "b"])

    def test_the_head_facts(self):
        a = self.write("p/a.jsonl", [
            {"type": "a", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "s1", "cwd": "/one", "entrypoint": "cli",
             "version": "1.0", "gitBranch": "main"},
            {"type": "b", "sessionId": "s2", "cwd": "/two", "entrypoint": "sdk"},
            {"type": "c", "timestamp": "2026-01-02T00:00:00Z", "version": "2.0", "gitBranch": "dev"}])
        st = self.read()[a]
        self.assertEqual({k: st[k] for k in HEAD[3:10]},
                         {"session": "s1", "cwd": "/one", "first_ts": "2026-01-01T00:00:00Z", "last_ts": "2026-01-02T00:00:00Z",
                          "entrypoint": "cli", "version": "2.0", "branch": "dev"})

    def test_a_bad_line_or_a_failing_consumer_stops_nothing_else(self):
        a = self.write("p/a.jsonl", ["x", {"type": "a"}])
        with open(a, "a") as f:
            f.write("not json\n")
        self.write("p/a.jsonl", [{"type": "b"}], mode="a")
        st = self.read(transcripts.Consumer(fresh=dict, line=boom), COUNT)[a]
        self.assertEqual(st["seen"], ["a", "b"])

    def test_a_file_that_got_shorter_is_read_again_and_its_ids_are_forgotten(self):
        a = self.write("p/a.jsonl", [{"type": "a", "k": "1"}, {"type": "a", "k": "2"}])
        b = self.write("p/b.jsonl", [{"type": "b", "k": "3"}])
        self.read(COUNT)
        self.assertEqual(self.cache["ids"], {"1": transcripts.owner(a), "2": transcripts.owner(a), "3": transcripts.owner(b)})
        self.write("p/a.jsonl", [{"type": "a"}])
        self.read(COUNT)
        self.assertEqual(self.cache["files"][a]["n"], 1)
        self.assertEqual(self.cache["ids"], {"3": transcripts.owner(b)})

    def test_a_state_of_another_version_is_read_again(self):
        a = self.write("p/a.jsonl", [{"type": "a"}, {"type": "b"}])
        self.read(COUNT)
        self.cache["files"][a].update(v="0.1", n=99)
        self.read(COUNT)
        self.assertEqual((self.cache["files"][a]["n"], self.cache["files"][a]["v"]), (2, "0.2.2"))

    def test_a_deleted_file_leaves_files(self):
        a = self.write("p/a.jsonl", [{"type": "a"}])
        b = self.write("p/b.jsonl", [{"type": "b"}])
        self.read(COUNT)
        Path(a).unlink()
        self.assertEqual(list(self.read(COUNT)), [b])

    def test_a_consumer_added_later_gets_every_line(self):
        a = self.write("p/a.jsonl", [{"type": "a"}, {"type": "a"}])
        b = self.write("p/b.jsonl", [{"type": "b"}] * 3)
        self.read()
        self.assertNotIn("n", self.cache["files"][a])
        files = self.read(COUNT)
        self.assertEqual((files[a]["n"], files[b]["n"]), (2, 3))

    def test_a_consumer_that_is_gone_leaves_the_files_that_move(self):
        a = self.write("p/a.jsonl", [{"type": "a"}, {"type": "a"}])
        b = self.write("p/b.jsonl", [{"type": "b"}] * 3)
        self.read(COUNT)
        self.write("p/a.jsonl", [{"type": "a"}], mode="a")
        files = self.read()
        self.assertEqual(list(files[a]), HEAD + ["v"])
        self.assertEqual(files[b]["n"], 3)
        b_before = json.dumps(files[b])
        files = self.read(COUNT)
        self.assertEqual(files[a]["n"], 3)
        self.assertEqual(json.dumps(files[b]), b_before)

    def test_consumer_keys_may_not_clash(self):
        self.write("p/a.jsonl", [{"type": "a"}])
        for consumers in ([transcripts.Consumer(fresh=lambda: {"cwd": 0}, line=count)],
                          [transcripts.Consumer(fresh=lambda: {"v": 0}, line=count)],
                          [COUNT, transcripts.Consumer(fresh=lambda: {"n": 0}, line=count)]):
            with self.assertRaises(ValueError):
                transcripts.read(self.dir, self.cache, consumers)
            self.assertEqual(self.cache, {})

    def test_sessions_groups_the_files(self):
        def state(path, **kw):
            st = {"path": path, "session": None, "cwd": None, "first_ts": None, "last_ts": None, "entrypoint": None,
                  "version": None, "branch": None, "sidechain": "/subagents/" in path}
            st.update(kw)
            return st
        main = state("/p/s.jsonl", session="s", cwd="/a", branch="main", first_ts="2026-01-02", last_ts="2026-01-03",
                     entrypoint="cli", version="1")
        later = state("/p/s2.jsonl", session="s", branch="dev", first_ts="2026-01-04", last_ts="2026-01-05", version="2")
        side = state("/p/s/subagents/x.jsonl", session="s", cwd="/other", branch="side", entrypoint="sdk",
                     first_ts="2026-01-01", last_ts="2026-01-02")
        lonely = state("/p/lonely.jsonl", cwd="/l", first_ts="2026-02-01", last_ts="2026-02-01")
        out = transcripts.sessions({s["path"]: s for s in (main, later, side, lonely)})
        self.assertEqual(list(out), ["s", "lonely"])
        self.assertEqual(out["s"], {"id": "s", "cwd": "/a", "branch": "dev", "first_ts": "2026-01-01", "last_ts": "2026-01-05",
                                    "entrypoint": "cli", "version": "2", "subagent_files": 1, "files": [main, later, side]})
        self.assertEqual(out["lonely"], {"id": "lonely", "cwd": "/l", "branch": None, "first_ts": "2026-02-01",
                                         "last_ts": "2026-02-01", "entrypoint": None, "version": None,
                                         "subagent_files": 0, "files": [lonely]})


class UtilTest(unittest.TestCase):
    def test_the_clock_can_be_fixed(self):
        from core import util
        with mock.patch.dict(os.environ, {"STELLAXIS_NOW": "2026-11-11T12:00:00Z"}):
            self.assertEqual(util.now(), dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc))
            self.assertEqual(util.now_iso(), "2026-11-11T12:00:00Z")
        with mock.patch.dict(os.environ):
            os.environ.pop("STELLAXIS_NOW", None)
            self.assertLess(abs((util.now() - dt.datetime.now(dt.timezone.utc)).total_seconds()), 5)


class UndoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.arch = self.home / ".claude" / "_archive"

    def test_a_note_is_printed_plain_and_kept(self):
        said = []
        plan = undo.Plan(False, "t", self.arch, out=said.append)
        plan.note("  skip x: not found")
        self.assertEqual((said, plan.notes, plan.steps, plan.result), (["  skip x: not found"], ["  skip x: not found"], [], None))
        self.assertEqual((undo.pre(True), undo.pre(False)), ("  ", "  [dry-run] "))

    def test_a_dry_run_plan_writes_nothing(self):
        lines = []
        p = undo.Plan(False, "t", self.arch, out=lines.append)
        p.say("move a -> b")
        p.manifest["entries"].append({"op": "move", "from": "a", "to": "b"})
        self.assertIsNone(p.finish())
        self.assertFalse(self.arch.exists())
        self.assertEqual(p.steps, ["move a -> b"])
        self.assertEqual(lines, ["  [dry-run] move a -> b", "\nNothing changed. Re-run with --apply to execute."])

    def test_an_applied_plan_writes_the_manifest_and_calls_back(self):
        lines, called = [], []
        p = undo.Plan(True, "t", self.arch, on_applied=lambda: called.append(1), undo_hint=lambda n: f"undo {n}", out=lines.append)
        p.manifest["entries"].append({"op": "move", "from": "a", "to": "b"})
        m = p.finish()
        self.assertEqual(m, p.dir / "manifest.json")
        self.assertEqual(json.loads(m.read_text())["entries"], [{"op": "move", "from": "a", "to": "b"}])
        self.assertEqual(called, [1])
        self.assertIn(f"Undo:     undo {p.dir.name}", lines)

    def test_an_applied_plan_with_no_entries_writes_nothing(self):
        self.assertIsNone(undo.Plan(True, "t", self.arch, out=lambda _: None).finish())
        self.assertFalse(self.arch.exists())

    def old_archive(self):
        """A manifest exactly as prune.py wrote it before this change, with one entry of every kind."""
        d = self.arch / "20261101-090000-archive"
        kept, cfg = d / "files" / "skill", self.home / "cfg.json"
        kept.mkdir(parents=True)
        (kept / "SKILL.md").write_text("s")
        cfg.write_text(json.dumps({"mcpServers": {"added": {}}, "enabledPlugins": {"p": False}}))
        (d / "manifest.json").write_text(json.dumps({"created": "2026-11-01T09:00:00Z", "label": "archive", "entries": [
            {"op": "json_backup", "backup_of": str(cfg), "backup": str(d / "json-backups" / "x")},
            {"op": "move", "id": "skill:user:s", "from": str(self.home / "skills" / "s"), "to": str(kept)},
            {"op": "json_del", "id": "mcp:user:gone", "file": str(cfg), "keys": ["mcpServers"], "name": "gone", "value": {"command": "x"}},
            {"op": "json_add", "id": "mcp:user:added", "file": str(cfg), "keys": ["mcpServers"], "name": "added"},
            {"op": "json_set", "id": "plugin:user:p", "file": str(cfg), "keys": ["enabledPlugins"], "name": "p", "prev": None}]}))
        return d, cfg

    def test_an_old_archive_still_restores(self):
        d, cfg = self.old_archive()
        lines = []
        undo.restore("last", False, self.arch, out=lines.append)
        self.assertTrue((d / "manifest.json").exists())
        self.assertFalse((self.home / "skills" / "s").exists())
        self.assertEqual(lines[0], f"Restoring {d.name}:")
        self.assertTrue(all(l.startswith("  [dry-run] ") for l in lines[1:-1]))
        called = []
        undo.restore(d.name, True, self.arch, on_applied=lambda: called.append(1), out=lambda _: None)
        self.assertEqual((self.home / "skills" / "s" / "SKILL.md").read_text(), "s")
        self.assertEqual(json.loads(cfg.read_text()), {"mcpServers": {"gone": {"command": "x"}}, "enabledPlugins": {}})
        self.assertTrue((d / "manifest.restored.json").exists())
        self.assertEqual(called, [1])

    def test_restore_stops_when_a_handler_says_no(self):
        d, _ = self.old_archive()
        man = json.loads((d / "manifest.json").read_text())
        man["entries"].append({"op": "future_op", "x": 1})
        (d / "manifest.json").write_text(json.dumps(man))
        with self.assertRaisesRegex(undo.RestoreError, "restore stopped; nothing after this step was changed"):
            undo.restore(d.name, True, self.arch, handlers={"future_op": lambda e, apply, out: False}, out=lambda _: None)
        self.assertTrue((d / "manifest.json").exists())
        with self.assertRaisesRegex(undo.RestoreError, "no manifest in"):
            undo.restore("absent", True, self.arch)

    def test_two_applied_plans_in_one_second_get_two_folders(self):
        with mock.patch.object(undo, "stamp", return_value="20261111-120000"):
            plans = [undo.Plan(True, "t", self.arch, out=lambda _: None) for _ in range(3)]
            dry = undo.Plan(False, "t", self.arch, out=lambda _: None)
        self.assertEqual([p.dir.name for p in plans], ["20261111-120000-t", "20261111-120000-t-2", "20261111-120000-t-3"])
        self.assertEqual(dry.dir.name, "20261111-120000-t")
        self.assertFalse(self.arch.exists())          # naming a folder creates nothing

    def test_a_folder_already_on_disk_is_not_reused(self):
        (self.arch / "20261111-120001-t").mkdir(parents=True)
        with mock.patch.object(undo, "stamp", return_value="20261111-120001"):
            self.assertEqual(undo.Plan(True, "t", self.arch, out=lambda _: None).dir.name, "20261111-120001-t-2")

    def test_restore_refuses_a_step_it_has_no_handler_for(self):
        d, cfg = self.old_archive()
        before = cfg.read_text()
        man = json.loads((d / "manifest.json").read_text())
        man["entries"].insert(0, {"op": "ghost.step"})      # restore runs entries last to first, so this one would run last
        (d / "manifest.json").write_text(json.dumps(man))
        for apply in (False, True):
            with self.assertRaisesRegex(undo.RestoreError, r"no undo for step 'ghost.step'; nothing was changed"):
                undo.restore(d.name, apply, self.arch, out=lambda _: None)
        self.assertEqual(cfg.read_text(), before)
        self.assertTrue((d / "manifest.json").exists())
        undo.restore(d.name, True, self.arch, handlers={"ghost.step": lambda e, apply, out: True}, out=lambda _: None)
        self.assertTrue((d / "manifest.restored.json").exists())


class ActionTest(unittest.TestCase):
    ACT = '''
from pathlib import Path
from core.component import Component
def touch(plan, ctx, path, keep=False, fail=False):
    plan.say(f"create {path}")
    if keep:
        plan.manifest["entries"].append({"op": "move", "from": path + ".old", "to": path})
    if plan.apply:
        Path(path).write_text("x")
    if fail:
        raise RuntimeError("half way")
    return True
def refuse(plan, ctx):
    plan.say("refused")
    return False
def note(plan, ctx, path):
    plan.say(f"note {path}")
    plan.manifest["entries"].append({"op": "alpha.unnote", "path": path})
    if plan.apply:
        Path(path).write_text("n")
    return True
def secret(plan, ctx, path, body):
    plan.result = {"n": len(body)}
    plan.note("  kept quiet")
    return True
secret.private = ("body",)
def unnote(e, apply, out, ctx):
    out("remove " + e["path"])
    out("home " + str(ctx.home))
    if apply:
        Path(e["path"]).unlink()
    return True
def uses_db(plan, ctx):
    ctx.db()
    return True
COMPONENT = Component(name="alpha", actions={"touch": touch, "refuse": refuse, "note": note, "secret": secret, "uses_db": uses_db}, undo={"alpha.unnote": unnote})
'''

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"
        put(self.comps, "alpha", self.ACT)
        self.core = Core(self.home, self.home / ".claude", self.comps)
        self.f = str(self.home / "made.txt")
        self.log = self.home / "actions.jsonl"

    def test_a_dry_run_changes_nothing_and_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.core.run_action("alpha", "touch", {"path": self.f}, False, {})
        self.assertEqual(r, {"applied": False, "ok": True, "steps": [f"create {self.f}"], "notes": [], "result": None, "manifest": None})
        self.assertEqual((buf.getvalue(), Path(self.f).exists(), self.log.exists()), ("", False, False))

    def test_an_applied_action_is_logged(self):
        r = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {})
        self.assertTrue(Path(self.f).exists())
        m = Path(r["manifest"])
        self.assertEqual(m.parent.parent, self.home / ".claude" / "_archive")
        self.assertTrue(m.parent.name.endswith("-alpha-touch"))
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual({k: row[k] for k in ("component", "action", "args", "ok", "manifest", "error")},
                         {"component": "alpha", "action": "touch", "args": {"path": self.f, "keep": True}, "ok": True, "manifest": r["manifest"], "error": None})

    def test_an_action_that_fails_half_way_still_leaves_its_manifest(self):
        with self.assertRaisesRegex(RuntimeError, "half way"):
            self.core.run_action("alpha", "touch", {"path": self.f, "keep": True, "fail": True}, True, {})
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual((row["ok"], row["error"]), (False, "RuntimeError: half way"))
        self.assertTrue(Path(row["manifest"]).is_file())

    def test_an_action_that_returns_false_is_not_ok(self):
        r = self.core.run_action("alpha", "refuse", {}, False, {})
        self.assertEqual(r, {"applied": False, "ok": False, "steps": ["refused"], "notes": [], "result": None, "manifest": None})
        self.assertFalse(self.log.exists())
        r = self.core.run_action("alpha", "refuse", {}, True, {})
        self.assertEqual((r["applied"], r["ok"]), (True, False))
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual((row["ok"], row["error"]), (False, None))

    def test_a_refused_dry_run_is_not_finished(self):
        said = []
        plan = undo.Plan(False, "mine", self.home / ".claude" / "_archive", out=said.append)
        r = self.core.run_action("alpha", "refuse", {}, False, {}, plan=plan)
        self.assertEqual((r["ok"], said), (False, ["  [dry-run] refused"]))

    def test_unknown_names_are_key_errors(self):
        for comp, act in (("ghost", "touch"), ("alpha", "ghost")):
            with self.assertRaises(KeyError):
                self.core.run_action(comp, act, {}, False, {})

    def test_a_component_runs_its_own_action_through_the_core(self):
        ctx = self.core.ctx("alpha", {})
        self.assertEqual(ctx.act("touch", {"path": self.f}), {"applied": False, "ok": True, "steps": [f"create {self.f}"], "notes": [], "result": None, "manifest": None})
        self.assertFalse(Path(self.f).exists())
        r = ctx.act("touch", {"path": self.f, "keep": True}, apply=True)
        self.assertTrue(Path(self.f).exists())
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual((row["component"], row["action"], row["manifest"]), ("alpha", "touch", r["manifest"]))

    def test_two_applied_actions_in_one_second_keep_two_manifests(self):
        with mock.patch.object(undo, "stamp", return_value="20261111-120002"):
            a = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {})
            b = self.core.run_action("alpha", "touch", {"path": self.f + "2", "keep": True}, True, {})
        self.assertNotEqual(a["manifest"], b["manifest"])
        self.assertTrue(Path(a["manifest"]).is_file() and Path(b["manifest"]).is_file())

    def test_a_component_undo_step_restores_through_the_core_table(self):
        r = self.core.run_action("alpha", "note", {"path": self.f}, True, {})
        arch, name = self.home / ".claude" / "_archive", Path(r["manifest"]).parent.name
        with self.assertRaisesRegex(undo.RestoreError, "no undo for step 'alpha.unnote'"):
            undo.restore(name, True, arch, out=lambda _: None)
        self.assertEqual(sorted(self.core.undo_handlers()), ["alpha.unnote"])
        undo.restore(name, True, arch, handlers=self.core.undo_handlers(), out=lambda _: None)
        self.assertFalse(Path(self.f).exists())

    def test_private_arguments_stay_out_of_the_log_and_the_result_comes_back(self):
        r = self.core.run_action("alpha", "secret", {"path": self.f, "body": "s3cret"}, True, {})
        self.assertEqual((r["result"], r["notes"]), ({"n": 6}, ["  kept quiet"]))
        row = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(row["args"], {"path": self.f})
        self.assertNotIn("s3cret", self.log.read_text())

    def test_a_way_in_can_bring_its_own_plan(self):
        said = []
        plan = undo.Plan(True, "mine", self.home / ".claude" / "_archive", out=said.append)
        r = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {}, plan=plan)
        self.assertIn("  create " + self.f, said)
        self.assertTrue(Path(r["manifest"]).parent.name.endswith("-mine"))
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_an_undo_handler_gets_the_ctx(self):
        r = self.core.run_action("alpha", "note", {"path": self.f}, True, {})
        said = []
        undo.restore(Path(r["manifest"]).parent.name, True, self.home / ".claude" / "_archive",
                     handlers=self.core.undo_handlers({"k": 1}), out=said.append)
        self.assertIn("home " + str(self.home), said)

    def test_actions_run_with_the_store_down_and_only_a_store_user_fails(self):
        home = self.home / "down"
        (home / "stellaxis.db").mkdir(parents=True)
        core = Core(home, home / ".claude", self.comps)
        self.assertTrue(core.error)
        self.assertTrue(core.run_action("alpha", "touch", {"path": self.f}, True, {})["ok"])
        with self.assertRaises(StoreError):
            core.run_action("alpha", "uses_db", {}, True, {})
        rows = [json.loads(l) for l in (home / "actions.jsonl").read_text().splitlines()]
        self.assertEqual([(r["action"], r["ok"]) for r in rows], [("touch", True), ("uses_db", False)])
        self.assertTrue(rows[1]["error"].startswith("StoreError: "))


WEB = '''
from core.component import Component
def handle(handler, method, ctx):
    if handler.path == "/api/alpha/boom":
        raise RuntimeError("boom")
    if handler.path != "/api/alpha" or method != "GET":
        return False
    handler.send_response(200); handler.end_headers(); handler.wfile.write(ctx.name.encode())
    return True
COMPONENT = Component(name="alpha", handle=handle)
'''


class HttpCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def start(self, srv):
        self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def get(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        with closing(conn):
            conn.request(method, path, headers=headers or {})
            r = conn.getresponse()
            return r.status, r.read()


ECHO = '''
from core.component import Component
from core.server import ApiError, read_body, send_json
def handle(handler, method, ctx):
    if handler.path != "/api/alpha/echo" or method != "POST":
        return False
    try:
        send_json(handler, 200, read_body(handler))
    except ApiError as e:
        send_json(handler, e.status, {"error": e.msg, **e.extra})
    return True
COMPONENT = Component(name="alpha", handle=handle)
'''


class ServeTest(HttpCase):
    def serve(self):
        self.start(server.serve(Core(self.home, self.home / ".claude", self.comps), 0, {}))

    def test_a_component_reads_a_body_and_refuses_a_bad_one(self):
        put(self.comps, "alpha", ECHO)
        self.serve()

        def post(body, ctype="application/json", length=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
            with closing(conn):
                conn.putrequest("POST", "/api/alpha/echo")
                conn.putheader("Content-Type", ctype)
                conn.putheader("Content-Length", str(len(body) if length is None else length))
                conn.endheaders(body)
                r = conn.getresponse()
                return r.status, json.loads(r.read())
        self.assertEqual(post(b'{"a": 1}'), (200, {"a": 1}))
        self.assertEqual(post(b'{}', ctype="text/plain"), (415, {"error": "send application/json"}))
        self.assertEqual(post(b'[1]'), (400, {"error": "request body must be an object"}))
        self.assertEqual(post(b'{')[0], 400)
        self.assertEqual(post(b'', length=server.MAX_BODY + 1), (413, {"error": "request too large"}))

    def test_the_core_serves_with_zero_components(self):
        self.serve()
        status, body = self.get("/api/core")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"components": [], "store": str(self.home / "stellaxis.db"), "error": None, "offer_errors": {}, "last_pass": None})
        self.assertEqual(self.get("/anything")[0], 404)
        self.assertEqual(self.get("/api/core", method="POST")[0], 404)
        self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)

    def test_component_routes_and_their_errors(self):
        put(self.comps, "alpha", WEB)
        put(self.comps, "beta", "raise RuntimeError('broken')")
        self.serve()
        self.assertEqual(self.get("/api/alpha"), (200, b"alpha"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status, body = self.get("/api/alpha/boom")
        self.assertIn("RuntimeError: boom", err.getvalue())
        self.assertEqual((status, json.loads(body)), (500, {"error": "RuntimeError: boom"}))
        self.assertEqual(self.get("/api/alpha")[0], 200)
        by = {c["name"]: c for c in json.loads(self.get("/api/core")[1])["components"]}
        self.assertIn("RuntimeError", by["beta"]["error"])

    def test_components_still_answer_without_a_store(self):
        put(self.comps, "alpha", WEB)
        (self.home / "stellaxis.db").mkdir()
        self.serve()
        self.assertTrue(json.loads(self.get("/api/core")[1])["error"])
        self.assertEqual(self.get("/api/alpha")[0], 200)


class WiringTest(HttpCase):
    def setUp(self):
        super().setUp()
        self.start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler))

    def test_the_service_answers_api_core_and_keeps_its_routes(self):
        core = Core(self.home, self.home / ".claude", self.home / "components")
        with mock.patch.object(C, "_core", core):
            self.assertEqual(json.loads(self.get("/api/core")[1])["components"], [])
            self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)
            self.assertEqual(self.get("/api/status")[0], 200)
            self.assertEqual(self.get("/overview")[0], 200)

    def test_a_core_that_cannot_open_its_store_does_not_break_the_service(self):
        (self.home / "stellaxis.db").mkdir()
        with mock.patch.object(C, "_core", Core(self.home, self.home / ".claude", self.home / "components")):
            self.assertTrue(json.loads(self.get("/api/core")[1])["error"])
            self.assertEqual(self.get("/api/status")[0], 200)

    def test_the_first_build_of_the_core_is_locked(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        got = []
        with mock.patch.object(C, "_core", None), mock.patch.object(C, "STX_HOME", home / "stx"), \
                mock.patch.object(C, "CLAUDE_DIR", home / "claude"):
            threads = [threading.Thread(target=lambda: got.append(C.the_core())) for _ in range(8)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(len(got), 8)
        self.assertTrue(all(g is got[0] for g in got))
        self.assertIsNone(got[0].error)


class IndependenceTest(unittest.TestCase):
    def test_the_core_imports_nothing_outside_itself(self):
        outside = {p.stem for p in (REPO / "collector").glob("*.py")} | {"components", "unplaced"}
        for f in (REPO / "collector" / "core").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for n in names:
                    self.assertNotIn(n.split(".")[0], outside, f"{f.name} imports {n}")


if __name__ == "__main__":
    unittest.main()
