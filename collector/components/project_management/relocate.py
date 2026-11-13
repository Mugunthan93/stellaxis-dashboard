"""Relocate a project folder (from prune.py): move it and rewrite everything that remembers it by path. The move is one
manifest step, `project_management.relocate`, so restore undoes it by relocating back."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from core import util
from core.undo import Plan
from core.util import read_json, short_path, write_json

WALK_SKIP = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build", "Pods", "DerivedData", ".gradle"}
CWD_RE = re.compile(rb'"cwd":"((?:[^"\\]|\\.)*)"')


def claude_dir_name(path: str) -> str:
    """How Claude Code names a folder's transcript directory under ~/.claude/projects."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def remap(p: str, src: str, dest: str):
    if p == src:
        return dest
    if p.startswith(src + "/"):
        return dest + p[len(src):]
    return None


def json_str(s: str) -> bytes:
    return json.dumps(s, ensure_ascii=False)[1:-1].encode()


def replace_path_field(data: bytes, field: str, src: str, dest: str) -> bytes:
    """Rewrite `"field":"<src>"` and `"field":"<src>/..."` in compact JSON lines; other paths stay untouched."""
    a, b = f'"{field}":"'.encode() + json_str(src), f'"{field}":"'.encode() + json_str(dest)
    return data.replace(a + b'"', b + b'"').replace(a + b"/", b + b"/")


def rewrite_file(path: Path, fn, apply: bool) -> bool:
    data = path.read_bytes()
    new = fn(data)
    if new == data:
        return False
    if apply:
        tmp = path.with_name(path.name + ".stellaxis-tmp")
        tmp.write_bytes(new)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    return True


def transcript_cwd(d: Path):
    """The folder a ~/.claude/projects/<dir> belongs to: first `cwd` recorded in its transcripts."""
    for f in sorted(d.glob("*.jsonl")):
        try:
            with open(f, "rb") as fh:
                for i, line in enumerate(fh):
                    m = CWD_RE.search(line)
                    if m:
                        return json.loads(b'"' + m.group(1) + b'"')
                    if i > 2000:
                        break
        except (OSError, ValueError):
            continue
    return None


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def git_worktrees(repo: Path) -> list:
    """[main, linked...] for the repo at `repo`, existing paths only."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    paths = [l[len("worktree "):] for l in out.splitlines() if l.startswith("worktree ")]
    return [p for i, p in enumerate(paths) if i == 0 or Path(p).exists()]


def repos_touching(src: Path) -> dict:
    """{main repo path: [linked worktree paths]} for every repo with its main or a worktree inside src."""
    found = {}
    for root, dirs, files in os.walk(src):
        if ".git" in dirs or ".git" in files:
            wts = git_worktrees(Path(root))
            if wts:
                found[wts[0]] = wts[1:]
        dirs[:] = [d for d in dirs if d not in WALK_SKIP]
    return found


def desktop_files() -> list:
    """Claude desktop app (Code tab) state that remembers folders by path."""
    DESKTOP_DIR = util.HOME / "Library" / "Application Support" / "Claude"
    if not DESKTOP_DIR.is_dir():
        return []
    files = [DESKTOP_DIR / "git-worktrees.json", DESKTOP_DIR / "ccd-session-presets.json"]
    for sub in ("claude-code-sessions", "local-agent-mode-sessions"):
        files += sorted((DESKTOP_DIR / sub).glob("*/*/local_*.json"))
        files += sorted((DESKTOP_DIR / sub).glob("*/*/scheduled-tasks.json"))
    return [f for f in files if f.is_file()]


def desktop_running() -> bool:
    forced = os.environ.get("STELLAXIS_DESKTOP_RUNNING")  # tests
    if forced is not None:
        return forced == "1"
    # ps, not pgrep: pgrep skips its own ancestors, and this often runs from a terminal inside the app
    out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
    return any(l.strip().endswith("/Claude.app/Contents/MacOS/Claude") for l in out.splitlines())


def remap_tree(v, s: str, d: str):
    """Copy of a JSON value with every string (and dict key) that is `s` or under it moved to `d`. Keys the app
    stores lowercased (git-worktrees.json originUrls) are matched case-insensitively."""
    def one(x):
        new = remap(x, s, d)
        if new is None and x == x.lower():
            new = remap(x, s.lower(), d.lower())
        return x if new is None else new
    if isinstance(v, str):
        return one(v)
    if isinstance(v, list):
        return [remap_tree(x, s, d) for x in v]
    if isinstance(v, dict):
        return {one(k): remap_tree(x, s, d) for k, x in v.items()}
    return v


def rewrite_json_paths(path: Path, s: str, d: str, apply: bool) -> bool:
    """Rewrite paths in a JSON file, keeping its layout: compact, 2-space or tab indent."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return False
    new = remap_tree(data, s, d)
    if new == data:
        return False
    if apply:
        second = raw.split("\n", 1)[1] if "\n" in raw else ""
        spaces = len(second) - len(second.lstrip(" "))
        if second.startswith("\t"):
            out = json.dumps(new, indent="\t", ensure_ascii=False)
        elif spaces:
            out = json.dumps(new, indent=spaces, ensure_ascii=False)
        else:
            out = json.dumps(new, separators=(",", ":"), ensure_ascii=False)
        tmp = path.with_name(path.name + ".stellaxis-tmp")
        tmp.write_text(out + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    return True


def reference_files(ctx) -> list:
    """Config files that may hold absolute project paths; reported, never edited."""
    lib = util.HOME / "Library"
    files = [ctx.claude_dir / "settings.json", ctx.claude_dir / "settings.local.json", ctx.home / "config.json",
             lib / "Application Support" / "Claude" / "claude_desktop_config.json",
             *[util.HOME / n for n in (".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".gitconfig")]]
    agents = lib / "LaunchAgents"
    files += sorted(agents.glob("*.plist")) if agents.is_dir() else []
    return [f for f in files if f.is_file()]


def relocate(plan, ctx, src: str, dest: str, force: bool = False, backups: bool = True) -> bool:
    """Move a project folder and everything that remembers it by path: Claude transcripts (resume + Stellaxis
    history), ~/.claude.json per-project settings, prompt history, and git worktree links."""
    src, dest = Path(src), Path(dest)   # the arguments of an action are plain strings
    s, d = str(src), str(dest)
    if not src.is_dir():
        plan.note(f"  stop: {short_path(src)} is not a folder")
        return False
    if dest.exists():
        plan.note(f"  stop: {short_path(dest)} already exists")
        return False
    if d == s or d.startswith(s + "/"):
        plan.note("  stop: destination is inside the source")
        return False
    for protected in (util.HOME, ctx.claude_dir, ctx.home):
        if remap(str(protected), s, d) is not None:
            plan.note(f"  stop: {short_path(src)} contains {short_path(protected)}")
            return False

    live = []
    for f in sorted((ctx.claude_dir / "sessions").glob("*.json")) if (ctx.claude_dir / "sessions").is_dir() else []:
        info = read_json(f, {}) or {}
        if info.get("cwd") and remap(info["cwd"], s, d) is not None and pid_alive(info.get("pid")):
            live.append(f"pid {info.get('pid')} in {short_path(info['cwd'])}")
    if live:
        plan.note("  Claude Code is running inside this folder: " + "; ".join(live))
        if not force and plan.apply:
            plan.note("  stop: close those sessions first (or pass --force)")
            return False

    desktop = [f for f in desktop_files() if rewrite_json_paths(f, s, d, False)]
    if desktop and desktop_running():
        plan.note(f"  the Claude desktop app has {len(desktop)} record(s) of this folder and is running")
        if not force and plan.apply:
            plan.note("  stop: quit the Claude desktop app first (it keeps these in memory and would write the old "
                  "paths back), then run this from Terminal")
            return False

    repos = repos_touching(src)
    proj_root = ctx.claude_dir / "projects"
    tdirs = []
    for td in sorted(proj_root.iterdir()) if proj_root.is_dir() else []:
        cwd = transcript_cwd(td) if td.is_dir() else None
        new = remap(cwd, s, d) if cwd else None
        if new:
            tdirs.append((td, cwd, new))

    plan.say(f"move folder {short_path(src)} -> {short_path(dest)}")
    plan.manifest["entries"].append({"op": "project_management.relocate", "from": s, "to": d})
    if plan.apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(s, d)

    for main, linked in repos.items():
        new_main = remap(main, s, d) or main
        new_linked = [remap(p, s, d) or p for p in linked]
        if not linked:
            continue
        plan.say(f"repair git worktree links of {short_path(new_main)} ({len(linked)} worktree(s))")
        if plan.apply:
            r = subprocess.run(["git", "-C", new_main, "worktree", "repair", *new_linked], capture_output=True, text=True)
            if r.returncode:
                plan.note(f"  warning: git worktree repair failed: {r.stderr.strip()}")

    for td, cwd, new in tdirs:
        files = list(td.rglob("*.jsonl"))
        changed = sum(rewrite_file(f, lambda b: replace_path_field(b, "cwd", s, d), plan.apply) for f in files)
        target = proj_root / claude_dir_name(new)
        if len(target.name) > 200:
            plan.note(f"  warning: {short_path(new)} is a very long path; Claude may not find these sessions to resume")
        plan.say(f"transcripts {td.name} -> {target.name} ({len(files)} file(s), cwd rewritten in {changed})")
        if not plan.apply or target == td:
            continue
        if not target.exists():
            td.rename(target)
            continue
        for child in list(td.iterdir()):  # sessions already started at the new path: merge
            if (target / child.name).exists():
                plan.note(f"  warning: {child.name} exists in both transcript folders; left in {td.name}")
            else:
                child.rename(target / child.name)
        if not any(td.iterdir()):
            td.rmdir()

    cj = read_json(ctx.claude_json, {}) or {}
    keys = [k for k in (cj.get("projects") or {}) if remap(k, s, d)]
    if keys:
        def rekey(data):
            projects = data.get("projects") or {}
            for k in [k for k in projects if remap(k, s, d)]:
                projects[remap(k, s, d)] = projects.pop(k)
        plan.say(f"rename {len(keys)} project entr{'y' if len(keys) == 1 else 'ies'} in {short_path(ctx.claude_json)}")
        if plan.apply:
            if backups:
                plan.backup_json(ctx.claude_json)
            data = read_json(ctx.claude_json, {}) or {}
            rekey(data)
            write_json(ctx.claude_json, data, indent=2)

    hist = ctx.claude_dir / "history.jsonl"
    if hist.is_file():
        fn = lambda b: replace_path_field(b, "project", s, d)
        if rewrite_file(hist, fn, False):
            plan.say(f"update prompt history in {short_path(hist)}")
            if plan.apply:
                if backups:
                    plan.backup_json(hist)
                rewrite_file(hist, fn, True)

    if desktop:
        sessions = sum(f.name.startswith("local_") for f in desktop)
        others = [f.name for f in desktop if not f.name.startswith("local_")]
        plan.say(f"update Claude desktop app records: {sessions} Code tab session(s)" + "".join(f", {n}" for n in others))
        if plan.apply:
            for f in desktop:
                if backups:
                    plan.backup_json(f)
                rewrite_json_paths(f, s, d, True)

    mentions = [short_path(f) for f in reference_files(ctx) if s.encode() in f.read_bytes()]
    if mentions:
        plan.note(f"  still mentions {short_path(src)} (not edited, check by hand): " + ", ".join(mentions))
    new_repo = remap(str(Path(__file__).resolve().parents[3]), s, d)
    if new_repo:
        plan.note(f"  this moves Stellaxis itself: afterwards run  bash {short_path(new_repo)}/collector/install.sh")
    return True


def undo_relocate(entry: dict, apply: bool, out, ctx) -> bool:
    return relocate(Plan(apply, "undo", ctx.claude_dir / "_archive", out=out), ctx, entry["to"], entry["from"], backups=False)
