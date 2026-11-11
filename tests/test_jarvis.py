"""Jarvis: the briefing, ask, hear, say and hush routes, which live in unplaced/jarvis.py.

  python3 -m unittest tests.test_jarvis
"""
import contextlib
import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import unplaced.jarvis as J  # noqa: E402
from core.app import Core  # noqa: E402
from core.component import import_folder  # noqa: E402

COMPS = Path(__file__).resolve().parent.parent / "collector" / "components"
W = import_folder(COMPS / "project_management").centre


class BriefingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        cls.old = os.environ.get("STELLAXIS_HOME")
        os.environ["STELLAXIS_HOME"] = str(cls.home)
        cls.cfg = {"claude_bin": "/no/such/claude"}
        core = Core(cls.home, cls.home / ".claude", COMPS)
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not J.handle(self, "GET", cls.cfg, core):
                    self.send_error(404)

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        if cls.old is None:
            os.environ.pop("STELLAXIS_HOME", None)
        else:
            os.environ["STELLAXIS_HOME"] = cls.old
        cls.tmp.cleanup()

    def get(self, path):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", path)
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if r.status == 200 else None)

    def test_jarvis_briefing_or_nulls(self):
        p = self.home / "jarvis" / "briefing.md"
        p.unlink(missing_ok=True)
        self.assertEqual(self.get("/api/jarvis"), (200, {"text": None, "written": None}))
        p.parent.mkdir(exist_ok=True)
        p.write_text("# Briefing\n- one")
        code, body = self.get("/api/jarvis")
        self.assertEqual((code, body["text"]), (200, "# Briefing\n- one"))
        self.assertTrue(body["written"])

    def test_the_command_centre_no_longer_answers_jarvis_routes(self):
        for name in ("_jarvis", "_jarvis_ask", "_jarvis_voice", "transcribe", "ask", "say", "_ASK_LOCK", "_HEAR_LOCK", "_SAY_LOCK"):
            self.assertFalse(hasattr(W, name), name)
        self.assertNotIn("/api/jarvis", inspect.getsource(W.handle))


class JarvisAskTest(unittest.TestCase):
    """POST /api/jarvis/ask with fake ffmpeg, whisper-cli and claude that log what they were given."""

    def setUp(self):
        import http.server
        import threading
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = d = Path(self.tmp.name)
        self.old = {k: os.environ.get(k) for k in ("STELLAXIS_HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["STELLAXIS_HOME"] = str(d / "stx")
        os.environ["CLAUDE_CONFIG_DIR"] = str(d / "claude")
        (d / "stx" / "jarvis").mkdir(parents=True)
        (d / "stx" / "jarvis" / "briefing.md").write_text("# Briefing\nPartHub has 240 unpushed commits.")
        log = d / "log.jsonl"

        def fake(name, body):
            p = d / name
            p.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                         f"open({str(log)!r}, 'a').write(json.dumps({{'bin': {name!r}, 'argv': sys.argv[1:]}}) + '\\n')\n" + body)
            p.chmod(0o755)
            return str(p)

        # ffmpeg copies the recording to the wav path; whisper prints the wav's text; claude echoes the question
        self.cfg = {
            "ffmpeg_bin": fake("ffmpeg", "a = sys.argv[1:]\nopen(a[-1], 'wb').write(open(a[a.index('-i') + 1], 'rb').read())\n"),
            "whisper_bin": fake("whisper", "a = sys.argv[1:]\nprint(open(a[a.index('-f') + 1]).read())\n"),
            "claude_bin": fake("claude", "a = sys.argv[1:]\nif a[:1] == ['agents']:\n    print('[]')\n"
                                         "else:\n    print('You asked: ' + sys.stdin.read())\n"),
            "say_bin": fake("say", f"open({str(d / 'spoken.txt')!r}, 'w').write(sys.stdin.read())\n"),
            "whisper_model": str(d / "model.bin"), "projects": [{"id": "p", "name": "PartHub"}]}
        self.log = log
        cfg = self.cfg
        core = Core(d / "stx", d / "claude", COMPS)

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if not J.handle(self, "POST", cfg, core):
                    self.send_error(404)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def post(self, body: bytes, ctype: str, path="/api/jarvis/ask"):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1])
        c.request("POST", path, body, {"Content-Type": ctype})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def calls(self, name):
        return [r["argv"] for r in map(json.loads, self.log.read_text().splitlines()) if r["bin"] == name]

    def test_recording_is_heard_then_answered_by_a_read_only_claude(self):
        code, body = self.post(b"what is running in PartHub", "audio/webm")
        self.assertEqual((code, body), (200, {"heard": "what is running in PartHub", "answer": "You asked: what is running in PartHub"}))
        self.assertIn("Jarvis, Stellaxis, PartHub", self.calls("whisper")[0])
        a = [x for x in self.calls("claude") if x[:1] != ["agents"]][0]
        self.assertEqual(a[a.index("--tools") + 1], "Read,Grep,Glob")
        self.assertIn("240 unpushed commits", a[a.index("--append-system-prompt") + 1])  # the briefing is its context

    def test_typed_question_skips_whisper(self):
        code, body = self.post(json.dumps({"question": "hello"}).encode(), "application/json")
        self.assertEqual((code, body["answer"]), (200, "You asked: hello"))
        self.assertFalse(self.log.exists() and self.calls("whisper"))

    def test_silence_is_not_sent_to_claude(self):
        code, body = self.post(b"[BLANK_AUDIO]", "audio/webm")
        self.assertEqual(code, 422)
        self.assertEqual([x for x in self.calls("claude") if x[:1] != ["agents"]], [])

    def test_empty_recording_and_other_types_are_refused(self):
        self.assertEqual(self.post(b"", "audio/webm")[0], 400)
        self.assertEqual(self.post(b"hi", "text/plain")[0], 415)

    def test_hear_only_transcribes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.post(b"jarvis show PartHub", "audio/webm", "/api/jarvis/hear")
        self.assertIn("jarvis heard", out.getvalue())
        self.assertEqual(result, (200, {"heard": "jarvis show PartHub"}))
        self.assertEqual([x for x in self.calls("claude") if x[:1] != ["agents"]], [])
        self.assertEqual(self.post(b"x", "application/json", "/api/jarvis/hear")[0], 415)

    def test_say_speaks_the_text_through_the_mac(self):
        self.assertEqual(self.post(json.dumps({"text": "All clear."}).encode(), "application/json", "/api/jarvis/say"), (200, {}))
        self.assertEqual((self.dir / "spoken.txt").read_text(), "All clear.")
        self.assertEqual(self.calls("say")[0], ["-f", "-"])  # the text goes on stdin, never as an argument
        self.assertEqual(self.post(json.dumps({"text": ""}).encode(), "application/json", "/api/jarvis/say")[0], 400)
        self.assertEqual(self.post(b"{}", "application/json", "/api/jarvis/hush"), (200, {}))

    def test_one_question_at_a_time(self):
        J._ASK_LOCK.acquire()
        try:
            self.assertEqual(self.post(json.dumps({"question": "hi"}).encode(), "application/json")[0], 429)
        finally:
            J._ASK_LOCK.release()


if __name__ == "__main__":
    unittest.main()
