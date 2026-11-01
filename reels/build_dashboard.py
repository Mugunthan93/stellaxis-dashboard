"""Build ~/Knowledge/reels/dashboard.html from the notes, INDEX.md, ACTIONS.md and RUNLOG.md.

Usage: python3 build_dashboard.py   (stdlib only; read-only on the notes)
Open with: open ~/Knowledge/reels/dashboard.html
"""
import base64, glob, html, os, re

R = os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels"))  # the notes; code lives in the repo
TOPICS = ["Claude Code & skills", "AI coding tools", "AI agents & LLM apps", "Models & research",
          "AI security", "AI for design & UI", "AI industry & trends"]


def read(name):
    p = os.path.join(R, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def inline(s):
    """Escape text but keep the few inline HTML tags the notes use, then apply md inline syntax."""
    keep = []
    s = re.sub(r"</?(a|img|details|summary)\b[^>]*>", lambda m: keep.append(m.group(0)) or f"\x00{len(keep)-1}\x00", s)
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])_([^_]+)_(?!\w)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{note_href(m.group(2))}">{m.group(1)}</a>', s)
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], s)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def note_href(href):
    m = re.match(r"\d{4}-\d{2}-\d{2}-([\w-]+)\.md$", href)
    return f"#n-{m.group(1)}" if m else href


def md(text):
    """Small markdown renderer: headings, lists, quotes, code fences, paragraphs, raw <details>."""
    out, para, lst = [], [], None

    def flush():
        nonlocal lst
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>"); para.clear()
        if lst:
            out.append(f"</{lst}>"); lst = None

    lines, i = text.splitlines(), 0
    while i < len(lines):
        l = lines[i]; s = l.strip()
        if s.startswith("```"):
            flush(); j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>"); i = j + 1; continue
        if not s:
            flush()
        elif re.match(r"</?(details|summary)", s):
            flush(); out.append(inline(s))
        elif m := re.match(r"(#{1,4}) (.*)", s):
            flush(); n = len(m.group(1)) + 1; out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif s.startswith("> "):
            flush(); out.append(f"<blockquote>{inline(s[2:])}</blockquote>")
        elif m := re.match(r"(?:[-*]|(\d+)\.) (.*)", s):
            kind = "ol" if m.group(1) else "ul"
            if para:
                out.append(f"<p>{inline(' '.join(para))}</p>"); para.clear()
            if lst != kind:
                if lst:
                    out.append(f"</{lst}>")
                out.append(f"<{kind}>"); lst = kind
            out.append(f"<li>{inline(m.group(2))}</li>")
        else:
            if lst:
                out.append(f"</{lst}>"); lst = None
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)


def parse_note(path):
    raw = open(path, encoding="utf-8").read()
    fm, body = {}, raw
    if raw.startswith("---"):
        head, body = raw[3:].split("\n---\n", 1)
        for l in head.strip().splitlines():
            if ":" in l:
                k, v = l.split(":", 1); fm[k.strip()] = v.strip().strip('"')
    code = re.match(r"\d{4}-\d{2}-\d{2}-([\w-]+)\.md$", os.path.basename(path)).group(1)
    title = (re.search(r"^# (.+)$", body, re.M) or [None, code])[1]
    hook = (re.search(r"^> \*\*Reel caption:\*\* (.+)$", body, re.M) or [None, ""])[1]
    tldr = (re.search(r"^\*\*TL;DR:\*\* (.+)$", body, re.M) or [None, ""])[1]
    # Detail view: everything after the TL;DR line (title, cover and hook are shown on the card).
    rest = body.split(tldr, 1)[1] if tldr else body
    tags = [t.strip() for t in fm.get("tags", "").strip("[]").split(",") if t.strip()]
    return {**fm, "code": code, "title": title, "hook": hook, "tldr": tldr, "body": rest, "tags": tags}


FIT = [("strong", ("strong", "do now")), ("good", ("good", "moderate", "relevant")),
       ("possible", ("possible", "light", "idea", "maybe")), ("no", ("no",))]


def fit_level(raw):
    r = raw.lower()
    return next((lvl for lvl, words in FIT if any(r.startswith(w) or f" {w}" in r for w in words)), "possible")


def parse_map(text):
    """PROJECT-MAP.md -> (projects [{name, where, what}], {code: [{project, fit, raw, why}]})."""
    projects = []
    for row in re.findall(r"^\|(.+)\|$", text, re.M):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) >= 3 and cells[0] not in ("Project",) and not set(cells[0]) <= set("-: "):
            projects.append({"name": cells[0], "where": cells[1], "what": cells[2]})
    by_code = {}
    for block in re.split(r"^### ", section(text, "By reel"), flags=re.M):
        m = re.search(r"\(\d{4}-\d{2}-\d{2}-([\w-]+)\.md\)", block)
        if not m:
            continue
        for name, raw, why in re.findall(r"^- \*\*(.+?): (.+?)\.\*\* *(.*)$", block, re.M):
            if name.lower() in ("caveat", "note"):
                continue
            key = name.lower()
            match = next((pr["name"] for pr in projects
                          if pr["name"].lower().startswith(key) or key.startswith(pr["name"].lower())), name)
            if match == name and name not in [pr["name"] for pr in projects]:
                projects.append({"name": name, "where": "", "what": ""})
            by_code.setdefault(m.group(1), []).append({"project": match, "fit": fit_level(raw), "raw": raw, "why": why})
    return projects, by_code


def section(text, heading):
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def main():
    notes = sorted((parse_note(p) for p in glob.glob(os.path.join(R, "????-??-??-*.md"))),
                   key=lambda n: (n.get("processed", ""), n.get("posted", "")), reverse=True)
    index, actions, runlog = read("INDEX.md"), read("ACTIONS.md"), read("RUNLOG.md")
    trends = section(index, "Current trends")
    projects, fits = parse_map(read("PROJECT-MAP.md"))
    order = {"strong": 0, "good": 1, "possible": 2, "no": 3}
    open_actions = [l[6:] for l in actions.splitlines() if l.startswith("- [ ] ")]
    done_actions = sum(1 for l in actions.splitlines() if l.lower().startswith("- [x] "))
    last_run = ([l[2:] for l in runlog.splitlines() if l.startswith("- ")] or ["never"])[-1]
    last_run = re.sub(r" \([^)]*\)", "", last_run.split(";")[0])  # "2026-10-27: 6 new notes"

    topics = TOPICS + sorted({n.get("topic", "Other") for n in notes} - set(TOPICS))
    groups = [(t, [n for n in notes if n.get("topic", "Other") == t]) for t in topics]
    groups = [(t, ns) for t, ns in groups if ns]

    def chips(code):
        fs = sorted((f for f in fits.get(code, []) if f["fit"] != "no"), key=lambda f: order[f["fit"]])
        return ('<div class="projs">' + "".join(
            f'<a class="pchip {f["fit"]}" href="#p-{slug(f["project"])}" title="{html.escape(f["raw"])}">{html.escape(f["project"])}</a>'
            for f in fs) + "</div>") if fs else ""

    def card(n):
        cover = f'images/{n["code"]}.jpg'
        img = (f'<img src="{cover}" alt="" loading="lazy">' if os.path.exists(os.path.join(R, cover))
               else '<div class="noimg">no cover</div>')
        tags = "".join(f"<span>{html.escape(t)}</span>" for t in n["tags"])
        return f"""
<article class="card" id="n-{n['code']}">
  <a class="cover" href="{html.escape(n.get('source', '#'))}" target="_blank" rel="noopener" title="Open the reel on Instagram">{img}</a>
  <div class="meta">
    <h3>{inline(n['title'])}</h3>
    {f'<p class="hook">“{html.escape(n["hook"])}”</p>' if n['hook'] else ''}
    <p class="tldr">{inline(n['tldr'])}</p>
    <p class="by">{html.escape(n.get('author', ''))} · posted {n.get('posted', '?')} · added {n.get('processed', '?')}</p>
    <div class="tags">{tags}</div>
    {chips(n['code'])}
  </div>
  <details class="full"><summary>Full note</summary><div class="note">{md(n['body'])}</div></details>
</article>"""

    nav = "".join(f'<a href="#t-{i}">{html.escape(t)} <b>{len(ns)}</b></a>' for i, (t, ns) in enumerate(groups))
    body = "".join(f'<section id="t-{i}"><h2>{html.escape(t)}</h2><div class="grid">{"".join(card(n) for n in ns)}</div></section>'
                   for i, (t, ns) in enumerate(groups))
    titles = {n["code"]: n["title"] for n in notes}
    proj_cards = []
    for pr in projects:
        rows = sorted(((code, f) for code, fl in fits.items() for f in fl if f["project"] == pr["name"] and code in titles),
                      key=lambda x: order[x[1]["fit"]])
        if not rows:
            continue
        items = "".join(
            f'<li><span class="fit {f["fit"]}">{f["fit"]}</span> <a href="#n-{code}">{inline(titles[code])}</a>'
            f'<p>{inline(f["why"])}</p></li>' for code, f in rows)
        where = f'<p class="where">{inline(pr["where"])}</p>' if pr["where"] else ""
        what = f'<p class="what">{inline(pr["what"])}</p>' if pr["what"] else ""
        proj_cards.append(f'<div class="proj" id="p-{slug(pr["name"])}"><h3>{html.escape(pr["name"])}</h3>{what}{where}<ul>{items}</ul></div>')
    by_project = (f'<section id="projects"><h2>By project</h2><div class="pgrid">{"".join(proj_cards)}</div></section>'
                  if proj_cards else "")
    nav = '<a href="#projects">By project</a>' + nav if proj_cards else nav
    acts = "".join(f"<li>{inline(a)}</li>" for a in open_actions) or "<li>Nothing open.</li>"

    head = """<title>AI Reel Knowledge</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">"""
    style = """<style>
/* Layout: summary strip, trends + to-try side by side, then 9:16 reel-cover cards grouped by topic. */
:root {
  --bg:#f3f4f8; --panel:#ffffff; --ink:#1b1d29; --mute:#5d6175; --line:#dfe1ea; --accent:#4553c9; --chip:#e9ebf8;
  --strong:#1d7a46; --strong-bg:#e2f4ea; --good:#2f5fb3; --good-bg:#e4ecfa; --possible:#8a5a00; --possible-bg:#fbf0d9;
  --display:"Bricolage Grotesque", "Avenir Next", system-ui, sans-serif;
  --body:"Source Sans 3", -apple-system, "Segoe UI", sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#12131a; --panel:#1b1d27; --ink:#e8e9f1; --mute:#9a9eb3; --line:#2b2e3d; --accent:#94a0ff; --chip:#262a40; --strong:#6fd59a; --strong-bg:#173426; --good:#93b4ff; --good-bg:#1d2a47; --possible:#f1c46b; --possible-bg:#3a2e14; color-scheme:dark } }
:root[data-theme="dark"] {
  --bg:#12131a; --panel:#1b1d27; --ink:#e8e9f1; --mute:#9a9eb3; --line:#2b2e3d; --accent:#94a0ff; --chip:#262a40; --strong:#6fd59a; --strong-bg:#173426; --good:#93b4ff; --good-bg:#1d2a47; --possible:#f1c46b; --possible-bg:#3a2e14; color-scheme:dark }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 var(--body) }
main { max-width:1180px; margin:0 auto; padding-block:24px 64px;
  padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)) }
a { color:var(--accent) } a:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
h1, h2, h3 { font-family:var(--display); text-wrap:balance; letter-spacing:-0.01em }
header h1 { margin:0; font-size:30px } header p { margin:4px 0 0; color:var(--mute) }
.stats { display:flex; gap:10px; flex-wrap:wrap; margin:18px 0 }
.stat { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:110px; flex:1 1 110px }
.stat b { display:block; font:600 24px var(--display); font-variant-numeric:tabular-nums }
.stat span { color:var(--mute); font-size:12px; text-transform:uppercase; letter-spacing:.06em }
.top { display:grid; grid-template-columns:3fr 2fr; gap:16px }
@media (max-width:820px) { .top { grid-template-columns:1fr } }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; min-width:0 }
.panel h2 { margin:0 0 8px; font-size:18px } .panel ul { margin:0; padding-left:18px } .panel li { margin:7px 0 }
.panel em { color:var(--mute); font-size:13px }
nav { display:flex; gap:8px; flex-wrap:wrap; margin:22px 0 0 }
nav a { text-decoration:none; background:var(--chip); padding:4px 11px; border-radius:999px; font-size:14px }
nav b { font-family:var(--mono); font-weight:500; font-size:12px; margin-left:2px }
section h2 { font-size:21px; margin:28px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line) }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 360px), 1fr)); gap:16px }
.card { display:grid; grid-template-columns:112px minmax(0, 1fr); gap:14px; align-content:start;
  background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px }
.card:target { outline:2px solid var(--accent) }
.cover img, .noimg { width:112px; max-width:100%; aspect-ratio:9/16; object-fit:cover; border-radius:8px; display:block; background:var(--line) }
.noimg { display:grid; place-items:center; color:var(--mute); font-size:12px }
.meta { min-width:0 } .meta h3 { margin:0 0 6px; font-size:16px; line-height:1.3 }
.hook { margin:0 0 6px; color:var(--mute); font-style:italic; font-size:14px }
.tldr { margin:0 0 6px; font-size:15px } .by { margin:0 0 6px; color:var(--mute); font-size:13px }
.tags span { display:inline-block; background:var(--chip); border-radius:6px; padding:1px 7px; margin:0 4px 4px 0; font:12px var(--mono) }
.card > details.full { grid-column:1/-1; min-width:0 }
details > summary { cursor:pointer; color:var(--accent); font-size:14px }
.note { font-size:15px; overflow-wrap:anywhere } .note h3 { font-size:15px; margin:14px 0 4px }
.note ul, .note ol { padding-left:18px } .note blockquote { margin:6px 0; padding-left:10px; border-left:3px solid var(--line); color:var(--mute) }
.projs { display:flex; flex-wrap:wrap; gap:4px; margin-top:2px }
.pchip, .fit { display:inline-block; border-radius:999px; padding:1px 8px; font-size:12px; text-decoration:none; white-space:nowrap }
.strong { color:var(--strong); background:var(--strong-bg) } .good { color:var(--good); background:var(--good-bg) }
.possible { color:var(--possible); background:var(--possible-bg) } .no { color:var(--mute); background:var(--chip) }
.fit { font:500 11px var(--mono); text-transform:uppercase; letter-spacing:.04em; padding:1px 7px }
.pgrid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 340px), 1fr)); gap:16px }
.proj { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; min-width:0 }
.proj:target { outline:2px solid var(--accent) }
.proj h3 { margin:0 0 2px; font-size:17px } .proj .what { margin:0; font-size:14px }
.proj .where { margin:2px 0 8px; color:var(--mute); font-size:13px; overflow-wrap:anywhere }
.proj ul { list-style:none; margin:0; padding:0; display:grid; gap:10px }
.proj li p { margin:3px 0 0; font-size:14px; color:var(--mute) }
code { background:var(--chip); padding:1px 4px; border-radius:4px; font:13px var(--mono) }
pre { background:var(--chip); padding:8px; border-radius:6px; overflow-x:auto }
</style>"""
    content = f"""<main>
<header><h1>AI Reel Knowledge</h1>
<p>AI ideas, tools and trends from your saved Instagram reels. Last run: {inline(last_run)}</p></header>
<div class="stats">
  <div class="stat"><b>{len(notes)}</b><span>AI notes</span></div>
  <div class="stat"><b>{len(groups)}</b><span>topics</span></div>
  <div class="stat"><b>{len(open_actions)}</b><span>open actions</span></div>
  <div class="stat"><b>{done_actions}</b><span>actions done</span></div>
</div>
<div class="top">
  <div class="panel"><h2>Current trends</h2>{md(trends) or '<p>No trends yet.</p>'}</div>
  <div class="panel"><h2>To try</h2><ul>{acts}</ul></div>
</div>
<nav>{nav}</nav>
{by_project}
{body}
</main>
"""
    # Local copy is a full document; the artifact copy is a fragment (the publisher adds the skeleton).
    local = f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n{head}\n{style}</head><body>\n{content}</body></html>\n'
    open(os.path.join(R, "dashboard.html"), "w", encoding="utf-8").write(local)
    os.makedirs(os.path.join(R, ".tools", "artifact"), exist_ok=True)
    # Artifact copy embeds the covers as data: URIs so it is one self-contained file.
    embed = lambda m: 'src="data:image/jpeg;base64,' + base64.b64encode(open(os.path.join(R, m.group(1)), "rb").read()).decode() + '"'
    art = re.sub(r'src="(images/[\w-]+\.jpg)"', embed, content)
    open(os.path.join(R, ".tools", "artifact", "index.html"), "w", encoding="utf-8").write(f"{head}\n{style}\n{art}")
    print(f"dashboard.html + .tools/artifact/index.html: {len(notes)} notes, {len(open_actions)} open actions")


if __name__ == "__main__":
    main()
