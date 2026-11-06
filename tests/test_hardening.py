"""Local-server hardening: untrusted reel text stays inert, and only localhost can reach the service."""
import http.client
import http.server
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory()
ENV = {"REELS_HOME": TMP.name}
sys.path.insert(0, str(REPO / "collector"))
import collect as C


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "reels" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, ENV):
        spec.loader.exec_module(mod)
    return mod


class NoteRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = load("build_dashboard")

    def test_note_tags_survive(self):
        for tag in ('<a href="https://www.instagram.com/reel/abc/">', '<img src="images/abc.jpg" width="220" alt="Reel cover">',
                    '<img src="images/abc.jpg" width="72">', "</a>", "<details>", "<summary>", "</summary>", "</details>"):
            self.assertEqual(self.b.inline(tag), tag)

    def test_active_markup_is_escaped(self):
        for bad in ("<img src=x onerror=alert(1)>", '<a href="javascript:alert(1)">', '<img src="images/a.jpg" onload="x()">',
                    '<details ontoggle="x()" open>', '<a href="https://a.b/" onclick="x()">'):
            self.assertNotIn("<", self.b.inline(bad).replace("<code>", ""), bad)

    def test_markdown_links_cannot_run_or_break_out(self):
        self.assertIn('href="#"', self.b.inline("[x](javascript:alert(1))"))
        self.assertNotIn('" on', self.b.inline('[x](https://a.b/"onmouseover=x())').replace('&quot;', ''))
        self.assertIn('href="https://a.b/c"', self.b.inline("[x](https://a.b/c)"))
        self.assertIn('href="#n-abc"', self.b.inline("[x](2026-11-04-abc.md)"))


class CaptureInput(unittest.TestCase):
    def test_reel_rejects_paths_and_foreign_urls(self):
        reel = load_reel()
        with self.assertRaises(ValueError):
            reel.run({"code": "../../x", "url": "u"})
        for url in ("file:///etc/passwd", "http://scontent.cdninstagram.com/a", "-o/tmp/x", "https://evil.example/a",
                    "https://cdninstagram.com.evil.example/a"):
            with self.assertRaises(ValueError, msg=url):
                reel.get(url, "/dev/null")

    def test_triage_skips_bad_codes(self):
        t = load("triage")
        tools = Path(TMP.name) / ".tools"
        tools.mkdir(exist_ok=True)
        (Path(TMP.name) / ".processed.json").write_text("[]")
        (tools / "inbox.jsonl").write_text(json.dumps({"code": "../../repo", "caption": "ai"}) + "\n"
                                           + json.dumps({"code": "DfGood_1-x", "caption": "ai"}) + "\n")
        t.main("q.json")
        self.assertEqual([o["code"] for o in json.load(open(tools / "q.json"))], ["DfGood_1-x"])

    def test_receiver_needs_its_token(self):
        r = load("receive")
        r.TOKEN = "secret"
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), r.Handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        Path(r.INBOX).parent.mkdir(exist_ok=True)

        def get(q):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
            c.request("GET", "/r?" + q)
            return c.getresponse().status
        self.assertEqual(get('d=[{"code":"a"}]'), 403)
        self.assertEqual(get('t=wrong&d=[{"code":"a"}]'), 403)
        self.assertEqual(get('t=secret&d=[{"code":"abcde"}]'), 200)


def load_reel():
    fake = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
        return load("reel")


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, path, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.putrequest("GET", path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{self.port}")
        c.endheaders()
        r = c.getresponse()
        r.body = r.read().decode("utf-8", "replace")
        return r

    def test_rebinding_host_is_refused_everywhere(self):
        for path in ("/snapshot.json", "/api/status", "/reels/raw", "/", "/api/refresh"):
            self.assertEqual(self.req(path, "evil.example").status, 403, path)

    def test_pages_are_routed(self):
        self.assertIn('id="needs-you"', self.req("/").body)
        self.assertIn('id="refresh"', self.req("/overview").body)
        self.assertIn('id="refresh"', self.req("/index.html").body)
        self.assertEqual(self.req("/workspace?id=x").status, 200)
        self.assertIn('href="/overview"', self.req("/").body)

    def test_own_host_works_with_headers(self):
        r = self.req("/snapshot.json")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'self'", r.getheader("Content-Security-Policy"))

    def test_reels_page_cannot_call_the_api(self):
        Path(TMP.name, "dashboard.html").write_text("<p>x</p>")
        with mock.patch.object(C, "REELS_HOME", Path(TMP.name)):
            csp = self.req("/reels/raw").getheader("Content-Security-Policy")
        self.assertIn("connect-src 'none'", csp)
        self.assertNotIn("sandbox", csp)

    def test_refresh_does_not_stack_up(self):
        with mock.patch.object(C, "collect") as run:
            with C._lock:
                self.assertEqual(self.req("/api/refresh").status, 202)
            run.assert_not_called()


class Probes(unittest.TestCase):
    def test_project_mcp_files_are_never_spawned(self):
        items = [{"kind": "mcp", "name": "e", "scope": "project", "_conf": {"command": "evil"}, "conf_hash": "a"},
                 {"kind": "mcp", "name": "o", "scope": "user", "_conf": {"command": "ok"}, "conf_hash": "b"}]
        with mock.patch.object(C, "probe_stdio", return_value={"tools": 1}) as run, mock.patch.object(C, "status"):
            C.run_probes(items, {"probe_mcp": True, "probe_every": "24h"}, {}, force=True)
        self.assertEqual([c.args[0]["command"] for c in run.call_args_list], ["ok"])


if __name__ == "__main__":
    unittest.main()
