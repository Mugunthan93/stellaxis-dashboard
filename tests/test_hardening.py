"""Local-server hardening: untrusted reel text stays inert, and only localhost can reach the service."""
import atexit
import contextlib
import base64
import hashlib
import html.parser
import io
import http.client
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory()
atexit.register(TMP.cleanup)  # removed at exit, also when another test module imports this one
ENV = {"REELS_HOME": TMP.name}
sys.path.insert(0, str(REPO / "collector"))
import collect as C
from core.component import import_folder

CENTRE = import_folder(REPO / "collector" / "components" / "project_management").centre
CM = import_folder(REPO / "collector" / "components" / "claude_management")


def fake_home(case, stx: Path, claude: Path | None = None) -> None:
    """Point collect, the command centre and a fresh core at a temp home, until the test (or class) ends."""
    claude = claude or stx / ".claude"
    add = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    for patch in (mock.patch.dict(os.environ, {"STELLAXIS_HOME": str(stx), "CLAUDE_CONFIG_DIR": str(claude)}),
                  mock.patch.object(C, "STX_HOME", stx), mock.patch.object(C, "CLAUDE_DIR", claude)):
        patch.start()
        add(patch.stop)
    C._core = None
    add(setattr, C, "_core", None)


class Page(html.parser.HTMLParser):
    """Parsed the way a browser reads it: every attribute name and the text of each script."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.attrs, self.scripts, self.in_script = [], [], False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.attrs += [a for a, _ in attrs]
        self.in_script = tag == "script"
        if self.in_script:
            self.scripts.append("")

    def handle_endtag(self, tag):
        self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data

    def handlers(self):
        return [a for a in self.attrs if a.startswith("on")]


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


    def test_kept_tag_cannot_land_inside_a_link_url(self):
        out = self.b.inline('[t](https://b<a href="https://x/onmouseover=fetch(1)//">)')
        self.assertEqual(Page(out).handlers(), [], out)
        self.assertIn('href="#"', out)

    def test_nul_in_a_note_cannot_forge_a_placeholder(self):
        self.assertEqual(self.b.inline('<a href="https://a.b/">x\x000\x00'), '<a href="https://a.b/">x0')

    def test_built_page_has_no_handlers_and_one_script_the_csp_allows(self):
        note = ('---\nposted: <img src=x onerror=alert(1)>\ntopic: AI security\n---\n# T\n\n**TL;DR:** tl\n\n'
                '[t](https://b<a href="https://x/onmouseover=fetch(1)//">)\n')
        Path(TMP.name, "2026-11-01-abc.md").write_text(note)
        self.addCleanup(Path(TMP.name, "2026-11-01-abc.md").unlink)
        with mock.patch("builtins.print"):
            self.b.main()
        raw = Path(TMP.name, "dashboard.html").read_bytes()
        page = Page(raw.decode("utf-8"))
        self.assertEqual(page.handlers(), [])
        self.assertEqual(len(page.scripts), 1)
        digest = base64.b64encode(hashlib.sha256(page.scripts[0].encode("utf-8")).digest()).decode()
        self.assertEqual(C.reels_scripts(raw), f"script-src 'sha256-{digest}'; ")


class ReelsCsp(unittest.TestCase):
    def test_injected_script_earlier_in_the_page_is_not_approved(self):
        own = C.reels_scripts(b"<p>x</p><script>good()</script>")
        self.assertEqual(C.reels_scripts(b"<script>evil()</script><p>x</p><script>good()</script>"), own)

    def test_page_with_no_script_allows_none(self):
        self.assertEqual(C.reels_scripts(b"<p>x</p>"), "script-src 'none'; ")


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
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            t.main("q.json")
        self.assertEqual(out.getvalue(), "1 queued, 0 dropped\n")
        self.assertEqual([o["code"] for o in json.loads((tools / "q.json").read_text())], ["DfGood_1-x"])

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
        cls.home = tempfile.TemporaryDirectory()
        fake_home(cls, Path(cls.home.name))
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.home.cleanup()

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
        self.assertEqual(self.req("/project?id=x").status, 200)
        self.assertIn('id="new-session"', self.req("/project?id=x").body)
        self.assertNotIn('class="sx-side"', self.req("/").body)  # Home is the full-screen voice portal
        self.assertIn('href="/overview"', self.req("/project?id=x").body)  # every other page keeps the sidebar

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
        self.assertIn("script-src 'none'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("sandbox", csp)
        Path(TMP.name, "dashboard.html").write_text("<p>x</p><script>go()</script>")
        with mock.patch.object(C, "REELS_HOME", Path(TMP.name)):
            csp = self.req("/reels/raw").getheader("Content-Security-Policy")
        self.assertIn("script-src 'sha256-" + base64.b64encode(hashlib.sha256(b"go()").digest()).decode() + "'", csp)

    def test_refresh_does_not_stack_up(self):
        with mock.patch.object(C, "collect") as run:
            with C._lock:
                self.assertEqual(self.req("/api/refresh").status, 202)
            run.assert_not_called()


class RouteMatrix(unittest.TestCase):
    """Every page and API route through the real Handler: it answers on our own Host, and is refused on any other."""
    LIVE, DONE = "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"
    REQ = "a" * 32

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name)
        stx, claude, reels, repo = root / "stx", root / "claude", root / "reels", root / "repo"
        for d in (stx / "approvals" / "on", claude / "projects" / "p", claude / "sessions", reels / "images", repo):
            d.mkdir(parents=True)
        fake_home(self, stx, claude)
        for patch in (mock.patch.object(C, "REELS_HOME", reels),
                      mock.patch.object(C.settings_api, "STX_HOME", stx), mock.patch.object(C.settings_api, "CLAUDE_DIR", claude),
                      mock.patch.object(C, "collect")):
            patch.start()
            self.addCleanup(patch.stop)
        (reels / "dashboard.html").write_text("<p>x</p>")
        (reels / "images" / "a.jpg").write_bytes(b"x")
        fake = root / "claude-bin"
        fake.write_text('#!/bin/sh\n[ "$1" = agents ] && echo \'[{"sessionId": "%s", "status": "idle"}]\' && exit 0\n'
                        'echo "backgrounded \u00b7 44444444-4444-4444-4444-444444444444"\n' % self.LIVE)
        fake.chmod(0o755)
        self.repo = str(repo)
        self.fake = str(fake)
        (stx / "config.json").write_text(json.dumps(self.config()))
        (stx / "projects.json").write_text(json.dumps({"projects": [{"id": "w", "name": "W", "repos": [
            {"path": self.repo, "abs": self.repo, "exists": True, "state": {}, "branches": [{"name": "main", "sessions": [
                {"id": self.LIVE, "path": self.repo}, {"id": self.DONE, "path": self.repo}]}]}]}]}))
        (claude / "settings.json").write_text(json.dumps({"hooks": {"PermissionRequest": [{"hooks": [{"command": "approval_hook.py"}]}]}}))
        (claude / "projects" / "p" / f"{self.DONE}.jsonl").write_text(json.dumps(
            {"type": "assistant", "timestamp": "t", "message": {"content": [{"type": "text", "text": "yo"}]}}) + "\n")
        (stx / "approvals" / f"{self.REQ}.req").write_text(json.dumps(
            {"id": self.REQ, "session_id": self.LIVE, "tool": "Bash", "summary": "ls", "at": "t", "extra": {}, "cwd": ""}))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def config(self) -> dict:
        return {"claude_bin": self.fake, "projects": [{"id": "w", "name": "W", "repos": [self.repo], "tests": {self.repo: ["/usr/bin/true"]}}]}

    def routes(self):
        return [("GET", p, None, code) for p, code in (
            ("/", 200), ("/overview", 200), ("/index.html", 200), ("/project?id=w", 200), ("/hierarchy", 200), ("/hierarchy.html", 200),
            ("/settings", 200), ("/settings.html", 200), ("/home.html", 200), ("/project.html", 200), ("/reels", 301), ("/reels/", 200),
            ("/reels/raw", 200), ("/reels/images/a.jpg", 200), ("/snapshot.json", 200), ("/api/snapshot", 200), ("/api/status", 200),
            ("/api/refresh", 202), ("/api/settings", 200), ("/api/projects", 200), ("/api/approvals", 200),
            ("/api/projects/w/tasks", 200), ("/api/projects/unassigned/tasks", 200), ("/api/projects/w/work", 200),
            (f"/api/sessions/{self.DONE}/reply", 200), ("/api/jarvis", 200))] + [
            ("POST", "/api/jarvis/hush", {}, 200), ("POST", "/api/jarvis/hear", {}, 415),
            ("POST", f"/api/approvals/session/{self.LIVE}", {"on": True}, 200), ("POST", f"/api/approvals/{self.REQ}", {"allow": False}, 200),
            ("POST", "/api/projects/w/tasks", {"add": "x"}, 200), ("POST", "/api/projects/w/tests/run", {"repo": self.repo}, 200),
            ("POST", "/api/sessions/new", {"project": "w", "folder": self.repo, "prompt": "hi"}, 200),
            ("POST", f"/api/sessions/{self.DONE}/message", {"prompt": "hi"}, 200),
            ("POST", "/api/settings/preview", {}, 422), ("POST", "/api/settings/save", {}, 422),
            ("GET", "/api/projects/new-names", None, 200), ("POST", "/api/projects/new-names", {"apply": False}, 200)]

    def req(self, method, path, body, **headers):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=20)
        self.addCleanup(c.close)
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", headers.get("host") or f"127.0.0.1:{self.srv.server_address[1]}")
        if headers.get("origin"):
            c.putheader("Origin", headers["origin"])
        raw = None if body is None else json.dumps(body).encode()
        if raw is not None:
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", str(len(raw)))
        c.endheaders(raw)
        r = c.getresponse()
        r.read()
        return r.status

    def test_every_route_answers_our_host_and_refuses_any_other(self):
        for method, path, body, code in self.routes():
            self.assertEqual(self.req(method, path, body), code, path)
        for _ in range(100):  # the test run writes under this temp home, so it has to end before cleanup
            if not CENTRE._RUNNING:
                break
            time.sleep(0.05)
        for method, path, body, _ in self.routes():
            self.assertEqual(self.req(method, path, body, host="evil.example"), 403, path)
            self.assertEqual(self.req(method, path, body, origin="http://evil.example"), 403, path)


class Install(unittest.TestCase):
    def test_service_gets_a_path_that_finds_claude_and_homebrew(self):
        text = (REPO / "collector" / "install.sh").read_text()
        self.assertIn("<key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:$HOME/.local/bin:", text)
        self.assertEqual(subprocess.run(["bash", "-n", str(REPO / "collector" / "install.sh")]).returncode, 0)


class IndexFailure(unittest.TestCase):
    def test_index_error_stays_on_the_idle_status(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(CENTRE, "live", return_value=None), \
                mock.patch.object(CENTRE, "app_records", return_value={}), \
                mock.patch.object(CENTRE, "build_index", side_effect=ValueError("boom")):
            fake_home(self, Path(t))
            C.collect({"scan_roots": [], "claude_bin": "/no/claude"})
            st = json.loads(Path(t, "status.json").read_text())
        self.assertEqual((st["phase"], st["index_error"]), ("idle", "project_management: ValueError: boom"))
        self.assertIn("index_error", (REPO / "dashboard" / "shell.html").read_text())
        self.assertIn("core_error", (REPO / "dashboard" / "shell.html").read_text())

    def test_collection_clears_stale_switches(self):
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(CENTRE, "live", return_value=[]) as live, \
                mock.patch.object(CENTRE, "clear_stale") as clear, \
                mock.patch.object(CENTRE, "app_records", return_value={}):
            fake_home(self, Path(t))
            C.collect({"scan_roots": [], "claude_bin": "/no/claude"})
            self.assertIsNone(json.loads(Path(t, "status.json").read_text())["index_error"])
        live.assert_called_once_with("/no/claude")
        clear.assert_called_once_with([])


class PageNames(unittest.TestCase):
    def test_pages_and_the_agent_use_the_new_names(self):
        old = "work" + "space"
        for name in ("dashboard/home.html", "dashboard/project.html", "dashboard/shell.html", "agents/jarvis.md", "DESIGN.md"):
            text = (REPO / name).read_text()
            self.assertNotIn(old, text.lower(), name)
        for name in ("home.html", "project.html", "shell.html"):
            self.assertIn("/api/projects", (REPO / "dashboard" / name).read_text(), name)
        self.assertFalse((REPO / "dashboard" / (old + ".html")).exists())
        shell = (REPO / "dashboard" / "shell.html").read_text()
        self.assertIn('href="/project?id=', shell)
        self.assertIn("<h2>Projects</h2>", shell)
        self.assertIn("/api/projects", (REPO / "agents" / "jarvis.md").read_text())
        self.assertIn("projects.json", (REPO / "agents" / "jarvis.md").read_text())


class Probes(unittest.TestCase):
    def test_project_mcp_files_are_never_spawned(self):
        items = [{"kind": "mcp", "name": "e", "scope": "project", "_conf": {"command": "evil"}, "conf_hash": "a"},
                 {"kind": "mcp", "name": "o", "scope": "user", "_conf": {"command": "ok"}, "conf_hash": "b"}]
        with mock.patch.object(CM.setup, "probe_stdio", return_value={"tools": 1}) as run:
            CM.setup.run_probes(items, {"probe_mcp": True, "probe_every": "24h"}, {}, lambda *a, **k: None, "t", force=True)
        self.assertEqual([c.args[0]["command"] for c in run.call_args_list], ["ok"])


if __name__ == "__main__":
    unittest.main()
