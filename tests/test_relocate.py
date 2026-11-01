"""prune.py relocate: moves a project and keeps Claude transcripts, ~/.claude.json, history and git worktrees intact.

  python3 -m unittest discover -s tests -k relocate -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRUNE = REPO / "collector" / "prune.py"
sys.path.insert(0, str(REPO / "collector"))
from prune import claude_dir_name  # noqa: E402


def jl(rows):
    return "\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n"


class Relocate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        H = self.H = Path(os.path.realpath(self.tmp.name))
        self.C = H / ".claude"
        self.app, self.other = H / "Documents" / "app", H / "Documents" / "app-other"
        self.wt, self.ext = self.app / ".claude" / "worktrees" / "wt1", H / "elsewhere" / "wt2"
        self.env = {**os.environ, "HOME": str(H), "STELLAXIS_HOME": str(H / ".stellaxis"),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull}
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        self.app.mkdir(parents=True)
        self.other.mkdir(parents=True)
        self.git(self.app, "init", "-q", "-b", "main")
        (self.app / "README.md").write_text("app\n")
        self.git(self.app, "add", ".")
        self.git(self.app, "commit", "-qm", "init")
        self.git(self.app, "worktree", "add", "-q", "-b", "wt1", str(self.wt))
        self.git(self.app, "worktree", "add", "-q", "-b", "wt2", str(self.ext))

        def session(cwd, sid):
            d = self.C / "projects" / claude_dir_name(str(cwd))
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{sid}.jsonl").write_text(jl([
                {"type": "queue-operation", "sessionId": sid},
                {"type": "user", "cwd": str(cwd), "sessionId": sid, "timestamp": "2026-10-01T10:00:00Z",
                 "message": {"role": "user", "content": f"look at {cwd}/README.md"}},
                {"type": "user", "cwd": f"{cwd}/sub", "sessionId": sid, "timestamp": "2026-10-01T10:01:00Z",
                 "message": {"role": "user", "content": "hi"}}]))
            return d
        self.t_app, self.t_wt, self.t_other = session(self.app, "s1"), session(self.wt, "s2"), session(self.other, "s3")
        (H / ".claude.json").write_text(json.dumps({"projects": {
            str(self.app): {"allowedTools": ["Bash"]}, str(self.wt): {"x": 1}, str(self.other): {"y": 2}}}))
        (self.C / "history.jsonl").write_text(jl([{"display": "a", "project": str(self.app)},
                                                  {"display": "b", "project": str(self.other)}]))
        (self.C / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"command": f"{self.app}/hook.sh"}]}}))
        (H / ".stellaxis").mkdir()
        (H / ".stellaxis" / "config.json").write_text(json.dumps({"scan_roots": [str(H / "Documents")]}))

        # Claude desktop app (Code tab) state: session records, worktree registry, presets, scheduled tasks
        D = self.D = H / "Library" / "Application Support" / "Claude"
        sess = D / "claude-code-sessions" / "acct" / "org"
        sess.mkdir(parents=True)
        (sess / "local_a.json").write_text(json.dumps(
            {"sessionId": "local_a", "cwd": str(self.wt), "originCwd": str(self.app), "worktreePath": str(self.wt),
             "title": f"notes about {self.app}"}, separators=(",", ":")))
        (sess / "local_b.json").write_text(json.dumps({"sessionId": "local_b", "cwd": str(self.other)},
                                                      separators=(",", ":")))
        (sess / "scheduled-tasks.json").write_text(json.dumps(
            {"scheduledTasks": [{"id": "t", "cwd": str(self.app)}]}, indent=2))
        (D / "git-worktrees.json").write_text(json.dumps({
            "worktrees": {"wt1": {"path": str(self.wt), "baseRepo": str(self.app),
                                  "anchors": [{"gitRoot": str(self.app), "commonDir": f"{self.app}/.git"}]}},
            "originUrls": {str(self.app).lower(): {"commonDir": f"{str(self.app).lower()}/.git"}},
            "originPins": {f"{self.app}/.git": {"folder": str(self.app)}},
            "untrackedDirGc": {"sightings": {f"{self.app}/.claude/worktrees/old": 1}}}, indent="\t"))
        (D / "ccd-session-presets.json").write_text(json.dumps(
            {"presets": [{"target": {"cwd": str(self.other)}}]}, indent="\t"))
        self.env["STELLAXIS_DESKTOP_RUNNING"] = "0"

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True,
                              check=True).stdout

    def prune(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(PRUNE), *args], env=self.env, capture_output=True, text=True,
                           timeout=120)
        if ok and r.returncode:
            raise AssertionError(f"prune {args} failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def test_dry_run_changes_nothing(self):
        out = self.prune("relocate", str(self.app), str(self.H / "code" / "app"))
        self.assertIn("dry-run", out)
        self.assertIn("settings.json", out, "unedited references are reported")
        self.assertTrue(self.app.is_dir())
        self.assertFalse((self.H / "code").exists())
        self.assertTrue(self.t_app.is_dir())

    def test_refuses_while_claude_runs_inside(self):
        (self.C / "sessions").mkdir()
        (self.C / "sessions" / "1.json").write_text(json.dumps({"pid": os.getpid(), "cwd": str(self.wt)}))
        out = self.prune("relocate", str(self.app), str(self.H / "code" / "app"), "--apply", ok=False)
        self.assertIn("close those sessions", out)
        self.assertTrue(self.app.is_dir())

    def test_refuses_while_desktop_app_runs(self):
        self.env["STELLAXIS_DESKTOP_RUNNING"] = "1"
        out = self.prune("relocate", str(self.app), str(self.H / "code" / "app"), "--apply", ok=False)
        self.assertIn("quit the Claude desktop app", out)
        self.assertTrue(self.app.is_dir())
        out = self.prune("relocate", str(self.other), str(self.H / "code" / "other"), "--apply", ok=False)
        self.assertIn("quit the Claude desktop app", out, "any desktop record under src blocks the move")

    def test_desktop_records_follow_the_move(self):
        new = self.H / "code" / "app"
        new_wt = new / ".claude" / "worktrees" / "wt1"
        before = {p: p.read_bytes() for p in self.D.rglob("*.json")}
        self.prune("relocate", str(self.app), str(new), "--apply")

        sess = self.D / "claude-code-sessions" / "acct" / "org"
        a = json.loads((sess / "local_a.json").read_text())
        self.assertEqual((a["cwd"], a["originCwd"], a["worktreePath"]), (str(new_wt), str(new), str(new_wt)))
        self.assertEqual(a["title"], f"notes about {self.app}", "free text is left as written")
        self.assertNotIn(b": ", (sess / "local_a.json").read_bytes(), "compact files stay compact")
        self.assertEqual((sess / "local_b.json").read_bytes(), before[sess / "local_b.json"], "other project untouched")
        self.assertEqual(json.loads((sess / "scheduled-tasks.json").read_text())["scheduledTasks"][0]["cwd"], str(new))

        g = json.loads((self.D / "git-worktrees.json").read_text())
        w = g["worktrees"]["wt1"]
        self.assertEqual((w["path"], w["baseRepo"]), (str(new_wt), str(new)))
        self.assertEqual(w["anchors"][0], {"gitRoot": str(new), "commonDir": f"{new}/.git"})
        self.assertEqual(g["originUrls"], {str(new).lower(): {"commonDir": f"{str(new).lower()}/.git"}},
                         "lowercased keys are matched case-insensitively")
        self.assertEqual(g["originPins"], {f"{new}/.git": {"folder": str(new)}})
        self.assertEqual(list(g["untrackedDirGc"]["sightings"]), [f"{new}/.claude/worktrees/old"])
        self.assertIn("\n\t", (self.D / "git-worktrees.json").read_text(), "tab indentation kept")
        presets = self.D / "ccd-session-presets.json"
        self.assertEqual(presets.read_bytes(), before[presets])

        self.prune("restore", "last", "--apply")
        for p, data in before.items():
            self.assertEqual(json.loads(p.read_bytes()), json.loads(data), f"{p.name} restored")

    def test_linked_worktree_outside_the_repo_can_move(self):
        new = self.H / "code" / "wt2"
        self.prune("relocate", str(self.ext), str(new), "--apply")
        self.assertEqual(self.git(new, "rev-parse", "--abbrev-ref", "HEAD").strip(), "wt2")
        listed = self.git(self.app, "worktree", "list", "--porcelain")
        self.assertIn(f"worktree {new}\n", listed)
        self.assertNotIn("prunable", listed)

    def test_apply_and_restore(self):
        new = self.H / "code" / "personal" / "app"
        new_wt = new / ".claude" / "worktrees" / "wt1"
        self.prune("relocate", str(self.app), str(new), "--apply")

        self.assertFalse(self.app.exists())
        self.assertEqual(self.git(new_wt, "rev-parse", "--abbrev-ref", "HEAD").strip(), "wt1")
        self.assertEqual(self.git(self.ext, "rev-parse", "--abbrev-ref", "HEAD").strip(), "wt2")
        listed = self.git(new, "worktree", "list", "--porcelain")
        self.assertIn(f"worktree {new_wt}\n", listed)
        self.assertNotIn("prunable", listed)

        t_new = self.C / "projects" / claude_dir_name(str(new))
        self.assertFalse(self.t_app.exists())
        rows = [json.loads(l) for l in (t_new / "s1.jsonl").read_text().splitlines()]
        self.assertEqual([r.get("cwd") for r in rows], [None, str(new), f"{new}/sub"])
        self.assertIn(str(self.app), rows[1]["message"]["content"], "message text is history, left as written")
        self.assertTrue((self.C / "projects" / claude_dir_name(str(new_wt)) / "s2.jsonl").exists())
        self.assertIn(f'"cwd":"{self.other}"', (self.t_other / "s3.jsonl").read_text(), "prefix-similar path untouched")

        cj = json.loads((self.H / ".claude.json").read_text())["projects"]
        self.assertEqual(set(cj), {str(new), str(new_wt), str(self.other)})
        self.assertEqual(cj[str(new)], {"allowedTools": ["Bash"]})
        hist = [json.loads(l)["project"] for l in (self.C / "history.jsonl").read_text().splitlines()]
        self.assertEqual(hist, [str(new), str(self.other)])

        self.prune("restore", "last", "--apply")
        self.assertTrue(self.app.is_dir())
        self.assertFalse(new.exists())
        self.assertEqual(self.git(self.wt, "rev-parse", "--abbrev-ref", "HEAD").strip(), "wt1")
        self.assertEqual(self.git(self.ext, "rev-parse", "--abbrev-ref", "HEAD").strip(), "wt2")
        self.assertTrue((self.t_app / "s1.jsonl").exists())
        self.assertIn(f'"cwd":"{self.app}"', (self.t_app / "s1.jsonl").read_text())
        self.assertIn(str(self.app), json.loads((self.H / ".claude.json").read_text())["projects"])


if __name__ == "__main__":
    unittest.main()
