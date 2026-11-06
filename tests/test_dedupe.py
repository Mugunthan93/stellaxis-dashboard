"""Usage must count each API call once, however many times a transcript replays it."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import collect as C


def turn(mid, output=10, cread=100, ts="2026-11-01T10:00:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "sessionId": "s", "requestId": "r" + mid,
            "message": {"id": mid, "model": "claude-opus-5-5", "usage": {
                "input_tokens": 1, "cache_read_input_tokens": cread, "output_tokens": output}}}


def write(path, lines, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as f:
        f.writelines(json.dumps(x) + "\n" for x in lines)


class DeferredTools(unittest.TestCase):
    def test_repeated_announcements_count_once(self):
        st = {"context": {}}
        delta = {"type": "deferred_tools_delta", "addedNames": ["mcp__srv__one", "Monitor"]}
        C._attachment(st, delta)
        C._attachment(st, delta)
        C._attachment(st, {"type": "deferred_tools_delta", "removedNames": ["Monitor"]})
        self.assertEqual(st["context"]["deferred"], {C.norm_mcp("srv"): len("mcp__srv__one") + 1})


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = C.CLAUDE_DIR
        self.addCleanup(setattr, C, "CLAUDE_DIR", self.old)
        C.CLAUDE_DIR = Path(self.tmp.name)
        self.root = C.CLAUDE_DIR / "projects" / "p"
        self.cache = {}

    def totals(self):
        files = C.parse_transcripts(self.cache)
        rows = [r for st in files.values() for r in st["models"].values()]
        hours = [r for st in files.values() for h in st["hourly"].values() for r in h.values()]
        return {"turns": sum(r[5] for r in rows), "read": sum(r[3] for r in rows),
                "out": sum(r[4] for r in rows), "hour_out": sum(r[4] for r in hours)}

    def test_replay_in_a_second_file_counts_once(self):
        write(self.root / "a.jsonl", [turn("msg_1"), turn("msg_2")])
        write(self.root / "b.jsonl", [turn("msg_1"), turn("msg_2"), turn("msg_3")])
        self.assertEqual(self.totals()["turns"], 3)

    def test_replay_far_back_in_the_same_file_counts_once(self):
        lines = [turn(f"msg_{i}") for i in range(100)]
        write(self.root / "a.jsonl", lines + lines[:5])
        self.assertEqual(self.totals()["turns"], 100)

    def test_replay_seen_on_a_later_pass_counts_once(self):
        write(self.root / "a.jsonl", [turn("msg_1")])
        self.totals()
        write(self.root / "b.jsonl", [turn("msg_1"), turn("msg_2")])
        self.assertEqual(self.totals()["turns"], 2)

    def test_streamed_copies_keep_the_final_output(self):
        write(self.root / "a.jsonl", [turn("msg_1", output=1), turn("msg_1", output=50)])
        got = self.totals()
        self.assertEqual((got["turns"], got["out"], got["hour_out"], got["read"]), (1, 50, 50, 100))

    def test_rewritten_file_is_counted_again(self):
        write(self.root / "a.jsonl", [turn("msg_1"), turn("msg_2"), turn("msg_3")])
        self.totals()
        write(self.root / "a.jsonl", [turn("msg_1")])
        self.assertEqual(self.totals()["turns"], 1)

    def test_deleted_file_releases_its_calls(self):
        write(self.root / "a.jsonl", [turn("msg_1")])
        self.totals()
        (self.root / "a.jsonl").unlink()
        self.totals()
        write(self.root / "b.jsonl", [turn("msg_1")])
        self.assertEqual(self.totals()["turns"], 1)


if __name__ == "__main__":
    unittest.main()
