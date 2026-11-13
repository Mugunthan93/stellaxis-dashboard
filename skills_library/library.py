"""Skill Library: Claude skills from the GitHub repos the reel notes name, ready to install into any project.

  python3 library.py harvest [--refresh]        download new repos named in the reel notes, rebuild the catalog
  python3 library.py catalog                    rebuild catalog.json offline (after installs, or new notes)
  python3 library.py search <words...>          best matches by name, description, use case, tags
  python3 library.py match [words...]           skills that fit a whole message; with no words, the prompt hook
  python3 library.py show <id|name>             one skill: where it came from, its files, its SKILL.md
  python3 library.py install <id|name> [--project DIR] [--force]

Stdlib + git. Data lives in $SKILLS_HOME (default ~/Knowledge/skills): repos/<owner>__<repo> (sparse clones holding
only the skill folders), repos.json (per-repo result), catalog.json (what the dashboard's /skills page reads).
Downloaded skills are untrusted: nothing here runs any file from them; skills that ship scripts are flagged.
"""
import argparse, concurrent.futures, datetime, json, math, os, re, shutil, subprocess, sys
from pathlib import Path

HOME = Path(os.path.expanduser(os.environ.get("SKILLS_HOME", "~/Knowledge/skills")))
REELS = Path(os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels")))
CODE = Path(os.path.expanduser(os.environ.get("SKILLS_CODE_ROOT", "~/code")))  # where projects with installs live
CLAUDE = Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")))
GIT_BASE = os.environ.get("SKILLS_GIT_BASE", "https://github.com/")
REPOS, REPOS_JSON, CATALOG = HOME / "repos", HOME / "repos.json", HOME / "catalog.json"
SCRIPT_EXT = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd"}
REPO_REF = re.compile(r"`([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`|github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
NOT_OFFERED = {"test", "tests", "__tests__", "fixtures", "testdata", "benchmark", "benchmarks", "legacy", "eval", "evals",
               "node_modules", "vendor"}  # skills here are a repo's samples (skillspector's are malicious on purpose)
BIG_REPO = 3000  # files; a root SKILL.md in a repo this big gets only its top-level files


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load(p, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")  # an install during a harvest writes the catalog too
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


# ------------------------------------------------------------------ reel notes

def frontmatter(text):
    """key: value pairs from a --- block; [a, b] lists; quoted values; | and > block scalars."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out, key, block = {}, None, None
    for line in text[3:end].splitlines():
        if block is not None and (line.startswith((" ", "\t")) or not line.strip()):
            block.append(line.strip())
            out[key] = " ".join(b for b in block if b)
            continue
        block = None
        m = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, v = m.group(1), m.group(2).strip()
        if v in ("|", ">", "|-", ">-"):
            block, out[key] = [], ""
        elif v.startswith("[") and v.endswith("]"):
            out[key] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            out[key] = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
    return out


def norm_repo(ref):
    ref = re.sub(r"(\.git)?\.*$", "", ref)
    owner, _, name = ref.partition("/")
    return f"{owner}/{name}" if owner and name and not name.endswith(".") else None


def read_notes():
    """Each reel note: its metadata and the owner/repo references in its text."""
    notes = []
    for f in sorted(REELS.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        fm = frontmatter(text)
        if not fm.get("source"):
            continue  # INDEX.md, ACTIONS.md and other non-note files
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), f.stem)
        refs = {norm_repo(a or b) for a, b in REPO_REF.findall(text)} - {None}
        notes.append({"file": f.name, "title": title, "source": fm.get("source", ""), "topic": fm.get("topic", ""),
                      "tags": fm.get("tags") or [], "projects": fm.get("projects") or [], "repos": sorted(refs)})
    return notes


# ------------------------------------------------------------------ download

def git(*args, cwd=None, timeout=180):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    # core.symlinks=false: a repo's symlink becomes a small text file holding its target, so a skill can't point
    # at ~/.ssh/... and have install copy (or show print) the file behind it
    safe = ["-c", "core.symlinks=false", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=",
            "-c", "filter.lfs.required=false"]
    return subprocess.run(["git", *safe, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def repo_dir(repo):
    return REPOS / repo.replace("/", "__")


def offered(dirs):
    """Skill folders a repo offers: no test/fixture/benchmark copies, and one folder per skill name when the repo
    mirrors a skill into .claude/, .cursor/, .agents/ etc. (a visible folder wins, then the shortest path)."""
    best = {}
    for d in dirs:
        parts = [] if d == "." else d.split("/")
        if NOT_OFFERED & {x.lower() for x in parts}:
            continue
        key = (parts[-1] if parts else ".").lower()
        rank = (any(x.startswith(".") for x in parts), len(parts), d)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, d)
    return sorted(d for _, d in best.values())


def fetch_repo(repo):
    """Sparse, shallow clone holding only the folders that contain a SKILL.md. Never runs anything from the repo:
    no checkout hooks (fresh clone has none), no LFS smudge, no submodules."""
    d = repo_dir(repo)
    shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout", GIT_BASE + repo, str(d))
        if r.returncode:
            shutil.rmtree(d, ignore_errors=True)
            return {"status": "failed", "error": (r.stderr.strip().splitlines() or ["clone failed"])[-1][:200]}
        tree = git("ls-tree", "-r", "--name-only", "HEAD", cwd=d).stdout.splitlines()
        dirs = offered([str(Path(p).parent) for p in tree if Path(p).name.lower() == "skill.md"])
        if not dirs:
            shutil.rmtree(d, ignore_errors=True)
            return {"status": "no-skills", "files": len(tree)}
        pats = ["/*", "!/*/"] if "." in dirs and len(tree) > BIG_REPO else \
               (["/*"] if "." in dirs else [f"/{x}/" for x in dirs])
        git("sparse-checkout", "set", "--no-cone", *pats, cwd=d)
        r = git("checkout", "--quiet", cwd=d, timeout=600)
        if r.returncode:
            shutil.rmtree(d, ignore_errors=True)
            return {"status": "failed", "error": r.stderr.strip()[-200:]}
        commit = git("rev-parse", "HEAD", cwd=d).stdout.strip()
        return {"status": "ok", "commit": commit, "skills": len(dirs)}
    except subprocess.TimeoutExpired:
        shutil.rmtree(d, ignore_errors=True)
        return {"status": "failed", "error": "timed out"}


def harvest(refresh=False, workers=8):
    notes = read_notes()
    wanted = sorted({r for n in notes for r in n["repos"]}, key=str.lower)
    state = load(REPOS_JSON, {})
    seen = {k.lower() for k in state}
    todo = [r for r in wanted if refresh or r.lower() not in seen]
    # the same repo spelled two ways (greensock/GSAP, greensock/gsap) is fetched once
    todo = list({r.lower(): r for r in todo}.values())
    print(f"{len(wanted)} repos named in {len(notes)} notes; fetching {len(todo)}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        for repo, res in zip(todo, ex.map(fetch_repo, todo)):
            state = {k: v for k, v in state.items() if k.lower() != repo.lower()}
            state[repo] = dict(res, checked=now())
            print(f"  {res['status']:9} {repo}" + (f"  ({res.get('skills')} skills)" if res.get("skills") else "")
                  + (f"  {res['error']}" if res.get("error") else ""), flush=True)
            save(REPOS_JSON, state)
    return build_catalog(notes)


# ------------------------------------------------------------------ catalog

def global_skills():
    """Names of skills every session already has: ~/.claude/skills and installed plugins' skills."""
    names = set()
    for md in [*(CLAUDE / "skills").glob("*/SKILL.md"), *(CLAUDE / "plugins" / "cache").glob("*/*/*/skills/*/SKILL.md")]:
        names.add(md.parent.name)  # folder name and declared name can differ (taste-skill / design-taste-frontend)
        try:
            names.add(frontmatter(md.read_text(encoding="utf-8", errors="replace")).get("name") or md.parent.name)
        except OSError:
            pass
    return names


def project_installs():
    """{skill id: [project path]} from the .library.json that install leaves in each copy."""
    out = {}
    for meta in CODE.glob("*/.claude/skills/*/.library.json"):
        sid = load(meta, {}).get("id")
        if sid:
            out.setdefault(sid, []).append("~/" + str(meta.parents[3].relative_to(Path.home()))
                                           if meta.parents[3].is_relative_to(Path.home()) else str(meta.parents[3]))
    return out


def skill_files(root):
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts]
    return files


def build_catalog(notes=None):
    notes = read_notes() if notes is None else notes
    state = load(REPOS_JSON, {})
    by_repo = {}
    for n in notes:
        for r in n["repos"]:
            by_repo.setdefault(r.lower(), []).append(n)
    have, installs = global_skills(), project_installs()
    skills = []
    for repo, st in sorted(state.items(), key=lambda kv: kv[0].lower()):
        d = repo_dir(repo)
        if st.get("status") != "ok" or not d.exists():
            continue
        src = by_repo.get(repo.lower(), [])
        found = [str(md.parent.relative_to(d)) for md in d.rglob("*")
                 if md.name.lower() == "skill.md" and md.is_file() and ".git" not in md.relative_to(d).parts]
        for rel_dir in offered(found):
            root = d / rel_dir if rel_dir != "." else d
            md = next(p for p in root.iterdir() if p.name.lower() == "skill.md")
            rel = str(root.relative_to(d)) if root != d else ""
            fm = frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            name = fm.get("name") or (root.name if rel else repo.split("/")[1])
            files = skill_files(root)
            scripts = sorted(str(f.relative_to(root)) for f in files
                             if f.suffix.lower() in SCRIPT_EXT or os.access(f, os.X_OK))
            sid = f"{repo}/{rel}" if rel else repo
            skills.append({
                "id": sid, "name": name, "description": str(fm.get("description", ""))[:600],
                "repo": repo, "path": rel, "commit": st.get("commit", ""),
                "url": f"https://github.com/{repo}" + (f"/tree/{st.get('commit') or 'HEAD'}/{rel}" if rel else ""),
                "use_cases": sorted({n["topic"] for n in src if n["topic"]}),
                "tags": sorted({t for n in src for t in n["tags"]}),
                "projects": sorted({p for n in src for p in n["projects"]}),
                "reels": [{"title": n["title"], "source": n["source"], "file": n["file"]} for n in src],
                "files": len(files), "scripts": scripts[:20], "has_scripts": bool(scripts),
                "installed_globally": name in have, "installed_in": sorted(installs.get(sid, [])),
            })
    named = {r.lower() for n in notes for r in n["repos"]}
    no_repo = [{"title": n["title"], "source": n["source"], "file": n["file"]}
               for n in notes if n["topic"] == "Claude Code & skills" and not n["repos"]]
    catalog = {
        "generated_at": now(), "skills": skills,
        "repos": {"named": len(named), "with_skills": sum(1 for s in state.values() if s.get("status") == "ok"),
                  "no_skills": sum(1 for s in state.values() if s.get("status") == "no-skills"),
                  "failed": sorted(({"repo": r, "error": s.get("error", "")} for r, s in state.items()
                                    if s.get("status") == "failed"), key=lambda x: x["repo"].lower()),
                  "pending": sorted(r for r in {r for n in notes for r in n["repos"]}
                                    if r.lower() not in {k.lower() for k in state})},
        "no_repo_shown": no_repo,
    }
    save(CATALOG, catalog)
    print(f"catalog: {len(skills)} skills from {catalog['repos']['with_skills']} repos -> {CATALOG}")
    return catalog


# ------------------------------------------------------------------ use

def skills():
    cat = load(CATALOG, None)
    if cat is None:
        sys.exit(f"No catalog at {CATALOG}. Run: python3 {__file__} harvest")
    return cat["skills"]


def find(key):
    all_ = skills()
    hit = [s for s in all_ if s["id"] == key] or [s for s in all_ if s["name"].lower() == key.lower()]
    if not hit:
        sys.exit(f"No skill '{key}'. Try: python3 {__file__} search <words>")
    if len(hit) > 1:
        sys.exit(f"'{key}' matches {len(hit)} skills; pass the id:\n" + "\n".join("  " + s["id"] for s in hit))
    return hit[0]


def search(words):
    words = [w.lower() for w in words]
    scored = []
    for s in skills():
        fields = [(s["name"], 5), (s["description"], 2), (" ".join(s["use_cases"]), 2), (" ".join(s["tags"]), 2),
                  (" ".join(s["projects"]), 1), (" ".join(r["title"] for r in s["reels"]), 1), (s["repo"], 1)]
        score = sum(w2 for w in words for text, w2 in fields if w in text.lower())
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    if not scored:
        print("No match. Try broader words (design, testing, security, agent, docs, video).")
    for _, s in scored[:12]:
        flags = [f for f, on in (("ships scripts", s["has_scripts"]), ("already in every session", s["installed_globally"]))
                 if on]
        print(f"{s['name']}  [{s['id']}]" + (f"  ({', '.join(flags)})" if flags else ""))
        print(f"    {s['description'][:220]}")
        print(f"    use case: {', '.join(s['use_cases']) or '-'}")


# Words that say nothing about which skill fits: grammar, plus words nearly every request or skill uses.
STOP = set("""the and for with that this from have has had not but are was were will would can could should you your
our their them they she his her its into onto out about over under after before then than there here what when where
which who whom why how all any some more most much many few lot very just also too only even still yet now new one two
get got give make made let lets need want like use used using work works help please thank thanks does did done doing
being been see look show tell say said know think try tried run add put set take keep going able way thing things
something anything everything time today app apps project projects code file files claude skill skills session
sessions message every each per may might must shall yes okay sure find write end fix page
create build full whole complete current simple good better best great nice check verify trigger change update""".split())
SUFFIXES = ("ions", "ion", "ing", "ed", "es", "s", "e")


def stems(text):
    """Lower-case word roots worth matching on: 'animations', 'animate' and 'animated' all become 'animat'."""
    out = []
    for w in re.findall(r"[a-z][a-z0-9]{2,}", text.lower()):
        if w in STOP:
            continue
        for suf in SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[:-len(suf)]
                break
        if w not in STOP:
            out.append(w)
    return out


def match(text, limit=3):
    """Skills that fit a whole message, best first: [(skill, matched words)]. Rare words count for more than common
    ones, a word in the skill's name counts most, and a skill needs two matching words (or its own name)."""
    words = set(stems(text))
    cat = skills()
    if not words or not cat:
        return []
    # name and description only: tags and use cases come from the reel, so a whole repo shares them
    bags = [(s, set(stems(s["name"].replace("-", " "))), set(stems(s["description"]))) for s in cat]
    df = {}
    for _, name, desc in bags:
        for w in words & (name | desc):
            df[w] = df.get(w, 0) + 1
    common = max(5, len(cat) * 0.3)
    idf = {w: math.log(1 + len(cat) / n) for w, n in df.items() if n <= common}
    best = {}
    for s, name, desc in bags:
        hit = {w: 5 if w in name else 2 for w in idf if w in name or w in desc}
        if len(hit) < 2 and not (len(name) > 1 and name <= words):
            continue
        score = sum(idf[w] * weight for w, weight in hit.items()) * (1.8 if s["installed_globally"] else 1)  # already usable, so offer it first
        if s["path"].startswith(".") or "/." in s["path"]:
            score *= 0.4  # a repo's own maintenance skills (.agents/, .claude/), rarely what another project wants
        if score > best.get(s["name"], (0,))[0]:
            best[s["name"]] = (score, s, sorted(hit))
    ranked = sorted(best.values(), key=lambda x: (-x[0], x[1]["name"]))
    return [(s, hit) for score, s, hit in ranked[:limit] if score >= ranked[0][0] * 0.6]


def match_lines(found):
    """One line per match. Only the name, id and flags: descriptions are third-party text and stay out of a session."""
    lines = []
    for s, hit in found:
        name = re.sub(r"[^A-Za-z0-9_.-]", "", s["name"])[:60]
        if s["installed_globally"]:
            how = "installed in every session: use it with the Skill tool"
        else:
            how = "in the library: install with the skill-library skill" + (
                "; ships scripts, so name them and ask the owner first" if s["has_scripts"] else "")
        lines.append(f"- {name} [{s['id']}] ({how}). Matched: {', '.join(hit)}")
    return lines


def prompt_hook():
    """UserPromptSubmit hook: read the hook's JSON on stdin and hand the session the skills that fit the message.
    Says nothing when nothing fits, and never fails the prompt."""
    try:
        text = str(json.load(sys.stdin).get("prompt") or "")
        found = [] if text.startswith("/") else match(text)
        if found:
            note = ("Skill Library: skills that may fit this message, from a keyword match. Use one only if it really "
                    "fits the task; otherwise ignore this note.\n" + "\n".join(match_lines(found)))
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}}))
    except Exception:
        pass


def show(key):
    s = find(key)
    root = repo_dir(s["repo"]) / s["path"]
    print(json.dumps({k: s[k] for k in s if k != "description"}, indent=1, ensure_ascii=False))
    print(f"\nFolder: {root}\n\n----- SKILL.md -----")
    md = next((p for p in root.iterdir() if p.name.lower() == "skill.md"), None)
    print(md.read_text(encoding="utf-8", errors="replace") if md else "(missing)")


def project_root(p):
    r = git("rev-parse", "--show-toplevel", cwd=p)
    return Path(r.stdout.strip()) if r.returncode == 0 else Path(p)


def install(key, project=None, force=False):
    s = find(key)
    src = repo_dir(s["repo"]) / s["path"]
    proj = project_root(Path(project or os.getcwd()).expanduser().resolve())
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", s["name"]).strip(".-") or "skill"
    dest = proj / ".claude" / "skills" / name
    if dest.exists() and not force:
        sys.exit(f"{dest} already exists. Pass --force to replace it (this drops any edits made to it).")
    shutil.rmtree(dest, ignore_errors=True)
    if s["path"]:
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"), symlinks=False)
    else:  # whole-repo skill: copy only what the catalog counts, never .git
        dest.mkdir(parents=True)
        for f in src.iterdir():
            if f.name != ".git":
                (shutil.copytree if f.is_dir() else shutil.copy2)(f, dest / f.name)
    meta = {"id": s["id"], "repo": s["repo"], "path": s["path"], "commit": s["commit"], "installed": now(),
            "has_scripts": s["has_scripts"], "adapted": False}
    (dest / ".library.json").write_text(json.dumps(meta, indent=1))
    print(f"Installed {s['name']} -> {dest}")
    if s["has_scripts"]:
        print("Ships scripts (not run): " + ", ".join(s["scripts"]))
    if CATALOG.exists():
        build_catalog()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest"); h.add_argument("--refresh", action="store_true")
    sub.add_parser("catalog")
    s = sub.add_parser("search"); s.add_argument("words", nargs="+")
    m = sub.add_parser("match"); m.add_argument("words", nargs="*")
    sh = sub.add_parser("show"); sh.add_argument("key")
    i = sub.add_parser("install"); i.add_argument("key"); i.add_argument("--project"); i.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "harvest":
        harvest(a.refresh)
    elif a.cmd == "catalog":
        build_catalog()
    elif a.cmd == "search":
        search(a.words)
    elif a.cmd == "match":  # with words: print the matches; without: act as the UserPromptSubmit hook
        print("\n".join(match_lines(match(" ".join(a.words)))) or "No match.") if a.words else prompt_hook()
    elif a.cmd == "show":
        show(a.key)
    else:
        install(a.key, a.project, a.force)


if __name__ == "__main__":
    main()
