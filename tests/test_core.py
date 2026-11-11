"""Tests of the core package (collector/core) and its component loader.

  python3 -m unittest discover -s tests -k test_core
"""
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
from core import component, store


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


if __name__ == "__main__":
    unittest.main()
