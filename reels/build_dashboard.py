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
        label = html.escape("Open on Instagram: " + re.sub(r"`", "", n["title"]), quote=True)
        hay = " ".join([n["title"], n["tldr"], n.get("author", ""), " ".join(n["tags"]),
                        " ".join(t for t, _ in n["tools"])]).lower()
        projs = " ".join(slug(f["project"]) for f in fits.get(n["code"], []) if f["fit"] != "no")
        verdict = f'<p class="verdict"><b>Worth it?</b> {inline(n["verdict"])}</p>' if n["verdict"] else ""
        return f"""
<article class="card" id="n-{n['code']}" data-s="{html.escape(hay, quote=True)}" data-p="{projs}">
  <a class="cover" href="{html.escape(n.get('source', '#'))}" target="_blank" rel="noopener" aria-label="{label}">{img}</a>
  <div class="meta">
    <h3>{inline(n['title'])}</h3>
    <p class="tldr">{inline(n['tldr'])}</p>
    {tool_links(n)}
    {verdict}
    {chips(n['code'])}
    <p class="by">{html.escape(n.get('author', ''))} · {n.get('posted', '?')}</p>
  </div>
  <details class="full"><summary>Full note</summary><div class="note">{md(n['body'])}</div></details>
</article>"""

    filters = "".join(f'<button type="button" class="chip" data-t="t-{i}" aria-pressed="false">{html.escape(t)} <b>{len(ns)}</b></button>'
                      for i, (t, ns) in enumerate(groups))
    body = "".join(f'<section class="topic" id="t-{i}"><h3 class="th">{html.escape(t)} <span>{len(ns)}</span></h3><div class="grid">{"".join(card(n) for n in ns)}</div></section>'
                   for i, (t, ns) in enumerate(groups))
    titles = {n["code"]: n["title"] for n in notes}
    proj_cards, proj_opts = [], []
    for pr in projects:
        rows = sorted(((code, f) for code, fl in fits.items() for f in fl if f["project"] == pr["name"] and code in titles),
                      key=lambda x: order[x[1]["fit"]])
        rows = [r for r in rows if r[1]["fit"] != "no"]
        if not rows:
            continue
        items = "".join(
            f'<li><span class="fit {f["fit"]}">{f["fit"]}</span> <a href="#n-{code}">{inline(titles[code])}</a>'
            f'<p>{inline(f["why"])}</p></li>' for code, f in rows)
        strong = sum(1 for _, f in rows if f["fit"] == "strong")
        count = f'{strong} strong · {len(rows)} reels' if strong else f'{len(rows)} reels'
        what = f'<p class="what">{inline(pr["what"])}</p>' if pr["what"] else ""
        proj_cards.append(f'<details class="proj" id="p-{slug(pr["name"])}"><summary><b>{html.escape(pr["name"])}</b><span>{count}</span></summary>{what}<ul>{items}</ul></details>')
        proj_opts.append(f'<option value="{slug(pr["name"])}">{html.escape(pr["name"])}</option>')

    # "Do next": the map's ranked order, not the 33-item backlog.
    steps = re.findall(r"^\d+\. (.+)$", section(read("PROJECT-MAP.md"), "Suggested order"), re.M)
    do_next = "".join(f'<li><b>{inline(s.split(":", 1)[0])}</b>{f"<span>{inline(s.split(chr(58), 1)[1].strip())}</span>" if ":" in s else ""}</li>' for s in steps)

    trend_cards = []
    for line in trends.splitlines():
        m = re.match(r"- \*\*(.+?)\*\*\s*(?:—\s*)?(.*?)\s*(\(\[.*)?$", line.strip())
        if m:
            codes = re.findall(r"\[([\w-]+)\]\(", m.group(3) or "")
            trend_cards.append(f'<article class="trend"><h3>{inline(m.group(1))}</h3><p>{inline(m.group(2))}</p>{thumbs(codes)}</article>')

    groups_a = {}
    for a in open_actions:
        m = re.match(r"\*\*(\w+\??)\*\*\s*(.*?)(?:\s+—\s+\[([\w-]+)\]\([^)]*\))?\s*$", a)
        verb, text, code = (m.group(1), m.group(2), m.group(3)) if m else ("Other", a, None)
        groups_a.setdefault(verb.rstrip("?"), []).append(
            f'<li>{thumbs([code], 1) if code else ""}<span>{inline(text)}</span></li>')
    act_groups = "".join(f'<div class="agroup"><h3>{html.escape(v)} <span>{len(ls)}</span></h3><ul>{"".join(ls)}</ul></div>'
                         for v, ls in sorted(groups_a.items(), key=lambda kv: -len(kv[1])))

    head = """<title>AI Reel Knowledge</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&family=IBM+Plex+Mono:wght@400;500&display=swap">"""
    style = """<style>
/* Order of the page answers: what do I do next, what is changing, find a reel, what fits each project. */
:root {
  --bg:#f8fafc; --panel:#ffffff; --ink:#1e293b; --mute:#475569; --line:#e2e8f0; --accent:#2563eb; --chip:#eaeff3; --on-accent:#fff;
  --strong:#166534; --strong-bg:#dcfce7; --good:#1e40af; --good-bg:#dbeafe; --possible:#854d0e; --possible-bg:#fef3c7;
  --body:"Atkinson Hyperlegible", -apple-system, "Segoe UI", sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#0f172a; --panel:#1e293b; --ink:#e2e8f0; --mute:#a3b1c6; --line:#334155; --accent:#7cb0ff; --chip:#2b3a50; --on-accent:#0f172a;
  --strong:#86efac; --strong-bg:#14351f; --good:#a5c8ff; --good-bg:#1c2f52; --possible:#fcd34d; --possible-bg:#3b2d0c; color-scheme:dark } }
:root[data-theme="dark"] {
  --bg:#0f172a; --panel:#1e293b; --ink:#e2e8f0; --mute:#a3b1c6; --line:#334155; --accent:#7cb0ff; --chip:#2b3a50; --on-accent:#0f172a;
  --strong:#86efac; --strong-bg:#14351f; --good:#a5c8ff; --good-bg:#1c2f52; --possible:#fcd34d; --possible-bg:#3b2d0c; color-scheme:dark }
* { box-sizing:border-box }
html { scroll-behavior:smooth; scroll-padding-top:128px }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 var(--body) }
main { max-width:1180px; margin:0 auto; padding-block:28px 72px;
  padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)) }
a { color:var(--accent); text-underline-offset:2px }
a:focus-visible, summary:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
summary, button { cursor:pointer }
h1, h2, h3 { text-wrap:balance; line-height:1.25; margin:0 }
header h1 { font-size:32px } header p { margin:4px 0 0; color:var(--mute) }
.jump { display:flex; gap:16px; flex-wrap:wrap; margin-top:10px; font-size:14px }
section.block { margin-top:36px } section.block > h2 { font-size:24px; margin-bottom:4px }
.sub { margin:0 0 14px; color:var(--mute); font-size:14px }
.next { list-style:none; counter-reset:n; margin:0; padding:0; display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(min(100%, 280px), 1fr)) }
.next li { counter-increment:n; position:relative; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px 14px 56px; min-width:0 }
.next li::before { content:counter(n); position:absolute; left:14px; top:14px; width:30px; height:30px; display:grid; place-items:center;
  border-radius:50%; background:var(--accent); color:var(--on-accent); font-weight:700; font-size:14px }
.next b { display:block; line-height:1.3 } .next span { display:block; color:var(--mute); font-size:14px; margin-top:2px }
.trends { display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(min(100%, 340px), 1fr)) }
.trend { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; min-width:0 }
.trend h3 { font-size:17px } .trend p { margin:4px 0 10px; color:var(--mute); font-size:14px }
.thumbs { display:flex; gap:6px; flex-wrap:wrap }
.thumb { display:block; flex:none; width:36px; height:64px; border-radius:6px; overflow:hidden; background:var(--line); transition:transform .18s }
.thumb:hover { transform:translateY(-2px) } .thumb img { width:100%; height:100%; object-fit:cover; display:block }
.thumb.none { font:10px var(--mono); color:var(--mute); display:grid; place-items:center; text-decoration:none }
.bar { position:sticky; top:0; z-index:5; background:var(--bg); padding:12px 0 10px; border-bottom:1px solid var(--line); margin-bottom:6px }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:center }
.row input, .row select { font:inherit; color:var(--ink); background:var(--panel); border:1px solid var(--line); border-radius:8px; min-height:44px; padding:0 12px }
.row input { flex:1 1 240px; min-width:0 } .row select { flex:0 1 220px; min-width:0 }
#count { color:var(--mute); font-size:14px; white-space:nowrap }
.chips { display:flex; gap:6px; margin-top:8px; overflow-x:auto; scrollbar-width:none; padding-bottom:2px }
.chip { font:inherit; font-size:14px; flex:none; min-height:36px; padding:0 12px; border-radius:999px; border:1px solid var(--line); background:var(--panel); color:var(--ink); transition:background .18s }
.chip b { font:500 12px var(--mono); color:var(--mute) } .chip[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:var(--on-accent) }
.chip[aria-pressed="true"] b { color:inherit }
@media (pointer:coarse) {
  .chip, .jump a, details.full > summary { min-height:44px } .jump a, details.full > summary { display:flex; align-items:center }
  a.tool, a.pchip { min-height:32px; display:inline-flex; align-items:center }
}
.th { font-size:18px; margin:24px 0 12px } .th span { color:var(--mute); font:500 13px var(--mono) }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap:14px }
.card { display:grid; grid-template-columns:104px minmax(0, 1fr); gap:14px; align-content:start; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px }
.card:target { outline:2px solid var(--accent) }
.cover img, .noimg { width:104px; aspect-ratio:9/16; object-fit:cover; border-radius:8px; display:block; background:var(--line) }
.noimg { display:grid; place-items:center; color:var(--mute); font-size:12px }
.meta { min-width:0 } .meta h3 { font-size:16px; line-height:1.3 }
.tldr { margin:6px 0 8px; font-size:15px; color:var(--ink) }
.tools { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:8px }
.tool { font:12px var(--mono); padding:2px 7px; border-radius:6px; background:var(--chip); color:var(--ink); text-decoration:none; overflow-wrap:anywhere }
a.tool { color:var(--accent) } a.tool:hover { text-decoration:underline } .more { font:12px var(--mono); color:var(--mute); padding:2px }
.verdict { margin:0 0 8px; font-size:14px; color:var(--mute) } .verdict b { color:var(--ink) }
.by { margin:6px 0 0; color:var(--mute); font-size:13px }
.card > details.full { grid-column:1/-1; min-width:0 }
details > summary { color:var(--accent); font-size:14px; min-height:28px }
.note { font-size:15px; overflow-wrap:anywhere } .note h3 { font-size:15px; margin:14px 0 4px }
.note ul, .note ol { padding-left:18px } .note blockquote { margin:6px 0; padding-left:10px; border-left:3px solid var(--line); color:var(--mute) }
.projs { display:flex; flex-wrap:wrap; gap:4px }
.pchip, .fit { display:inline-block; border-radius:999px; padding:1px 8px; font-size:12px; text-decoration:none; white-space:nowrap }
.strong { color:var(--strong); background:var(--strong-bg) } .good { color:var(--good); background:var(--good-bg) }
.possible { color:var(--possible); background:var(--possible-bg) } .no { color:var(--mute); background:var(--chip) }
.fit { font:500 11px var(--mono); text-transform:uppercase; letter-spacing:.04em; padding:1px 7px }
.pgrid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap:10px; align-items:start }
.proj { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:0 14px; min-width:0 }
.proj:target { outline:2px solid var(--accent) }
.proj > summary { display:flex; justify-content:space-between; gap:12px; align-items:center; min-height:52px; color:var(--ink); font-size:16px }
.proj > summary span { color:var(--mute); font-size:13px; white-space:nowrap; margin-left:auto }
.proj .what { margin:0 0 10px; font-size:14px; color:var(--mute) }
.proj ul { list-style:none; margin:0 0 14px; padding:0; display:grid; gap:10px }
.proj li p { margin:3px 0 0; font-size:14px; color:var(--mute) }
.agroups { display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(min(100%, 340px), 1fr)); align-items:start }
.agroup { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; min-width:0 }
.agroup h3 { font-size:16px; margin-bottom:8px } .agroup h3 span { color:var(--mute); font:500 13px var(--mono) }
.agroup ul { list-style:none; margin:0; padding:0; display:grid; gap:10px }
.agroup li { display:flex; gap:10px; font-size:14px; align-items:flex-start } .agroup .thumb { width:27px; height:48px } .agroup li span { min-width:0; overflow-wrap:anywhere }
code { background:var(--chip); padding:1px 4px; border-radius:4px; font:13px var(--mono) }
pre { background:var(--chip); padding:8px; border-radius:6px; overflow-x:auto }
[hidden] { display:none !important }
@media (max-width:480px) { header h1 { font-size:26px } .card { grid-template-columns:76px minmax(0, 1fr); gap:10px } .cover img, .noimg { width:76px } html { scroll-padding-top:170px } }
@media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto } .thumb, .chip { transition:none } }
</style>"""
    script = """<script>
(function () {
  var q = document.getElementById('q'), proj = document.getElementById('proj'), count = document.getElementById('count');
  if (!q) return;
  var cards = [].slice.call(document.querySelectorAll('.card')), topics = [].slice.call(document.querySelectorAll('.topic'));
  var chips = [].slice.call(document.querySelectorAll('.chip')), active = '';
  function apply() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), p = proj.value, shown = 0;
    cards.forEach(function (c) {
      var ok = words.every(function (w) { return c.dataset.s.indexOf(w) >= 0; }) && (!p || c.dataset.p.split(' ').indexOf(p) >= 0);
      c.hidden = !ok; if (ok && (!active || c.closest('.topic').id === active)) shown++;
    });
    topics.forEach(function (t) { t.hidden = (active && t.id !== active) || !t.querySelector('.card:not([hidden])'); });
    count.textContent = shown + ' of ' + cards.length + ' reels';
  }
  function reset() { q.value = ''; proj.value = ''; active = ''; chips.forEach(function (x) { x.setAttribute('aria-pressed', 'false'); }); apply(); }
  chips.forEach(function (c) { c.addEventListener('click', function () {
    active = active === c.dataset.t ? '' : c.dataset.t;
    chips.forEach(function (x) { x.setAttribute('aria-pressed', String(x.dataset.t === active)); }); apply();
  }); });
  q.addEventListener('input', apply); proj.addEventListener('change', apply);
  function follow() {
    var t = location.hash && document.getElementById(location.hash.slice(1)); if (!t) return;
    if (t.classList.contains('proj')) t.open = true;
    if (t.classList.contains('card') && t.hidden || (t.closest && t.closest('.topic[hidden]'))) { reset(); t.scrollIntoView(); }
  }
  addEventListener('hashchange', follow); follow();
  addEventListener('keydown', function (e) { if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); } });
  apply();
})();
</script>"""
    content = f"""<main>
<header><h1>AI Reel Knowledge</h1>
<p>{len(notes)} notes from your saved reels · updated <span style="white-space:nowrap">{last_date}</span></p>
<nav class="jump" aria-label="Sections"><a href="#next">Do next</a><a href="#trends">Trends</a><a href="#reels">All reels</a><a href="#projects">By project</a><a href="#actions">All actions ({len(open_actions)})</a></nav></header>
<section class="block" id="next"><h2>Do next</h2><p class="sub">The highest-value steps, in order. {done_actions} of {len(open_actions) + done_actions} actions done.</p><ol class="next">{do_next}</ol></section>
<section class="block" id="trends"><h2>What's changing in AI</h2><p class="sub">Patterns across your saves. Tap a cover to open that reel's note.</p><div class="trends">{''.join(trend_cards) or '<p>No trends yet.</p>'}</div></section>
<section class="block" id="reels"><h2>All reels</h2>
<div class="bar"><div class="row"><input id="q" type="search" placeholder="Search tools, topics, authors  ( / )" aria-label="Search reels">
<select id="proj" aria-label="Filter by project"><option value="">All projects</option>{''.join(proj_opts)}</select>
<span id="count" role="status" aria-live="polite">{len(notes)} reels</span></div>
<div class="chips" role="group" aria-label="Filter by topic">{filters}</div></div>
{body}</section>
<section class="block" id="projects"><h2>By project</h2><p class="sub">Which reels help which project. Tap to expand.</p><div class="pgrid">{''.join(proj_cards)}</div></section>
<section class="block" id="actions"><h2>All actions</h2><p class="sub">Everything worth trying, grouped by what it takes.</p><div class="agroups">{act_groups or '<p>Nothing open.</p>'}</div></section>
</main>
{script}
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
