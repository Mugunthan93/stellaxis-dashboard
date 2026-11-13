#!/usr/bin/env python3
"""Stellaxis share: the weekly share of the Claude plan for each project.

  python3 share.py show
  python3 share.py budget <amount|none> [--apply]
  python3 share.py week-start <day> <HH:MM> [--utc-offset=+05:30] [--apply]
  python3 share.py percent <project id> <percent|none> [--apply]

Every setting command is a dry run until --apply. An applied one can be undone with: python3 prune.py restore <stamp> --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402
from core import undo  # noqa: E402


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def when(t: str) -> str:
    """2026-11-05T21:00:00Z -> 2026-11-05 21:00 UTC"""
    return f"{t[:10]} {t[11:16]} UTC"


def project_line(l: dict, budget) -> str:
    text = f"{l['name']} ({l['id']}): {money(l['use'])} used"
    if l["percent"] is None:
        return text + ", no share"
    if budget is None:
        return text + f", {l['percent']:g}% of a budget that is not set"
    text += f" of {money(l['allowance'])} ({l['percent']:g}% of the budget), {money(l['left'])} left"
    return text + (". All of its share is used." if l["over"] else "")


def show(core) -> None:
    got = core.read("claude_management", "share", C.load_config())
    if not got:
        sys.exit("The share cannot be read: the store is down")
    v = got[0]
    print(f"Week: {when(v['week']['start'])} to {when(v['week']['end'])} "
          f"(a new week starts every {v['week_start']['day'].capitalize()} {v['week_start']['hour']:02d}:00 UTC)")
    print(f"Budget: {'not set' if v['budget'] is None else money(v['budget']) + ' a week'}. Used so far: {money(v['used'])}.")
    for l in v["projects"] + ([v["unassigned"]] if v["unassigned"] else []):
        print(project_line(l, v["budget"]))
    if v["past"]:
        print("Past weeks:")
    for w in v["past"]:
        print(f"{when(w['start'])} to {when(w['end'])}: {money(w['used'])} used" + ("" if w["budget"] is None else f" of {money(w['budget'])}"))


def value(text: str):
    """none -> None, a number -> float, anything else as it is (the action refuses it with its own sentence)."""
    if text.lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    for name, args in (("budget", ["amount"]), ("week-start", ["day", "time"]), ("percent", ["project", "percent"])):
        p = sub.add_parser(name)
        for a in args:
            p.add_argument(a)
        if name == "week-start":
            p.add_argument("--utc-offset")
        p.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    core = C.the_core()
    if not any(c.name == "claude_management" and not c.error for c in core.components):
        sys.exit("Claude management is not loaded")
    if a.cmd == "show":
        return show(core)
    if a.cmd == "budget":
        action, args = "set_budget", {"budget": value(a.amount)}
    elif a.cmd == "week-start":
        action, args = "set_week_start", {"day": a.day, "time": a.time, **({"utc_offset": a.utc_offset} if a.utc_offset else {})}
    else:
        action, args = "set_percent", {"project": a.project, "percent": value(a.percent)}
    plan = undo.Plan(a.apply, "share-" + a.cmd, C.CLAUDE_DIR / "_archive",
                     undo_hint=lambda name: f"python3 {C.short_path(Path(__file__).with_name('prune.py'))} restore {name} --apply")
    if not core.run_action("claude_management", action, args, a.apply, C.load_config(), plan=plan)["ok"]:   # the core finishes the plan
        sys.exit(1)


if __name__ == "__main__":
    main()
