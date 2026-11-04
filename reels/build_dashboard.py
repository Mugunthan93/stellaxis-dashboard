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
    tools = re.findall(r"\*\*([^*]+)\*\*(?: \(`([\w.-]+/[\w.-]+)`)?", section(body, "Tools, repos & links"))
    worth = section(body, "Worth acting on?").split("\n\n")[0].replace("\n", " ")
    verdict = re.split(r"(?<=[.!?])\s", worth)[0] if worth else ""
    return {**fm, "code": code, "title": title, "hook": hook, "tldr": tldr, "body": rest, "tags": tags,
            "tools": tools, "verdict": verdict}


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
    last_date = last_run.split(":")[0]  # "2026-10-27"

    topics = TOPICS + sorted({n.get("topic", "Other") for n in notes} - set(TOPICS))
    groups = [(t, [n for n in notes if n.get("topic", "Other") == t]) for t in topics]
    groups = [(t, ns) for t, ns in groups if ns]
    by_code = {n["code"]: n for n in notes}

    def chips(code):
        fs = sorted((f for f in fits.get(code, []) if f["fit"] != "no"), key=lambda f: order[f["fit"]])
        return ('<div class="projs">' + "".join(
            f'<a class="pchip {f["fit"]}" href="#p-{slug(f["project"])}" title="{html.escape(f["raw"])}">{html.escape(f["project"])}</a>'
            for f in fs) + "</div>") if fs else ""

    def thumbs(codes, limit=6):
        """Reel covers instead of shortcodes: people recognise the picture, not De-REw8FdXv."""
        out = []
        for c in [c for c in codes if c in by_code][:limit]:
            t = html.escape(re.sub(r"`", "", by_code[c]["title"]), quote=True)
            out.append(f'<a class="thumb" href="#n-{c}" title="{t}"><img src="images/{c}.jpg" alt="{t}" loading="lazy"></a>'
                       if os.path.exists(os.path.join(R, f"images/{c}.jpg")) else f'<a class="thumb none" href="#n-{c}" title="{t}">{c[:4]}</a>')
        return '<div class="thumbs">' + "".join(out) + "</div>" if out else ""

    def tool_links(n, limit=5):
        items = "".join(
            f'<a class="tool" href="https://github.com/{repo}" target="_blank" rel="noopener">{html.escape(name)}</a>' if repo
            else f'<span class="tool">{html.escape(name)}</span>' for name, repo in n["tools"][:limit])
        more = len(n["tools"]) - limit
        return f'<div class="tools">{items}{f"<span class=more>+{more}</span>" if more > 0 else ""}</div>' if items else ""

    def card(n):
        cover = f'images/{n["code"]}.jpg'
        img = (f'<img src="{cover}" alt="" loading="lazy">' if os.path.exists(os.path.join(R, cover))
               else '<div class="noimg">no cover</div>')
        plain = re.sub(r"`", "", n["title"])
        source = html.escape(n.get("source", "#"))
        hay = " ".join([n["title"], n["tldr"], n.get("author", ""), " ".join(n["tags"]),
                        " ".join(t for t, _ in n["tools"])]).lower()
        projs = " ".join(slug(f["project"]) for f in fits.get(n["code"], []) if f["fit"] != "no")
        verdict = f'<p class="verdict"><b>Worth it?</b> {inline(n["verdict"])}</p>' if n["verdict"] else ""
        author = n.get("author", "")
        initial = html.escape(author.lstrip("@")[:1].upper() or "?")
        return f"""
<article class="card" id="n-{n['code']}" data-s="{html.escape(hay, quote=True)}" data-p="{projs}" data-t="{tid[n.get('topic', 'Other')]}">
  <p class="by"><span class="av" aria-hidden="true">{initial}</span><b>{html.escape(author)}</b><span>{n.get('posted', '?')}</span></p>
  <a class="cover" href="#n-{n['code']}" data-src="{source}" aria-label="{html.escape('Open note: ' + plain, quote=True)}">{img}</a>
  <div class="meta">
    <h3>{inline(n['title'])}</h3>
    <p class="tldr">{inline(n['tldr'])}</p>
    {tool_links(n)}
    {verdict}
    {chips(n['code'])}
    <p class="src"><a href="{source}" target="_blank" rel="noopener">Open on Instagram</a></p>
    <details class="full"><summary>Full note</summary><div class="note">{md(n['body'])}</div></details>
  </div>
</article>"""

    tid = {t: f"t-{i}" for i, (t, _) in enumerate(groups)}
    filters = "".join(f'<button type="button" class="chip" data-t="t-{i}" aria-pressed="false">{html.escape(t)} <b>{len(ns)}</b></button>'
                      for i, (t, ns) in enumerate(groups))
    body = "".join(card(n) for n in notes)  # newest first, the way a profile grid reads
    titles = {n["code"]: n["title"] for n in notes}
    proj_cards, proj_opts = [], []
    for pr in projects:
        rows = sorted(((code, f) for code, fl in fits.items() for f in fl if f["project"] == pr["name"] and code in titles),
                      key=lambda x: order[x[1]["fit"]])
        rows = [r for r in rows if r[1]["fit"] != "no"]
        if not rows:
            continue
        items = "".join(
            f'<li>{thumbs([code], 1)}<div><a href="#n-{code}">{inline(titles[code])}</a> <span class="fit {f["fit"]}">{f["fit"]}</span>'
            f'<p>{inline(f["why"])}</p></div></li>' for code, f in rows)
        strong = sum(1 for _, f in rows if f["fit"] == "strong")
        count = f'{strong} strong · {len(rows)} reels' if strong else f'{len(rows)} reels'
        what = f'<p class="what">{inline(pr["what"])}</p>' if pr["what"] else ""
        proj_cards.append(f'<details class="proj" id="p-{slug(pr["name"])}"><summary><b>{html.escape(pr["name"])}</b><span>{count}</span></summary>{what}<ul>{items}</ul></details>')
        proj_opts.append(f'<option value="{slug(pr["name"])}">{html.escape(pr["name"])}</option>')

    # "Do next": the map's ranked order, not the 33-item backlog.
    steps = re.findall(r"^\d+\. (.+)$", section(read("PROJECT-MAP.md"), "Suggested order"), re.M)
    do_next = "".join(f'<li><b>{inline(s.split(":", 1)[0])}</b>{f"<span>{inline(s.split(chr(58), 1)[1].strip())}</span>" if ":" in s else ""}</li>' for s in steps)
    next_up = f'<a class="nextup" href="#next"><span>Next up</span><b>{inline(steps[0].split(":", 1)[0])}</b></a>' if steps else ""

    # Trends are the story row: a circle per trend, opening a full-height viewer.
    stories, trend_cards = [], []
    for line in trends.splitlines():
        m = re.match(r"- \*\*(.+?)\*\*\s*(?:—\s*)?(.*?)\s*(\(\[.*)?$", line.strip())
        if m:
            codes = [c for c in re.findall(r"\[([\w-]+)\]\(", m.group(3) or "") if c in by_code]
            first = next((c for c in codes if os.path.exists(os.path.join(R, f"images/{c}.jpg"))), None)
            pic = f'<img src="images/{first}.jpg" alt="">' if first else ""
            stories.append(f'<button type="button" class="story" data-i="{len(stories)}"><span class="ring"><span class="pic">{pic}</span></span>'
                           f'<span class="lbl">{html.escape(re.sub(r"`", "", m.group(1)))}</span></button>')
            trend_cards.append(f'<article class="trend"><h3>{inline(m.group(1))}</h3><p>{inline(m.group(2))}</p>{thumbs(codes)}</article>')

    groups_a = {}
    for a in open_actions:
        m = re.match(r"\*\*(\w+\??)\*\*\s*(.*?)(?:\s+—\s+\[([\w-]+)\]\([^)]*\))?\s*$", a)
        verb, text, code = (m.group(1), m.group(2), m.group(3)) if m else ("Other", a, None)
        groups_a.setdefault(verb.rstrip("?"), []).append(
            f'<li>{thumbs([code], 1) if code else ""}<span>{inline(text)}</span></li>')
    act_groups = "".join(f'<div class="agroup"><h3>{html.escape(v)} <span>{len(ls)}</span></h3><ul>{"".join(ls)}</ul></div>'
                         for v, ls in sorted(groups_a.items(), key=lambda kv: -len(kv[1])))

    icon = lambda d: f'<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{d}</svg>'
    i_grid = icon('<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>')
    i_feed = icon('<rect x="4" y="3" width="16" height="11" rx="2"/><path d="M4 18h16M4 21h10"/>')
    i_next = icon('<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>')
    i_proj = icon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>')
    i_acts = icon('<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>')
    i_x = icon('<path d="M6 6l12 12M18 6 6 18"/>')

    head = """<title>AI Reel Knowledge</title>
<meta name="claude:viewport-fit-sides" content="cover">"""
    style = """<style>
/* Instagram's layout language (profile header, story row, 3-column 4:5 grid, post view, tab bar) on our own content.
   No logo and no name of theirs: it stays AI Reel Knowledge. Tokens are measured from instagram.com, 2026-11-04. */
:root {
  --bg:#fff; --bg2:#f2f4f5; --float:#fff; --ink:#0c1014; --mute:#65676b; --line:#dbdfe4; --link:#00376b; --blue:#4a5df9;
  --strong:#166534; --good:#1e40af; --possible:#854d0e;
  --body:-apple-system, "system-ui", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono:ui-monospace, Menlo, monospace; --rail:76px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#0c1014; --bg2:#25292e; --float:#212328; --ink:#f5f5f5; --mute:#a8a8a8; --line:#262626; --link:#e0f1ff;
  --strong:#86efac; --good:#a5c8ff; --possible:#fcd34d; color-scheme:dark } }
:root[data-theme="dark"] {
  --bg:#0c1014; --bg2:#25292e; --float:#212328; --ink:#f5f5f5; --mute:#a8a8a8; --line:#262626; --link:#e0f1ff;
  --strong:#86efac; --good:#a5c8ff; --possible:#fcd34d; color-scheme:dark }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 var(--body); padding-bottom:calc(64px + env(safe-area-inset-bottom, 0px)) }
main { max-width:935px; margin:0 auto; padding-left:env(safe-area-inset-left, 0px); padding-right:env(safe-area-inset-right, 0px) }
a { color:var(--link); text-decoration:none } a:hover { text-decoration:underline }
a:focus-visible, summary:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible { outline:2px solid var(--blue); outline-offset:2px }
summary, button { cursor:pointer }
h1, h2, h3 { line-height:1.25; margin:0 }
p { margin:0 }
[hidden] { display:none !important }

.tabs { position:fixed; z-index:20; left:0; right:0; bottom:0; display:flex; background:var(--bg); border-top:1px solid var(--line); padding-bottom:env(safe-area-inset-bottom, 0px) }
.tabs a { flex:1; min-height:56px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1px; color:var(--mute); font-size:11px; text-decoration:none; transition:color .15s }
.tabs a[aria-current="page"] { color:var(--ink); font-weight:600 }
.tabs a[aria-current="page"] svg { stroke-width:2.4 }

.profile { display:grid; grid-template-columns:auto minmax(0, 1fr); gap:6px 20px; align-items:center; padding:20px 16px 12px }
.avatar { grid-row:span 2; width:82px; height:82px; border-radius:50%; background:var(--blue); color:#fff; display:grid; place-items:center; font-size:30px; font-weight:700 }
.profile h1 { font-size:20px; font-weight:600 }
.stats { display:flex; gap:20px; list-style:none; margin:0; padding:0 }
.stats a { color:var(--ink); font-size:14px; display:block; text-decoration:none } .stats b { display:block; font-size:16px; font-weight:600 }
.bio { grid-column:1/-1; font-size:14px; color:var(--mute); margin-top:4px }
.storyrow { display:flex; gap:12px; overflow-x:auto; padding:8px 16px 12px; scrollbar-width:none }
.storyrow::-webkit-scrollbar, .chips::-webkit-scrollbar { display:none }
.story { flex:none; width:84px; padding:0; border:0; background:none; color:var(--ink); font:12px/1.25 var(--body); text-align:center }
.ring { display:block; width:74px; height:74px; margin:0 auto 6px; padding:3px; border-radius:50%; background:linear-gradient(45deg, #f9b33b, #ee2a7b 55%, #8a3ab9); transition:transform .15s }
.story:hover .ring { transform:scale(1.04) }
.pic { display:block; width:100%; height:100%; border-radius:50%; border:2px solid var(--bg); overflow:hidden; background:var(--bg2) }
.pic img { width:100%; height:100%; object-fit:cover; display:block }
.lbl { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden }
.nextup { display:flex; gap:10px; align-items:baseline; margin:0 16px 12px; padding:12px 14px; border-radius:12px; background:var(--bg2); color:var(--ink); text-decoration:none }
.nextup span { flex:none; color:var(--mute); font-size:13px } .nextup b { font-weight:600; min-width:0 }

.bar { position:sticky; top:0; z-index:5; background:var(--bg); padding:8px 16px; border-top:1px solid var(--line) }
.row { display:flex; gap:8px; align-items:center }
.row input, .row select { font:inherit; font-size:16px; color:var(--ink); background:var(--bg2); border:0; border-radius:8px; min-height:40px; padding:0 12px; min-width:0 }
.row input { flex:1 1 0 } .row select { flex:0 1 130px }
.mode { flex:none; width:40px; height:40px; display:grid; place-items:center; border:0; border-radius:8px; background:none; color:var(--mute) }
.mode[aria-pressed="true"] { color:var(--ink); background:var(--bg2) }
.chips { display:flex; gap:6px; margin-top:8px; overflow-x:auto; scrollbar-width:none }
.chip { font:inherit; font-size:13px; font-weight:600; flex:none; min-height:36px; padding:0 12px; border-radius:8px; border:0; background:var(--bg2); color:var(--ink); transition:background .15s }
.chip b { font-weight:400; color:var(--mute) } .chip[aria-pressed="true"] { background:var(--blue); color:#fff } .chip[aria-pressed="true"] b { color:inherit }
#count { padding:8px 16px 4px; color:var(--mute); font-size:13px }

.reels { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:2px }
.card { position:relative; min-width:0; background:var(--bg) }
.by { display:flex; gap:8px; align-items:center; padding:10px 12px; font-size:14px } .by b { font-weight:600; min-width:0; overflow-wrap:anywhere } .by span:last-child { color:var(--mute); font-size:12px; margin-left:auto; white-space:nowrap }
.av { flex:none; width:32px; height:32px; border-radius:50%; background:var(--bg2); display:grid; place-items:center; font-size:13px; font-weight:600 }
.cover { display:block } .cover img, .noimg { display:block; width:100%; aspect-ratio:4/5; object-fit:cover; background:var(--bg2) }
.noimg { display:grid; place-items:center; color:var(--mute); font-size:12px }
.meta { padding:10px 12px 16px; min-width:0 }
.meta h3 { font-size:15px; font-weight:600 } .tldr { margin:6px 0 10px }
.tools { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:10px }
.tool { font-size:13px; padding:3px 8px; border-radius:6px; background:var(--bg2); color:var(--ink); overflow-wrap:anywhere } a.tool { color:var(--link) } .more { font-size:13px; color:var(--mute); padding:3px }
.verdict { margin-bottom:10px; font-size:14px; color:var(--mute) } .verdict b { color:var(--ink); font-weight:600 }
.projs { display:flex; flex-wrap:wrap; gap:4px 10px; margin-bottom:10px }
.pchip, .fit { font-size:13px; font-weight:600 } .fit { font-size:12px }
.strong { color:var(--strong) } .good { color:var(--good) } .possible { color:var(--possible) } .no { color:var(--mute) }
.src { font-size:14px; font-weight:600; margin-bottom:4px } .src a { color:var(--link) }
details > summary { font-size:14px; color:var(--mute); min-height:32px; display:flex; align-items:center; list-style:none } details > summary::-webkit-details-marker { display:none }
.note { overflow-wrap:anywhere } .note h3 { font-size:15px; margin:14px 0 4px } .note img { display:none }
.note ul, .note ol { padding-left:18px } .note blockquote { margin:6px 0; padding-left:10px; border-left:3px solid var(--line); color:var(--mute) } .note p { margin:6px 0 }
/* Grid mode: the tile is the cover plus its title; the rest waits for the post view. */
.grid .by, .grid .tldr, .grid .tools, .grid .verdict, .grid .projs, .grid .src, .grid .full { display:none }
.grid .meta { position:absolute; left:0; right:0; bottom:0; padding:32px 8px 8px; color:#fff; pointer-events:none; background:linear-gradient(transparent, rgba(0,0,0,.82)) }
.grid .meta h3 { font-size:13px; line-height:1.25; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden }
.grid .meta code { background:none; padding:0; color:inherit; font:inherit }
.grid .cover img { transition:opacity .15s } .grid .cover:hover img { opacity:.85 }
.feed { grid-template-columns:minmax(0, 470px); justify-content:center; gap:12px }

.page { padding:20px 16px } .page > h2 { font-size:20px; font-weight:600 } .sub { color:var(--mute); font-size:14px; margin:2px 0 12px }
.next { list-style:none; counter-reset:n; margin:0; padding:0 }
.next li { counter-increment:n; position:relative; padding:12px 0 12px 54px; min-height:68px; border-bottom:1px solid var(--line) }
.next li::before { content:counter(n); position:absolute; left:0; top:14px; width:42px; height:42px; display:grid; place-items:center; border-radius:50%; background:var(--bg2); font-weight:600 }
.next b { display:block; font-weight:600 } .next span { display:block; color:var(--mute); font-size:14px }
.proj { border-bottom:1px solid var(--line); min-width:0 }
.proj > summary { justify-content:space-between; gap:12px; min-height:56px; color:var(--ink); font-size:15px } .proj > summary b { font-weight:600 }
.proj > summary span { color:var(--mute); font-size:13px; white-space:nowrap }
.proj .what { margin-bottom:10px; font-size:14px; color:var(--mute) }
.proj ul, .agroup ul { list-style:none; margin:0 0 14px; padding:0; display:grid; gap:12px }
.proj li, .agroup li { display:flex; gap:12px; align-items:flex-start; min-width:0 } .proj li > div, .agroup li > span { min-width:0; overflow-wrap:anywhere }
.proj li p { margin-top:2px; font-size:14px; color:var(--mute) }
.agroup { margin-top:16px } .agroup h3 { font-size:15px; font-weight:600; margin-bottom:10px } .agroup h3 span { color:var(--mute); font-weight:400 }
.thumbs { display:flex; gap:6px; flex-wrap:wrap }
.thumb { display:block; flex:none; width:44px; height:44px; border-radius:8px; overflow:hidden; background:var(--bg2) }
.thumb img { width:100%; height:100%; object-fit:cover; display:block } .thumb.none { font-size:10px; color:var(--mute); display:grid; place-items:center }
.trend { padding:10px 0 } .trend h3 { font-size:17px; font-weight:600 } .trend p { margin:4px 0 10px; color:var(--mute) }
code { background:var(--bg2); padding:1px 4px; border-radius:4px; font:13px var(--mono) }
pre { background:var(--bg2); padding:8px; border-radius:6px; overflow-x:auto }

dialog { border:0; padding:0; color:var(--ink); background:var(--float); max-width:none; max-height:none; overflow:hidden }
dialog::backdrop { background:rgba(0,0,0,.65) }
.x { position:absolute; top:8px; right:8px; z-index:3; width:44px; height:44px; display:grid; place-items:center; border:0; border-radius:50%; background:rgba(0,0,0,.45); color:#fff }
#post { width:100vw; height:100dvh; margin:0 }
#postbody { height:100%; overflow-y:auto } #postbody .card { background:none } #postbody .by { padding-right:60px } #postbody .cover img { max-height:62dvh; aspect-ratio:auto; height:62dvh }
#story { width:100vw; height:100dvh; margin:0; background:#000; color:#fff }
#story .bg { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; opacity:.45 }
#story .scrim { position:absolute; inset:0; background:linear-gradient(rgba(0,0,0,.55), rgba(0,0,0,.2) 35%, rgba(0,0,0,.85)) }
.seg { position:absolute; z-index:3; top:10px; left:10px; right:60px; display:flex; gap:3px } .seg i { flex:1; height:3px; border-radius:2px; background:rgba(255,255,255,.35) } .seg i.on { background:#fff }
.side { position:absolute; z-index:1; top:0; bottom:0; width:34%; border:0; background:none; color:transparent } .side.prev { left:0 } .side.nextb { right:0 }
#storybody { position:absolute; z-index:2; left:0; right:0; bottom:0; padding:24px 20px calc(28px + env(safe-area-inset-bottom, 0px)); pointer-events:none }
#storybody .trend h3 { font-size:24px; font-weight:700 } #storybody .trend p { color:#f5f5f5; font-size:16px } #storybody a { pointer-events:auto } #storybody code { background:rgba(255,255,255,.18) }
#storybody .thumb { width:64px; height:85px; background:#222 } .n { font-size:13px; color:#d8d8d8 }

@media (pointer:coarse) { .chip { min-height:40px } a.tool, a.pchip { min-height:32px; display:inline-flex; align-items:center } }
@media (min-width:768px) {
  body { padding-left:var(--rail); padding-bottom:0 }
  .tabs { top:0; right:auto; bottom:0; width:var(--rail); flex-direction:column; justify-content:flex-start; gap:6px; padding:16px 0; border-top:0; border-right:1px solid var(--line) }
  .tabs a { flex:none; min-height:60px }
  .profile { grid-template-columns:150px minmax(0, 1fr); padding:32px 16px 16px } .avatar { width:120px; height:120px; font-size:42px; justify-self:center }
  .profile h1 { font-size:24px } .stats { gap:32px } .stats a { font-size:15px } .stats b { display:inline; margin-right:4px } .bio { grid-column:2 }
  .ring { width:86px; height:86px } .story { width:100px } .storyrow { gap:16px }
  .reels.grid { gap:4px } .grid .meta { padding:48px 12px 12px } .grid .meta h3 { font-size:15px }
  .row select { flex-basis:190px }
  #post { width:min(980px, 92vw); height:min(760px, 90dvh); margin:auto; border-radius:6px; overflow:hidden }
  #postbody { overflow:hidden } #postbody .card { height:100%; display:grid; grid-template-columns:minmax(0, 11fr) minmax(0, 10fr); grid-template-rows:auto minmax(0, 1fr) }
  #postbody .cover { grid-column:1; grid-row:1/3; background:#000; min-height:0 } #postbody .cover img { height:100%; max-height:none }
  #postbody .by { grid-column:2; border-bottom:1px solid var(--line) } #postbody .meta { grid-column:2; overflow-y:auto }
  #story { width:min(460px, 92vw); height:min(818px, 92dvh); margin:auto; border-radius:10px }
}
@media (prefers-reduced-motion:reduce) { .ring, .tabs a, .chip, .grid .cover img { transition:none } }
</style>"""
    script = """<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var q = $('q'), proj = $('proj'), count = $('count'), list = $('list'), post = $('post'), story = $('story');
  var body = $('postbody'), sbody = $('storybody'), views = ['reels', 'next', 'projects', 'actions'];
  var all = function (s, r) { return [].slice.call((r || document).querySelectorAll(s)); };
  var cards = all('.card', list), chips = all('.chip'), trends = all('#trends .trend'), active = '', at = 0;
  $('trends').hidden = true;

  function apply() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), p = proj.value, shown = 0;
    cards.forEach(function (c) {
      var ok = words.every(function (w) { return c.dataset.s.indexOf(w) >= 0; })
        && (!p || c.dataset.p.split(' ').indexOf(p) >= 0) && (!active || c.dataset.t === active);
      c.hidden = !ok; if (ok) shown++;
    });
    count.textContent = shown === cards.length ? shown + ' reels' : shown + ' of ' + cards.length + ' reels';
  }
  chips.forEach(function (c) { c.addEventListener('click', function () {
    active = active === c.dataset.t ? '' : c.dataset.t;
    chips.forEach(function (x) { x.setAttribute('aria-pressed', String(x.dataset.t === active)); }); apply();
  }); });
  q.addEventListener('input', apply); proj.addEventListener('change', apply);

  function layout(m) {
    list.className = 'reels ' + m;
    all('.mode').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.m === m)); });
    try { localStorage.setItem('reel-layout', m); } catch (e) {}
  }
  all('.mode').forEach(function (b) { b.addEventListener('click', function () { layout(b.dataset.m); }); });
  var saved = 'grid'; try { saved = localStorage.getItem('reel-layout') || 'grid'; } catch (e) {}
  layout(saved);

  function show(v) {
    if (document.body.dataset.view !== v) scrollTo(0, 0);
    document.body.dataset.view = v;
    all('[data-v]').forEach(function (s) { s.hidden = s.dataset.v !== v; });
    all('.tabs a').forEach(function (a) { if (a.getAttribute('href') === '#' + v) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  }
  function clearHash() {
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { location.hash = ''; }
  }
  function openPost(t) {
    var c = t.cloneNode(true); c.removeAttribute('id'); c.hidden = false;
    var a = c.querySelector('.cover'); a.href = a.dataset.src; a.target = '_blank'; a.rel = 'noopener'; a.setAttribute('aria-label', 'Open on Instagram');
    var f = c.querySelector('.full'); if (f) f.open = false;
    body.textContent = ''; body.appendChild(c); body.scrollTop = 0;
    if (!post.open) post.showModal();
  }
  post.addEventListener('close', function () { if (location.hash.slice(0, 3) === '#n-') clearHash(); });
  function route() {
    var h = location.hash.slice(1), t = h && $(h);
    if (story.open) story.close();
    if (t && h.slice(0, 2) === 'n-') { if (!document.body.dataset.view) show('reels'); openPost(t); return; }
    if (post.open) post.close();
    if (t && h.slice(0, 2) === 'p-') { show('projects'); t.open = true; t.scrollIntoView(); return; }
    show(views.indexOf(h) >= 0 ? h : 'reels');
  }
  addEventListener('hashchange', route); route();

  function showStory(i) {
    at = (i + trends.length) % trends.length;
    var c = trends[at].cloneNode(true), img = all('.story img')[at];
    sbody.textContent = ''; sbody.appendChild(c);
    story.querySelector('.bg').src = img ? img.src : '';
    all('.seg i', story).forEach(function (s, n) { s.className = n <= at ? 'on' : ''; });
    if (!story.open) story.showModal();
  }
  story.querySelector('.seg').innerHTML = trends.map(function () { return '<i></i>'; }).join('');
  all('.story').forEach(function (b) { b.addEventListener('click', function () { showStory(+b.dataset.i); }); });
  story.querySelector('.prev').addEventListener('click', function () { showStory(at - 1); });
  story.querySelector('.nextb').addEventListener('click', function () { showStory(at + 1); });
  [post, story].forEach(function (d) {
    d.querySelector('.x').addEventListener('click', function () { d.close(); });
    d.addEventListener('click', function (e) { if (e.target === d) d.close(); });
  });
  addEventListener('keydown', function (e) {
    if (story.open && e.key === 'ArrowRight') showStory(at + 1);
    if (story.open && e.key === 'ArrowLeft') showStory(at - 1);
    if (e.key === '/' && !post.open && !story.open && document.activeElement !== q) { e.preventDefault(); location.hash = ''; show('reels'); q.focus(); }
  });
  apply();
})();
</script>"""
    tab = lambda v, i, label: f'<a href="#{v}">{i}{label}</a>'
    content = f"""<nav class="tabs" aria-label="Sections">{tab('reels', i_grid, 'Reels')}{tab('next', i_next, 'Next')}{tab('projects', i_proj, 'Projects')}{tab('actions', i_acts, 'Actions')}</nav>
<main>
<section data-v="reels" id="reels">
<header class="profile"><div class="avatar" aria-hidden="true">AI</div><h1>AI Reel Knowledge</h1>
<ul class="stats"><li><a href="#reels"><b>{len(notes)}</b> reels</a></li><li><a href="#actions"><b>{len(open_actions)}</b> to try</a></li><li><a href="#projects"><b>{len(proj_cards)}</b> projects</a></li></ul>
<p class="bio">What your saved reels add up to. Updated <span style="white-space:nowrap">{last_date}</span>.</p></header>
<div class="storyrow" aria-label="What's changing in AI">{''.join(stories)}</div>
{next_up}
<div class="bar"><div class="row"><input id="q" type="search" placeholder="Search" aria-label="Search tools, topics and authors">
<select id="proj" aria-label="Filter by project"><option value="">All projects</option>{''.join(proj_opts)}</select>
<button type="button" class="mode" data-m="grid" aria-label="Grid view" aria-pressed="true">{i_grid}</button><button type="button" class="mode" data-m="feed" aria-label="Feed view" aria-pressed="false">{i_feed}</button></div>
<div class="chips" role="group" aria-label="Filter by topic">{filters}</div></div>
<p id="count" role="status" aria-live="polite">{len(notes)} reels</p>
<div class="reels grid" id="list">{body}</div></section>
<section data-v="next" id="next" class="page"><h2>Do next</h2><p class="sub">The highest-value steps, in order. {done_actions} of {len(open_actions) + done_actions} actions done.</p><ol class="next">{do_next}</ol></section>
<section id="trends" class="page"><h2>What's changing in AI</h2>{''.join(trend_cards) or '<p>No trends yet.</p>'}</section>
<section data-v="projects" id="projects" class="page"><h2>By project</h2><p class="sub">Which reels help which project.</p>{''.join(proj_cards)}</section>
<section data-v="actions" id="actions" class="page"><h2>All actions</h2><p class="sub">Everything worth trying, grouped by what it takes.</p>{act_groups or '<p>Nothing open.</p>'}</section>
</main>
<dialog id="post" aria-label="Reel note"><button type="button" class="x" aria-label="Close">{i_x}</button><div id="postbody"></div></dialog>
<dialog id="story" aria-label="What's changing in AI"><img class="bg" alt=""><div class="scrim"></div><div class="seg"></div><button type="button" class="x" aria-label="Close">{i_x}</button>
<button type="button" class="side prev" aria-label="Previous trend">Previous</button><button type="button" class="side nextb" aria-label="Next trend">Next</button><div id="storybody"></div></dialog>
{script}
"""
    # Local copy is a full document; the artifact copy is a fragment (the publisher adds the skeleton).
    local = f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n{head}\n{style}</head><body>\n{content}</body></html>\n'
    open(os.path.join(R, "dashboard.html"), "w", encoding="utf-8").write(local)
    os.makedirs(os.path.join(R, ".tools", "artifact"), exist_ok=True)
    # Artifact copy embeds the covers as data: URIs so it is one self-contained file.
    # A cover can appear several times, so only its first <img> carries the bytes; a small script fills the rest.
    seen = set()
    def embed(m):
        if m.group(1) in seen:
            return f'data-from="{m.group(1)}"'
        seen.add(m.group(1))
        return f'data-cover="{m.group(1)}" src="data:image/jpeg;base64,' + base64.b64encode(open(os.path.join(R, m.group(1)), "rb").read()).decode() + '"'
    art = re.sub(r'src="(images/[\w-]+\.jpg)"', embed, content)
    art += """<script>document.querySelectorAll('img[data-from]').forEach(function (i) { i.src = document.querySelector('img[data-cover="' + i.dataset.from + '"]').src; });</script>\n"""
    open(os.path.join(R, ".tools", "artifact", "index.html"), "w", encoding="utf-8").write(f"{head}\n{style}\n{art}")
    print(f"dashboard.html + .tools/artifact/index.html: {len(notes)} notes, {len(open_actions)} open actions")


if __name__ == "__main__":
    main()
