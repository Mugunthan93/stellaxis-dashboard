---
name: skill-library
description: Local library of Claude skills downloaded from the repos the owner's saved AI reels named (design/UI, testing, security, agents, docs, video, writing…). Use when a task in any project could use a ready-made skill: search the library, install the right one into this project, then adapt it to the project and the task.
---

# Skill Library

A catalog of real skills (each a `SKILL.md` folder) downloaded from GitHub repos the owner's reel notes point to,
grouped by use case. Installing copies one into the current project's `.claude/skills/`, so it loads only there.

`L` below means `python3 ~/.claude/skills/skill-library/library.py`.

## 1. Search

```bash
L search <words about the task>      # e.g. L search design ui react
L show <id or name>                  # source reel, files, flags, full SKILL.md
```

Pick by the task at hand, not by popularity. Read the candidate's SKILL.md with `show` before choosing.
Skip a skill marked "already in every session": it is installed globally, just use it.

## 2. Install

```bash
L install <id or name>               # into the git root of the current folder
L install <id> --project ~/code/x    # elsewhere
```

- Tell the owner in one line which skill and why before installing.
- If `show` lists scripts ("ships scripts"), it is third-party code: name the scripts and ask the owner before
  installing. Never run them unless the owner says so.
- Installing never overwrites; `--force` replaces an existing copy and loses its edits, so ask first.
- A newly installed skill becomes available to the Skill tool in the next session; in this session, read its
  `SKILL.md` and follow it directly.

## 3. Adapt to this project and this session

Edit only the installed copy (`<project>/.claude/skills/<name>/`), never the library's.

1. Read the project's `CLAUDE.md`, its stack (package.json, pyproject, etc.) and its conventions.
2. Add a short `## In this project` section at the top of the copy's body: the stack, paths, commands and
   house rules that change how the skill applies; drop or override advice that conflicts with the project
   (the project's `CLAUDE.md` wins).
3. Keep the frontmatter `name`; tighten `description` so it triggers for this project's real tasks.
4. Set `"adapted": true` in the copy's `.library.json`.
5. Apply it to the current task right away.

## Keeping it fresh

New reels name new repos. `L harvest` fetches only repos not seen before (`--refresh` re-fetches all) and rebuilds
the catalog shown at http://127.0.0.1:7777/skills. Data lives in `~/Knowledge/skills`.
