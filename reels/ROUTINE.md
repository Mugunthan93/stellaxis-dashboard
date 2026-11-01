# Instagram saved reels → knowledge (daily routine)

Turn the user's newest saved Instagram posts (mostly reels) into markdown notes they can act on.
The user (Instagram @mugunthan_srinivasan) is a developer who builds with Claude Code.

**Agenda (user's rule): build knowledge of AI and keep up with current AI trends. Only AI-related posts
get notes; ignore everything else** (food, gadgets, shopping, memes, general dev tools with no AI
angle, career/business with no AI angle). AI-related means: AI models and research, LLM apps and agents,
Claude Code / Codex / Cursor and other AI coding tools, agent skills, plugins and MCP servers, AI
security, AI in design, AI industry news and trends.
**UI/UX counts too (user's rule, 2026-11-01):** the user builds UI quickly with AI, so UI/UX design
resources are in scope even when the post never says "AI": component libraries (shadcn, Magic UI,
Aceternity…), design-inspiration and pattern sites (Mobbin, Godly…), colour/motion/background tools,
UX laws, and "make your vibe-coded site look premium" lists. Put them under `AI for design & UI` and
say in the note how to hand the resource to Claude (prompt, skill, or copy-paste component).

Everything lives in `~/Knowledge/reels/` (below: `R`). Tools are in `R/.tools/`. The scripts and this
file there are symlinks into the Stellaxis repo (`~/code/stellaxis-dashboard/reels/`), where the code is versioned.

## Running unattended (scheduled)
Nobody is there to approve permission prompts. Each tool needs to be allowed for this task once
(the user clicks "Run now" and approves with "always allow", or sets the task's permission mode).
Keep the set of commands small and identical every run so approvals stick: use only
`Read`/`Write`/`Edit` on files under `R/`, the browser tools, `Artifact`, and these Bash commands:
`cd ~/Knowledge/reels/.tools && ./venv/bin/python reel.py queue.json …`,
`python3 ~/Knowledge/reels/.tools/build_dashboard.py`, and `rm -f ~/Knowledge/reels/.tools/queue.json ~/Knowledge/reels/.tools/out/*.txt`, and
`cp ~/Knowledge/reels/.tools/artifact/index.html <your scratchpad>/ai-reel-knowledge.html` for step 12.
Do not invent extra shell one-liners (use Read/Write/Edit for JSON and markdown edits).

## Hard rules
- **Read-only on Instagram.** Only the GET fetch in `fetch_saved.js`. Never like, comment, unsave,
  follow, DM, change settings, or sign out. Never click anything on instagram.com.
- Use the **built-in browser pane** (`mcp__Claude_Browser__*`) where the user is already logged in.
  Do not use Claude in Chrome. Do not enter any credentials. If not logged in, stop (see step 2).
- Do not touch anything outside `~/Knowledge/reels/` (reading project folders and `gh repo list` for the monthly map refresh is fine).
- Never delete or rewrite existing notes, or untick items in ACTIONS.md. Only add.

## Steps
1. Read `R/.processed.json` (object keyed by shortcode). Build the list of shortcodes whose
   `status` is `ok`, `caption-only` or `skipped` → DONE. Note today's date (YYYY-MM-DD, local time).
2. Open the pane: `mcp__Claude_Browser__preview_start` with url `https://www.instagram.com/`.
   Read `R/.tools/fetch_saved.js`. **LIST mode:** replace `__PROCESSED__` with DONE as a JSON array
   and `__WANT__` with `null`, and run it with `mcp__Claude_Browser__javascript_tool` (javascript_exec).
   It returns `{seen, more, items:[{code,user,date,cap}]}`: every unprocessed saved post (up to 60),
   newest save first. If it returns an `error` item (e.g. HTTP 401/403) or the page shows a login
   form, append `- <date>: FAILED — not logged in / <error>` to `R/RUNLOG.md` and stop. If `items`
   is empty, append `- <date>: nothing new (<seen> saved checked)` to RUNLOG.md, close the tab and stop.
3. **Classify, then fetch media for the AI ones only.**
   a. From `cap` and `user`, mark every clearly non-AI item `skipped` (record
      `{status:"skipped", reason:"<3-6 words>"}` in step 9; no note, no download). Skips are cheap,
      so classify the whole list, not just 10.
   b. Pick up to **10** AI-related or unclear items (vague "comment X for the link" captions count as
      unclear), newest save first. The rest wait for the next run.
   c. **MEDIA mode:** run `fetch_saved.js` again with `__WANT__` = those codes as a JSON array.
      It returns `{found, bytes, codes}` and keeps the full JSON in `window.__q`. Read it with
      `window.__q.slice(0, 10000)`, `window.__q.slice(10000, 20000)`, … until you have all `bytes`.
      Write it **exactly as returned, character for character** (URLs are signed; any change gives
      HTTP 403) to `R/.tools/queue.json` with the Write tool. Close the browser tab — the rest is local.
4. Run (Bash, timeout 600000):
   `cd ~/Knowledge/reels/.tools && ./venv/bin/python reel.py queue.json 2>&1 | grep -E '^(ok|FAIL)'`
   (If an item prints `FAIL ... 403`, its URL was mangled or expired: refetch just that code in MEDIA mode.)
   It writes `R/images/<code>.jpg` (cover image) and `R/.tools/out/<code>.txt` (caption, Whisper transcript, OCR of frames/images).
   Non-English speech (often Tamil) is re-transcribed with a bigger model and translated to English;
   the Speech header then says e.g. `[ta, translated to en]`. Expect ~35 s per English reel and
   ~1 min per translated one. OCR is noisy: use it for exact names/URLs, ignore the junk.
5. For each `ok` item, read `R/.tools/out/<code>.txt`. If the speech/on-screen text shows it is not
   AI-related after all, mark it `skipped` and write no note. Otherwise read `R/.tools/out/<code>.txt` and write `R/<date>-<code>.md`:
   ```
   ---
   source: <url>
   author: "@<user>"
   posted: <YYYY-MM-DD from taken>
   processed: <date>
   topic: <one topic, see below>
   tags: [<3-6 short lowercase tags>]
   projects: [<slugs of projects this helps, fit strong/good/possible; [] if none>]
   cover: images/<code>.jpg
   ---
   # <short descriptive title — what it is, not the hook>

   <a href="<url>"><img src="images/<code>.jpg" width="220" alt="Reel cover"></a>

   > **Reel caption:** <first line of the original caption, verbatim, so the user recognises the reel>

   **TL;DR:** <one or two sentences>

   ## Key ideas
   - ...
   ## Steps / how-to        (only if the reel teaches a procedure)
   1. ...
   ## Tools, repos & links
   - **<exact name>** (`owner/repo` or URL if shown/known) — what it does. Install: `<cmd>` if shown.
   ## Worth acting on?
   <one or two lines: why this matters (or not) for the user's work; "No" is fine.>

   <details><summary>Raw caption & transcript</summary>

   <caption, transcript and on-screen text from the out file, lightly trimmed of hashtags/OCR junk>
   </details>
   ```
   Trust the video (speech/on-screen) over the caption when they differ — "comment X for the link"
   captions usually hide the real tool name, which is in the speech or on screen. Do not invent repo
   URLs: give `owner/repo` only when seen or when you are confident it is the well-known project;
   otherwise write the name and "(repo not shown)". Non-English speech: write the note in English.
   For a `FAIL` item: if the caption alone has substance, write the note from the caption and mark
   status `caption-only`; otherwise record `{status:"failed", tries:n+1}` and retry on a later run
   (after 3 tries, write a caption-only note).
6. Topics (reuse these; add a new AI topic only when nothing fits): `Claude Code & skills`,
   `AI coding tools`, `AI agents & LLM apps`, `Models & research`, `AI security`, `AI for design & UI`,
   `AI industry & trends`.
7. Rewrite the `## Current trends` section at the top of `R/INDEX.md` (right after the intro, before
   the topic sections) from all notes processed in the last 30 days: 3-6 bullets, each a trend in
   one line with the evidence as note links, e.g.
   `- **Skill security is becoming a concern** — scanners and audits for agent skills ([DesW4SFJ5FU](…))`.
   Prefer trends seen in 2+ reels; mark single-reel signals "(early signal)". Also add
   `_Updated <date> from <n> notes._` under the heading.
   Then, under a `## <topic>` heading (create if missing, keep headings in the order
   above), insert at the top of that section:
   `- <img src="images/<code>.jpg" width="72"> [<title>](<date>-<code>.md) — <TL;DR in ≤15 words> · @<user> · <date>`
   (reel.py saves each cover to `R/images/<code>.jpg`; if it is missing, leave the `<img>` out.)
8. Append clearly actionable items to `R/ACTIONS.md` under a `## <date>` heading (create once per
   run, newest run at the bottom), one line each:
   `- [ ] **<Try|Install|Adopt|Read|Cook|Buy?>** <concrete action> — [<code>](<date>-<code>.md)`
   Only real, specific actions (a named tool/repo to try, a workflow or setting to adopt, a recipe
   to cook). Skip vague advice and pure promotion. Skip an action already listed for the same tool.
8b. **Project mapping.** Read `R/PROJECT-MAP.md`. For each new note decide which projects in its table
   it helps, then append a section under `## By reel` (before `## Suggested order`), same format:
   ```
   ### <Short name>: <what it is> ([note](<date>-<code>.md))
   - **<Project name exactly as in the table>: <strong fit|good fit|possible|no>.** <one line: why, and the concrete first use>
   ```
   List only projects with a real reason (include `no` only when it's a tempting but wrong match).
   Project slugs for the note's `projects:` field: PartsHub backend `parthub`, PartsHub AI worker
   `ph-api-ai`, Stellaxis dashboard `stellaxis-dashboard`, Budget app `budget-app`, Desflo `desflo`,
   Managed agent / sandbox `managed-agent`, Curlberry `curlberry`, Your global Claude setup
   `claude-setup` (for new table rows, slugify the name). Update `## Suggested order` if a new reel
   belongs near the top.
   **Monthly refresh** (when the "Built"/"Refreshed" date in the map's intro line is 30+ days old):
   rebuild the project table from local checkouts (`~/arunbro/*`, `~/code/*`, `~/Documents/*` that
   are git repos) and `gh repo list Mugunthan93`, `gh repo list stellaxis-org`, `gh repo list
   self-hosted-ai` (read-only `gh` calls), keeping repos pushed in the last ~4 months; set the intro
   line's date to `Refreshed <date>`. Keep existing `By reel` sections; rename a project in them only
   if it was renamed.
9. Update `R/.processed.json`: for each item set
   `"<code>": {"status": "ok|caption-only|failed|skipped", "note": "<date>-<code>.md" (omit if skipped), "processed": "<date>", "author": "@<user>", "reason": "<only if skipped>"}`.
   Keep it valid JSON; read-modify-write the whole file.
10. Clean up: `rm -f ~/Knowledge/reels/.tools/queue.json ~/Knowledge/reels/.tools/out/*.txt`.
    Append `- <date>: <n> new notes (<codes>), <k> actions, <f> failed; more saved remain: <more>`
    to `R/RUNLOG.md`, adding `, <s> skipped (not AI)`.
11. Rebuild the dashboard: `python3 ~/Knowledge/reels/.tools/build_dashboard.py` (writes
    `R/dashboard.html` for the Mac and `R/.tools/artifact/index.html` for the artifact).
12. Republish the phone-friendly artifact (same URL every time, stored in `R/.tools/artifact_url.txt`,
    currently https://claude.ai/artifact/RiZUZVKQM8rzAdq6tewodE). step 11 already wrote
    `R/.tools/artifact/index.html` (covers embedded, self-contained). Then:
    a. Copy it into your scratchpad directory (or working directory) as `ai-reel-knowledge.html`.
    b. `Artifact` action "read" with that `url` (a publish to an artifact this session has not read is refused).
    c. `Artifact` publish with `url` = that URL and `file_path` = the copy. No `icon`, no `files`.
    If the Artifact tool is unavailable or the publish is refused, do not work around it: append
    `- <date>: artifact NOT republished — <reason>` to RUNLOG.md and continue.
13. Finish with a short summary: the current trends, the new note titles with their topics, and the
    new actions.
