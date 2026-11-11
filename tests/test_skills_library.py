"""Skill Library: harvest from reel notes, catalog, install into a project.

  python3 -m unittest discover -s tests -k test_skills_library

Fake reel notes name three local git repos (a skills folder, a whole-repo skill, a repo with none) and one that
doesn't exist; harvest clones them over file:// instead of GitHub.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "skills_library" / "library.py"


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_repo(root, files, links=None):
    root.mkdir(parents=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if rel.endswith(".sh"):
            p.chmod(0o755)
    for rel, target in (links or {}).items():
        (root / rel).symlink_to(target)
    sh("git", "init", "-q", "-b", "main", cwd=root)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=root)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=root)


NOTE = """---
source: https://www.instagram.com/reel/{code}/
topic: {topic}
tags: [{tags}]
projects: [{projects}]
---
# {title}

## Tools, repos & links
{body}
"""


class SkillsLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        remotes = cls.tmp / "remotes"
        make_repo(remotes / "acme" / "skills", {
            "README.md": "x",
            "skills/ui-polish/SKILL.md": "---\nname: ui-polish\ndescription: >\n  Polish UI screens\n  with care.\n---\nBody",
            "skills/ui-polish/ref.md": "ref",
            "skills/deploy/SKILL.md": "---\nname: deploy\ndescription: \"Ship it\"\n---\n",
            "skills/deploy/run.sh": "echo hi",
            "src/big.c": "int main(){}",
            "tests/fixtures/evil/SKILL.md": "---\nname: evil\n---\n",  # a scanner's sample, not offered
            ".claude/skills/ui-polish/SKILL.md": "---\nname: ui-polish\n---\n",  # mirror of skills/ui-polish
        })
        cls.secret = cls.tmp / "id_rsa"
        cls.secret.write_text("PRIVATE KEY")
        make_repo(remotes / "sneaky" / "skill", {"skills/leak/SKILL.md": "---\nname: leak\n---\n"},
                  links={"skills/leak/ref.md": str(cls.secret)})
        make_repo(remotes / "solo" / "taste", {"SKILL.md": "---\nname: taste\ndescription: Taste rules\n---\n", "extra.md": "e"})
        make_repo(remotes / "tool" / "plain", {"README.md": "no skills here"})
        reels = cls.tmp / "reels"
        reels.mkdir()
        (reels / "2026-11-01-A.md").write_text(NOTE.format(code="A", topic="AI for design & UI", tags="design, ui",
            projects="budget-app", title="Design skills", body="- **Acme** (`acme/skills`), `sneaky/skill` and `solo/taste.`\n- github.com/tool/plain"))
        (reels / "2026-11-02-B.md").write_text(NOTE.format(code="B", topic="Claude Code & skills", tags="skills",
            projects="", title="A skill with no repo", body="- **Mystery skill** (repo not shown)"))
        (reels / "2026-11-03-C.md").write_text(NOTE.format(code="C", topic="AI coding tools", tags="cli",
            projects="", title="Gone", body="- `ghost/missing`"))
        (reels / "INDEX.md").write_text("# index, mentions `acme/other` but is not a note")
        cls.home = cls.tmp / "home"
        (cls.home / ".claude" / "skills" / "taste").mkdir(parents=True)
        (cls.home / ".claude" / "skills" / "taste" / "SKILL.md").write_text("---\nname: taste\n---\n")
        cls.code = cls.home / "code"
        cls.proj = cls.code / "budget-app"
        cls.proj.mkdir(parents=True)
        sh("git", "init", "-q", cwd=cls.proj)
        cls.env = dict(os.environ, HOME=str(cls.home), SKILLS_HOME=str(cls.tmp / "lib"), REELS_HOME=str(reels),
                       SKILLS_CODE_ROOT=str(cls.code), CLAUDE_CONFIG_DIR=str(cls.home / ".claude"),
                       SKILLS_GIT_BASE=f"file://{remotes}/")
        cls.out = cls.run_lib("harvest").stdout
        cls.cat = json.loads((cls.tmp / "lib" / "catalog.json").read_text())

    @classmethod
    def run_lib(cls, *args, cwd=None, check=True):
        return subprocess.run([sys.executable, str(LIB), *args], env=cls.env, cwd=cwd or cls.tmp,
                              capture_output=True, text=True, check=check)

    def skill(self, name):
        return next(s for s in self.cat["skills"] if s["name"] == name)

    def test_finds_skills_in_folders_and_whole_repo(self):
        self.assertEqual(sorted(s["id"] for s in self.cat["skills"]),
                         ["acme/skills/skills/deploy", "acme/skills/skills/ui-polish", "sneaky/skill/skills/leak", "solo/taste"])

    def test_only_skill_folders_are_checked_out(self):
        d = self.tmp / "lib" / "repos" / "acme__skills"
        self.assertTrue((d / "skills" / "ui-polish" / "ref.md").exists())
        self.assertFalse((d / "src").exists())
        self.assertFalse((d / "README.md").exists())

    def test_repo_without_skills_is_not_kept_and_failure_is_listed(self):
        self.assertFalse((self.tmp / "lib" / "repos" / "tool__plain").exists())
        self.assertEqual(self.cat["repos"]["no_skills"], 1)
        self.assertEqual([f["repo"] for f in self.cat["repos"]["failed"]], ["ghost/missing"])
        self.assertNotIn("acme/other", json.dumps(self.cat))

    def test_metadata_from_skill_and_reel(self):
        s = self.skill("ui-polish")
        self.assertEqual(s["description"], "Polish UI screens with care.")
        self.assertEqual(s["use_cases"], ["AI for design & UI"])
        self.assertEqual(s["projects"], ["budget-app"])
        self.assertEqual(s["reels"][0]["source"], "https://www.instagram.com/reel/A/")
        self.assertEqual(self.skill("deploy")["description"], "Ship it")

    def test_flags_scripts_and_global_installs(self):
        self.assertEqual(self.skill("deploy")["scripts"], ["run.sh"])
        self.assertFalse(self.skill("ui-polish")["has_scripts"])
        self.assertTrue(self.skill("taste")["installed_globally"])
        self.assertEqual([r["title"] for r in self.cat["no_repo_shown"]], ["A skill with no repo"])

    def test_search(self):
        out = self.run_lib("search", "polish", "screens").stdout
        self.assertTrue(out.startswith("ui-polish  ["), out)  # its own name/description beat the reel's tags
        self.assertIn("No match", self.run_lib("search", "kubernetes").stdout)

    def test_rerun_fetches_only_new_repos(self):
        self.assertIn("fetching 0", self.run_lib("harvest").stdout)

    def test_zz_symlink_never_reaches_the_file_behind_it(self):
        dest = self.proj / ".claude" / "skills" / "leak"
        self.run_lib("install", "leak", "--project", str(self.proj))
        ref = dest / "ref.md"
        self.assertFalse(ref.is_symlink())
        self.assertNotIn("PRIVATE KEY", ref.read_text())
        self.assertNotIn("PRIVATE KEY", self.run_lib("show", "leak").stdout)

    def test_no_temp_files_left(self):
        self.assertEqual([p.name for p in (self.tmp / "lib").iterdir() if ".tmp" in p.name], [])

    def test_zz_install_into_project(self):
        sub = self.proj / "src"
        sub.mkdir()
        self.run_lib("install", "ui-polish", cwd=sub)
        dest = self.proj / ".claude" / "skills" / "ui-polish"
        self.assertTrue((dest / "SKILL.md").exists() and (dest / "ref.md").exists())
        meta = json.loads((dest / ".library.json").read_text())
        self.assertEqual((meta["id"], meta["adapted"]), ("acme/skills/skills/ui-polish", False))
        cat = json.loads((self.tmp / "lib" / "catalog.json").read_text())
        self.assertEqual(next(s for s in cat["skills"] if s["name"] == "ui-polish")["installed_in"], ["~/code/budget-app"])
        # no silent overwrite of an adapted copy
        r = self.run_lib("install", "ui-polish", "--project", str(self.proj), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force", r.stderr)
        # whole-repo skill copies without .git
        self.run_lib("install", "solo/taste", "--project", str(self.proj))
        t = self.proj / ".claude" / "skills" / "taste"
        self.assertTrue((t / "extra.md").exists())
        self.assertFalse((t / ".git").exists())


if __name__ == "__main__":
    unittest.main()
