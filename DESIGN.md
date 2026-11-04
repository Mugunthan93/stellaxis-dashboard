# Stellaxis design system

The rules every dashboard page follows, taken from the pages as they are. Read this before changing
UI. `dashboard/index.html`, `hierarchy.html` and `settings.html` each carry the same tokens in
`:root`; a colour or font changes in all three plus `shell.html`, or not at all. `/reels/` has its
own Instagram-style look and is not covered here.

## Colour

Never write a hex value in a rule; use a token. Every token has a dark value, set twice: under
`prefers-color-scheme: dark` and under `:root[data-theme="dark"]`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f3f4f1` | `#121614` | page |
| `--surface` | `#ffffff` | `#1a201d` | panels, inputs |
| `--sunk` | `#e9ebe6` | `#222a26` | table heads, hover, chips |
| `--ink` | `#17201c` | `#e6ebe7` | text |
| `--muted` | `#5a6560` | `#9aa69f` | secondary text |
| `--line` | `#d8ddd7` | `#2e3833` | borders |
| `--accent` / `--accent-soft` | `#2c55c9` / `#dfe6f8` | `#7c9cf0` / `#24304f` | the primary action, selected state, links |
| `--good` / `--good-soft` | `#23784a` / `#dcefe3` | `#5cc48b` / `#1d3428` | healthy, used |
| `--warn` / `--warn-soft` | `#9a6512` / `#f6ead2` | `#e0ad55` / `#3a2f1a` | needs a look |
| `--crit` / `--crit-soft` | `#b63a26` / `#f7e0da` | `#ef8a76` / `#40231d` | broken, costly |
| `--c1`…`--c7` | see `:root` | see `:root` | chart series only, in order |

Good, warn and crit mean state; never use them for decoration. The shell's `--sx-*` tokens mirror
these for the sidebar.

## Type

One font pair plus one mono for figures, loaded from Google Fonts.

- **Headings:** Bricolage Grotesque, weight 700 (`--display`).
- **Body:** Instrument Sans, 400 to 600 (`--body`), 14px on 1.5 line height.
- **Figures, paths, ids:** JetBrains Mono with tabular numbers (`--mono`, classes `.num`, `.mono`, `td.n`).

One `h1` per page at 1.55rem; `h2` panel titles at 1.12rem.

## Layout

- `.wrap` centres the page at 1240px with a 16px gutter and a 22px gap between sections.
- Group content in a `.panel`: `--surface`, 1px `--line` border, `--r` (10px) radius, 18px padding.
- A panel starts with `.panel-head`: an `h2` and a one-line muted `p` that says what it shows.
- Numbers go right-aligned in `td.n`. Long tables sit in `.tablewrap` so only the table scrolls.
- No horizontal page scroll at phone width.

## Components

Reuse before adding: `.btn` and `.btn.primary`, `.seg` (segmented), `.tabs`, `.chip` (filters),
`.pill` with `good`, `warn`, `crit`, `acc`, `.notice`, `.kpis`, `.minibar`. At most one `.btn.primary`
in a view.

## Behaviour

- Every action shows a result where it happened, within 400ms.
- A section with nothing to show is `hidden`, not empty.
- Show a status only when it needs attention; healthy plumbing stays out of the way.
- Every table has an `.empty` row for no data.
- Focus shows `:focus-visible` in `--accent`. Motion sits behind `prefers-reduced-motion`.
- Numbers come from the snapshot; label anything estimated. Never invent a figure.
- Words are plain: say what it means for the user, not the mechanism.

## Checking a UI change

Work through the 20 UX laws in `~/Knowledge/reels/research/ux-laws-skill/SKILL.md`. Two of its rules
are adjusted here on purpose: this is a dense desktop tool, so body text stays 14px rather than 16px,
and controls reach 44px only on touch screens (`@media (pointer: coarse)`).

Then check light and dark, and desktop and phone width, in the browser.
