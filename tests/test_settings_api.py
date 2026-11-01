"""Settings page backend: no-op saves are byte-identical, edits keep layout and unknown keys, backups and
conflicts behave. Runs against a temp CLAUDE_CONFIG_DIR / STELLAXIS_HOME over real HTTP.

  python3 -m unittest discover -s tests -v
"""
import glob
import http.client
import http.server
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ORIGINAL = ('{\n  "permissions": {\n    "allow": [\n      "Bash(ls:*)"\n    ]\n  },\n  "model": "sonnet",\n'
            '  "futureKey": {\n    "nested": [\n      1,\n      2.5\n    ]\n  },\n  "note": "caf\\u00e9 \\u2014 ok"\n}')


class SettingsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        T = Path(cls.tmp.name)
        cls.cfg, cls.proj = T / "cfg", T / "proj"
        cls.cfg.mkdir()
        cls.proj.mkdir()
        (T / "stx").mkdir()
        (T / "stx" / "snapshot.json").write_text(json.dumps({"projects": [{"path": str(cls.proj)}]}))
        os.environ["CLAUDE_CONFIG_DIR"] = str(cls.cfg)
        os.environ["STELLAXIS_HOME"] = str(T / "stx")
        sys.path.insert(0, str(REPO / "collector"))
        # Another test may have imported settings_api (via prune -> collect) with the real paths bound.
        sys.modules.pop("settings_api", None)
        import settings_api
        assert settings_api.CLAUDE_DIR == cls.cfg, f"settings_api would write to {settings_api.CLAUDE_DIR}"
        cls.api = settings_api

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                settings_api.handle(self, "GET")

            def do_POST(self):
                settings_api.handle(self, "POST")

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        for f in glob.glob(str(self.cfg / "*")):
            os.unlink(f)
        self.path = self.cfg / "settings.json"
        self.path.write_text(ORIGINAL)
        os.chmod(self.path, 0o600)

    def call(self, method, url, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, url, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_noop_save_is_byte_identical_and_backed_up(self):
        _, f = self.call("GET", "/api/settings")
        self.assertTrue(f["format"]["exact"])
        s, r = self.call("POST", "/api/settings/save", {"scope": "user", "data": f["data"], "base_sha": f["sha"]})
        self.assertEqual(s, 200, r)
        self.assertTrue(r["bytes_identical"])
        self.assertEqual(self.path.read_text(), ORIGINAL)
        self.assertEqual(Path(r["backup"]).read_text(), ORIGINAL)
        self.assertEqual(stat.S_IMODE(os.stat(r["backup"]).st_mode), 0o600)

    def test_edit_keeps_layout_order_and_unknown_keys(self):
        _, f = self.call("GET", "/api/settings")
        d = f["data"]
        d["model"] = "opus"
        d["env"] = {"A": "1"}
        _, p = self.call("POST", "/api/settings/preview", {"scope": "user", "data": d})
        self.assertIn('-  "model": "sonnet"', p["diff"])
        self.assertIn('+  "model": "opus"', p["diff"])
        s, r = self.call("POST", "/api/settings/save", {"scope": "user", "data": d, "base_sha": f["sha"]})
        self.assertEqual(s, 200, r)
        text = self.path.read_text()
        self.assertEqual(text, ORIGINAL.replace('"sonnet"', '"opus"')[:-2] + ',\n  "env": {\n    "A": "1"\n  }\n}')
        self.assertEqual(list(json.loads(text)), ["permissions", "model", "futureKey", "note", "env"])

    def test_conflict_when_file_changed(self):
        _, f = self.call("GET", "/api/settings")
        self.path.write_text(ORIGINAL.replace("sonnet", "haiku"))
        s, r = self.call("POST", "/api/settings/save", {"scope": "user", "data": f["data"], "base_sha": f["sha"]})
        self.assertEqual(s, 409)
        self.assertIn("haiku", self.path.read_text())

    def test_validation_and_origin(self):
        s, r = self.call("POST", "/api/settings/preview", {"scope": "user", "data": {"permissions": {"allow": "x"}}})
        self.assertEqual(s, 422)
        self.assertIn("permissions.allow must be a list of strings", r["errors"])
        s, _ = self.call("POST", "/api/settings/save", {"scope": "user", "data": {}}, {"Origin": "http://evil.example"})
        self.assertEqual(s, 403)
        s, _ = self.call("GET", "/api/settings?scope=local&project=/etc")
        self.assertEqual(s, 400)

    def test_local_scope_creates_file(self):
        q = f"/api/settings?scope=local&project={self.proj}"
        _, f = self.call("GET", q)
        self.assertFalse(f["exists"])
        s, r = self.call("POST", "/api/settings/save", {"scope": "local", "project": str(self.proj),
                                                         "data": {"model": "haiku"}, "base_sha": None})
        self.assertEqual(s, 200, r)
        self.assertIsNone(r["backup"])
        self.assertEqual((self.proj / ".claude" / "settings.local.json").read_text(), '{\n  "model": "haiku"\n}')


if __name__ == "__main__":
    unittest.main()
