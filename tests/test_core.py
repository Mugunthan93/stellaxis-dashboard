"""Tests of the core package (collector/core) and its component loader.

  python3 -m unittest discover -s tests -k test_core
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
from core import component


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


if __name__ == "__main__":
    unittest.main()
