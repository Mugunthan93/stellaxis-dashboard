"""Tests of the weekly share: the tables, the week arithmetic, and the actions that set the budget, the week start and a percent.

  python3 -m unittest tests.test_share
"""
import contextlib
import datetime as dt
import http.client
import http.server
import io
import json
import os
import sqlite3
import sys
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
sys.path.insert(0, str(REPO / "tests"))
from core.app import Core

import prune as P
import share as CLI                         # the command line; the component's own share.py is imported only inside its package
import test_claude_management as TCM        # its FakeHome has no tests of its own
C, COMPS, CENTRE = TCM.C, TCM.COMPS, TCM.CENTRE
PRICE = {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2}}     # 250,000 input tokens cost $1.00

class ShareHome(TCM.FakeHome):
    """The fake home plus two projects (PartHub: code/parts, Curlberry: code/curl), a folder in no project (code/loose),
    no session at the start, and a clock the test sets."""
    def setUp(self):
        super().setUp()
        self.transcript.unlink()
        code = self.app.parent
        self.parts, self.curl, self.loose = code / "parts", code / "curl", code / "loose"
        for d in (self.parts, self.curl, self.loose):
            d.mkdir()
        self.cfg = {**TCM.CFG, "pricing": PRICE, "projects": [
            {"id": "parts", "name": "PartHub", "repos": [str(self.parts)]},
            {"id": "curl", "name": "Curlberry", "repos": [str(self.curl)]}]}
        (self.stx / "config.json").write_text(json.dumps(self.cfg))     # the routes and the command line read the config from here
        patch = mock.patch.object(P, "ARCHIVE", self.claude / "_archive")   # prune worked its archive folder out at import
        patch.start()
        self.addCleanup(patch.stop)
        self.n = 0

    def at(self, when):                                  # the clock, until the test ends or `at` is called again
        os.environ["STELLAXIS_NOW"] = when
        self.addCleanup(os.environ.pop, "STELLAXIS_NOW", None)

    def turn(self, session, folder, ts, dollars, file=None):
        """One assistant turn of `session`, run in `folder` at `ts`, that costs `dollars`."""
        self.n += 1
        f = self.claude / "projects" / (file or "p") / f"{session}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as out:
            out.write(json.dumps({"type": "assistant", "timestamp": ts, "sessionId": session, "cwd": str(folder), "gitBranch": "main",
                                  "message": {"id": f"msg_{self.n:016d}", "model": "claude-opus-5-5",
                                              "usage": {"input_tokens": int(dollars * 250000), "output_tokens": 0}}}) + "\n")

    def act(self, action, apply=True, **args):
        return C.the_core().run_action("claude_management", action, args, apply, self.cfg)

    def sql(self, query, *args):
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db:
            return db.execute(query, args).fetchall()

    def collect(self):
        return C.collect(dict(self.cfg))

    def share(self):
        return C.the_core().read("claude_management", "share", self.cfg)[0]

    def log(self):
        f = self.stx / "actions.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []


SHARE, SET = TCM.CM.share, TCM.CM.share_set
CM = TCM.CM


class WeekTest(unittest.TestCase):
    def test_friday_0230_in_india_is_thursday_2100_utc(self):
        self.assertEqual(SHARE.utc_start("friday", "02:30", "+05:30"), (3, 21))
        self.assertEqual(SHARE.utc_start("Fri", "02:30", "+05:30"), (3, 21))
        self.assertEqual(SHARE.utc_start("monday", "00:00", "+00:00"), (0, 0))
        self.assertEqual(SHARE.utc_start("monday", "01:00", "+05:00"), (6, 20))      # goes back over the start of the list
        self.assertEqual(SHARE.utc_start("sunday", "20:00", "-08:00"), (0, 4))       # goes on over the end of the list

    def test_a_start_that_is_not_on_a_whole_utc_hour_is_refused_in_plain_words(self):
        with self.assertRaises(ValueError) as e:
            SHARE.utc_start("friday", "02:00", "+05:30")
        self.assertEqual(str(e.exception), "Friday 02:00 at +05:30 is Thursday 20:30 UTC. The week must start on a whole UTC hour, "
                         "because the cost is kept for whole UTC hours. The nearest times that work are 01:30 and 02:30.")

    def test_a_part_that_cannot_be_read_is_refused(self):
        for day, time, off in (("someday", "02:30", "+05:30"), ("friday", "25:00", "+05:30"), ("friday", "2.30", "+05:30"),
                               ("friday", "02:30", "+5"), ("friday", "02:30", "IST")):
            with self.assertRaises(ValueError, msg=(day, time, off)):
                SHARE.utc_start(day, time, off)

    def test_the_week_of_a_moment(self):
        utc = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        self.assertEqual(SHARE.bounds(utc("2026-11-06T10:00:00Z"), 3, 21), (utc("2026-11-05T21:00:00Z"), utc("2026-11-12T21:00:00Z")))
        self.assertEqual(SHARE.bounds(utc("2026-11-05T21:00:00Z"), 3, 21)[0], utc("2026-11-05T21:00:00Z"))   # exactly on the start: the new week
        self.assertEqual(SHARE.bounds(utc("2026-11-05T20:59:59Z"), 3, 21)[0], utc("2026-10-29T21:00:00Z"))


class TablesTest(ShareHome):
    def test_a_store_from_before_this_step_gets_the_tables_through_one_change(self):
        # make the store as step 3 left it: the two old tables, and no share table
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            for sql in CM.records.RECORDS[:2]:
                db.execute(sql)
            db.execute("CREATE TABLE core_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            db.execute("INSERT INTO core_schema VALUES ('claude_management', 0)")
        core = Core(self.stx, self.claude, COMPS)
        self.assertIsNone([c for c in core.components if c.name == "claude_management"][0].error)
        self.assertEqual(self.sql("SELECT version FROM core_schema WHERE component = 'claude_management'"), [(1,)])
        names = {r[0] for r in self.sql("SELECT name FROM sqlite_master WHERE name LIKE 'claude_management_share_%'")}
        self.assertEqual(names, {"claude_management_share_" + n for n in ("plan", "percents", "weeks", "use", "warnings")})

    def test_with_nothing_set_there_is_no_budget_and_the_week_starts_thursday_2100_utc(self):
        C.the_core()
        with closing(C.the_core().ctx("claude_management", self.cfg).db()) as db:
            self.assertEqual(SHARE.setting(db), {"budget": None, "day": 3, "hour": 21})
            self.assertEqual(SHARE.percents(db), {})


class ActionsTest(ShareHome):
    def setting(self):
        with closing(C.the_core().ctx("claude_management", self.cfg).db()) as db:
            return SHARE.setting(db)

    def percents(self):
        with closing(C.the_core().ctx("claude_management", self.cfg).db()) as db:
            return SHARE.percents(db)

    def restore(self, manifest, out=lambda _: None, apply=True):
        core = C.the_core()
        core.restore(Path(manifest).parent.name, apply, self.claude / "_archive", core.undo_handlers(self.cfg), out=out)

    def test_a_dry_run_says_what_it_would_do_and_changes_nothing(self):
        r = self.act("set_budget", apply=False, budget=200)
        self.assertEqual((r["ok"], r["steps"], r["manifest"]), (True, ["weekly budget: not set becomes $200.00"], None))
        self.assertEqual(self.setting()["budget"], None)
        self.assertEqual(self.log(), [])
        self.assertFalse((self.claude / "_archive").exists())

    def test_an_applied_budget_is_kept_logged_and_can_be_undone(self):
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=250.5)
        self.assertEqual(r["steps"], ["weekly budget: $200.00 becomes $250.50"])
        self.assertEqual(self.setting()["budget"], 250.5)
        self.assertEqual([(row["action"], row["ok"], row["args"]) for row in self.log()],
                         [("set_budget", True, {"budget": 200}), ("set_budget", True, {"budget": 250.5})])
        said = []
        self.restore(r["manifest"], out=said.append)             # through Core.restore, as TCM.SettingsActions.restore names the folder
        self.assertEqual(said[-1].strip(), "weekly budget: $250.50 goes back to $200.00")
        self.assertEqual(self.setting()["budget"], 200)
        self.assertEqual(self.log()[-1]["action"], "restore")

    def test_removing_the_budget_keeps_the_week_start(self):
        self.act("set_week_start", day="monday", time="05:30", utc_offset="+05:30")
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=None)
        self.assertEqual(r["steps"], ["weekly budget: $200.00 becomes not set"])
        self.assertEqual(self.setting(), {"budget": None, "day": 0, "hour": 0})

    def test_the_same_value_again_writes_nothing(self):
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=200)
        self.assertEqual((r["ok"], r["steps"], r["manifest"]), (True, ["weekly budget: stays $200.00"], None))

    def test_a_budget_that_is_not_a_number_above_0_is_refused(self):
        for bad in ("200", True, -5, 0, float("nan"), float("inf")):
            r = self.act("set_budget", budget=bad)
            self.assertEqual((r["ok"], r["notes"]), (False, ["The weekly budget must be a number above 0, or none to remove it."]), bad)
        self.assertEqual(self.setting()["budget"], None)
        self.assertFalse((self.claude / "_archive").exists())

    def test_the_week_start_is_set_in_local_time_and_kept_in_utc(self):
        r = self.act("set_week_start", day="sunday", time="23:30", utc_offset="+05:30")
        self.assertEqual(r["steps"], ["week start: Thursday 21:00 UTC becomes Sunday 18:00 UTC (Sunday 23:30 at +05:30)"])
        self.assertEqual(r["notes"], ["The weeks are added up again at the next collection."])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (6, 18))
        self.restore(r["manifest"])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (3, 21))

    def test_a_week_start_off_the_whole_utc_hour_is_refused_and_says_what_works(self):
        r = self.act("set_week_start", day="friday", time="02:00", utc_offset="+05:30")
        self.assertFalse(r["ok"])
        self.assertIn("The nearest times that work are 01:30 and 02:30.", r["notes"][0])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (3, 21))

    def test_without_an_offset_the_macs_own_is_used(self):
        with mock.patch.object(SET, "mac_offset", return_value="+05:30"):
            r = self.act("set_week_start", day="friday", time="02:30")
        self.assertEqual((r["ok"], r["steps"], r["manifest"]), (True, ["week start: stays Thursday 21:00 UTC"], None))

    def test_a_percent_is_set_changed_and_removed(self):
        r = self.act("set_percent", project="parts", percent=40)
        self.assertEqual(r["steps"], ["PartHub (parts): no share becomes 40% of the weekly budget"])
        r2 = self.act("set_percent", project="parts", percent=12.5)
        self.assertEqual(r2["steps"], ["PartHub (parts): 40% becomes 12.5% of the weekly budget"])
        r3 = self.act("set_percent", project="parts", percent=None)
        self.assertEqual(r3["steps"], ["PartHub (parts): 12.5% becomes no share"])
        self.assertEqual(self.percents(), {})
        self.restore(r3["manifest"])
        self.assertEqual(self.percents(), {"parts": 12.5})

    def test_a_percent_of_0_is_a_share_and_is_not_the_same_as_none(self):
        self.act("set_percent", project="parts", percent=0)
        self.assertEqual(self.percents(), {"parts": 0})

    def test_shares_may_add_up_to_more_than_100_and_the_action_says_so(self):
        self.act("set_percent", project="parts", percent=80)
        r = self.act("set_percent", project="curl", percent=60)
        self.assertEqual((r["ok"], r["notes"]), (True, ["The shares now add up to 140%. That is allowed."]))

    def test_unassigned_can_be_given_a_share(self):
        r = self.act("set_percent", project="unassigned", percent=10)
        self.assertEqual(r["steps"], ["Unassigned (unassigned): no share becomes 10% of the weekly budget"])

    def test_a_percent_outside_0_to_100_or_an_unknown_project_is_refused(self):
        for bad in (101, -1, "40", True, float("nan")):
            r = self.act("set_percent", project="parts", percent=bad)
            self.assertEqual((r["ok"], r["notes"]), (False, ["The percent must be a number from 0 to 100, or none to remove the share."]), bad)
        r = self.act("set_percent", project="nope", percent=10)
        self.assertEqual(r["notes"], ["No project has the id 'nope'. The ids are: parts, curl, unassigned."])
        self.assertEqual(self.percents(), {})

    def test_an_undo_after_a_later_change_says_so_and_still_puts_the_old_value_back(self):
        first = self.act("set_budget", budget=200)
        self.act("set_budget", budget=300)
        said = []
        self.restore(first["manifest"], out=said.append)
        self.assertEqual(said[-1].strip(), "weekly budget: $300.00 goes back to not set (it was changed again after this action)")
        self.assertEqual(self.setting()["budget"], None)

    def test_a_dry_run_of_the_undo_changes_nothing(self):
        r = self.act("set_budget", budget=200)
        said = []
        self.restore(r["manifest"], out=said.append, apply=False)
        self.assertIn("[dry-run] weekly budget: $200.00 goes back to not set", said[1])
        self.assertEqual(self.setting()["budget"], 200)


W0, W1, W2 = "2026-10-29T21:00:00Z", "2026-11-05T21:00:00Z", "2026-11-12T21:00:00Z"


class ReaderTest(ShareHome):
    def collect(self):
        return C.collect(dict(self.cfg))

    def use(self, week):
        return dict(self.sql("SELECT project, cost FROM claude_management_share_use WHERE week = ?", week))

    def weeks(self):
        return self.sql("SELECT starts, ends, closed FROM claude_management_share_weeks ORDER BY starts")

    def warnings(self):
        return self.sql("SELECT week, project, at FROM claude_management_share_warnings ORDER BY week, project")

    def warned_in_w1(self):                              # budget 10, parts 40% ($4.00): warned in W1 at the second pass
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 4)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [(W1, "parts", "2026-11-06T10:00:00Z")])

    def test_each_project_gets_the_cost_of_its_sessions_in_this_week(self):
        self.turn("a", self.parts, "2026-11-06T09:10:00Z", 3)
        self.turn("b", self.curl, "2026-11-06T09:20:00Z", 2)
        self.turn("c", self.loose, "2026-11-06T09:30:00Z", 1)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.weeks(), [(W1, W2, 0)])
        self.assertEqual(self.use(W1), {"parts": 3.0, "curl": 2.0, "unassigned": 1.0})
        self.assertEqual(self.warnings(), [])                       # no budget: no warning

    def test_a_week_boundary_in_the_middle_of_a_session_splits_its_cost(self):
        self.turn("a", self.parts, "2026-11-05T20:59:00Z", 3)       # one minute before the week starts: the week before
        self.turn("a", self.parts, "2026-11-05T21:00:00Z", 2)       # exactly on the start: this week
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.use(W1), {"parts": 2.0})
        self.assertEqual(self.weeks(), [(W1, W2, 0)])               # the week before had no pass: it gets no row

    def test_a_session_with_files_in_two_projects_repos_counts_once(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3, file="p")
        self.turn("a", self.curl, "2026-11-06T10:00:00Z", 2, file="q")      # the same session, resumed in a repo of another project
        self.at("2026-11-06T11:00:00Z")
        self.collect()
        use = self.use(W1)
        self.assertEqual(sum(use.values()), 5.0)
        self.assertEqual(len(use), 1)                               # all of it in one project

    def test_a_repo_that_moves_to_another_project_takes_its_weeks_cost_along(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.cfg["projects"][0]["repos"], self.cfg["projects"][1]["repos"] = [], [str(self.curl), str(self.parts)]
        self.at("2026-11-06T10:05:00Z")
        self.collect()
        self.assertEqual(self.use(W1), {"curl": 3.0})

    def test_a_new_week_starts_at_zero_and_the_week_before_is_kept(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.turn("a", self.parts, "2026-11-12T20:30:00Z", 1)       # still week 1, after its last pass
        self.turn("a", self.parts, "2026-11-12T21:30:00Z", 2)       # week 2
        self.at("2026-11-12T22:00:00Z")
        self.collect()
        self.assertEqual(self.weeks(), [(W1, W2, 1), (W2, "2026-11-19T21:00:00Z", 0)])
        self.assertEqual(self.use(W1), {"parts": 4.0})              # added up one last time, with the late turn
        self.assertEqual(self.use(W2), {"parts": 2.0})

    def test_a_closed_week_keeps_its_numbers_when_the_transcript_is_gone(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.at("2026-11-13T10:00:00Z")
        self.collect()                                              # closes week 1
        (self.claude / "projects" / "p" / "a.jsonl").unlink()
        self.turn("b", self.curl, "2026-11-13T11:00:00Z", 1)
        self.at("2026-11-13T12:00:00Z")
        self.collect()
        self.assertEqual(self.use(W1), {"parts": 3.0})

    def test_a_pass_after_a_long_gap_closes_the_old_week_and_makes_no_week_for_the_gap(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.at("2026-11-27T10:00:00Z")                             # three weeks later
        self.collect()
        self.assertEqual(self.weeks(), [(W1, W2, 1), ("2026-11-26T21:00:00Z", "2026-12-03T21:00:00Z", 0)])
        self.assertEqual(self.use(W1), {"parts": 3.0})

    def test_a_project_that_uses_all_of_its_share_is_warned_once(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)        # an allowance of $4.00
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [])                       # $3.00 of $4.00
        self.turn("a", self.parts, "2026-11-06T10:30:00Z", 1)
        self.at("2026-11-06T11:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [(W1, "parts", "2026-11-06T11:00:00Z")])      # $4.00 of $4.00: all of it
        self.turn("a", self.parts, "2026-11-06T11:30:00Z", 5)
        self.at("2026-11-06T12:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [(W1, "parts", "2026-11-06T11:00:00Z")])      # not again, and the first time stays
        self.assertEqual(self.sql("SELECT cost, allowance FROM claude_management_share_warnings"), [(4.0, 4.0)])

    def test_a_new_week_can_warn_again_and_the_old_warning_stays(self):
        self.warned_in_w1()
        self.turn("a", self.parts, "2026-11-12T21:30:00Z", 4)
        self.at("2026-11-12T22:00:00Z")
        self.collect()
        self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts"), (W2, "parts")])

    def test_a_percent_of_0_warns_at_the_first_cent_and_no_percent_never_warns(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=0)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 1)
        self.turn("b", self.curl, "2026-11-06T09:00:00Z", 9)        # Curlberry has no percent
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts")])
        self.assertEqual(self.sql("SELECT project, percent FROM claude_management_share_use ORDER BY project"), [("curl", None), ("parts", 0.0)])

    def test_a_share_of_0_with_no_use_does_not_warn_and_still_has_a_row(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=0)
        self.turn("b", self.curl, "2026-11-06T09:00:00Z", 1)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [])
        self.assertEqual(self.use(W1), {"curl": 1.0, "parts": 0.0})

    def test_shares_that_add_up_to_more_than_100_each_count_on_their_own(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=80)
        self.act("set_percent", project="curl", percent=60)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 8)
        self.turn("b", self.curl, "2026-11-06T09:00:00Z", 5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts")])

    def test_without_a_budget_nothing_warns(self):
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 100)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [])
        self.assertEqual(self.sql("SELECT project, cost, percent FROM claude_management_share_use"), [("parts", 100.0, 40.0)])

    def test_unassigned_warns_only_when_it_was_given_a_share(self):
        self.act("set_budget", budget=10)
        self.turn("c", self.loose, "2026-11-06T09:00:00Z", 5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.warnings(), [])
        self.act("set_percent", project="unassigned", percent=10)
        self.at("2026-11-06T10:05:00Z")
        self.collect()
        self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "unassigned")])

    def test_the_week_start_changed_in_the_middle_of_a_week_cuts_the_running_week_and_counts_no_hour_twice(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)       # Friday
        self.turn("a", self.parts, "2026-11-09T05:00:00Z", 2)       # Monday morning
        self.at("2026-11-09T10:00:00Z")
        self.collect()
        self.assertEqual(self.use(W1), {"parts": 5.0})
        self.act("set_week_start", day="monday", time="00:00", utc_offset="+00:00")
        self.at("2026-11-09T10:05:00Z")
        self.collect()
        self.assertEqual(self.weeks(), [(W1, "2026-11-09T00:00:00Z", 1), ("2026-11-09T00:00:00Z", "2026-11-16T00:00:00Z", 0)])
        self.assertEqual(self.use(W1), {"parts": 3.0})
        self.assertEqual(self.use("2026-11-09T00:00:00Z"), {"parts": 2.0})

    def test_a_week_start_moved_back_before_the_running_week_takes_the_running_week_in(self):
        self.turn("a", self.parts, "2026-11-03T09:00:00Z", 1)       # Tuesday, week 0
        self.at("2026-11-03T10:00:00Z")
        self.collect()
        self.turn("a", self.parts, "2026-11-04T09:00:00Z", 2)       # Wednesday, week 0
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 4)       # Friday, week 1
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual((self.use(W0), self.use(W1)), ({"parts": 3.0}, {"parts": 4.0}))
        self.act("set_week_start", day="wednesday", time="00:00", utc_offset="+00:00")
        self.at("2026-11-06T10:05:00Z")
        self.collect()
        new = "2026-11-04T00:00:00Z"
        self.assertEqual(self.weeks(), [(W0, new, 1), (new, "2026-11-11T00:00:00Z", 0)])
        self.assertEqual((self.use(W0), self.use(new)), ({"parts": 1.0}, {"parts": 6.0}))

    def test_a_warning_of_a_week_that_is_cut_is_made_again_only_when_it_is_still_true(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)        # allowance 4
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)       # Friday
        self.turn("a", self.parts, "2026-11-09T05:00:00Z", 2)       # Monday
        self.at("2026-11-09T10:00:00Z")
        self.collect()
        self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts")])
        self.act("set_week_start", day="monday", time="00:00", utc_offset="+00:00")
        self.at("2026-11-09T10:05:00Z")
        self.collect()
        self.assertEqual(self.use(W1), {"parts": 3.0})              # not over
        self.assertEqual(self.warnings(), [])

    def test_a_session_project_management_does_not_list_counts_as_unassigned(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.turn("b", self.curl, "2026-11-06T09:30:00Z", 2)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        SHARE.update(C.the_core().ctx("claude_management", self.cfg), [{"id": "a", "project": "parts"}], dt.datetime(2026, 11, 6, 10, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(self.use(W1), {"parts": 3.0, "unassigned": 2.0})

    def test_a_listed_session_with_no_hours_adds_nothing(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        SHARE.update(C.the_core().ctx("claude_management", self.cfg),
                     [{"id": "a", "project": "parts"}, {"id": "ghost", "project": "curl"}], dt.datetime(2026, 11, 6, 10, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(self.use(W1), {"parts": 3.0})

    def test_when_project_management_gives_no_session_nothing_changes(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 4)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        tables = "SELECT * FROM claude_management_share_%s"
        before = [self.sql(tables % t) for t in ("weeks", "use", "warnings")]
        self.assertTrue(before[0] and before[1] and before[2])
        [pm] = [c for c in C.the_core().components if c.name == "project_management"]
        self.at("2026-11-13T10:00:00Z")
        with mock.patch.dict(pm.offers, {"sessions": lambda ctx, **args: []}):
            self.collect()
        self.assertEqual([self.sql(tables % t) for t in ("weeks", "use", "warnings")], before)

    def test_with_the_store_down_the_pass_has_no_error_of_the_share(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
        self.at("2026-11-06T10:00:00Z")
        (self.stx / "stellaxis.db").mkdir()
        snap = self.collect()
        self.assertTrue(snap["sessions"])
        self.assertIsNone(self.status()["index_error"])

    def test_a_pass_that_is_not_a_collection_adds_nothing_up(self):
        C.the_core().run_pass(self.cfg, {})
        self.assertEqual(self.weeks(), [])


class ViewTest(ShareHome):
    def line(self, project):
        return [l for l in self.share()["projects"] if l["id"] == project][0]

    def over_in_w1(self):                                # budget 10, parts 40% ($4.00), parts uses 5 at a pass at 10:00
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()

    def closed_rows(self, *rows):                        # closed week rows (starts, ends, budget), by SQL
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            for w in rows:
                db.execute("INSERT INTO claude_management_share_weeks (starts, ends, budget, closed, updated) VALUES (?, ?, ?, 1, ?)", (*w, w[1]))

    def test_the_answer_for_a_week_with_a_budget(self):
        self.act("set_budget", budget=200)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
        self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2.5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        v = self.share()
        self.assertEqual({k: v[k] for k in ("now", "week", "week_start", "budget", "used", "updated")},
                         {"now": "2026-11-06T10:00:00Z", "week": {"start": W1, "end": W2}, "week_start": {"day": "thursday", "hour": 21},
                          "budget": 200.0, "used": 12.5, "updated": "2026-11-06T10:00:00Z"})
        self.assertEqual(v["projects"], [
            {"id": "parts", "name": "PartHub", "percent": 40.0, "allowance": 80.0, "use": 10.0, "left": 70.0, "over": False, "warned_at": None},
            {"id": "curl", "name": "Curlberry", "percent": None, "allowance": None, "use": 0, "left": None, "over": False, "warned_at": None}])
        self.assertEqual(v["unassigned"], {"id": "unassigned", "name": "Unassigned", "percent": None, "allowance": None, "use": 2.5,
                                           "left": None, "over": False, "warned_at": None})
        self.assertEqual(v["past"], [])

    def test_a_project_over_its_share_is_marked_and_carries_the_time_of_its_warning(self):
        self.over_in_w1()
        line = self.line("parts")
        self.assertEqual((line["allowance"], line["use"], line["left"], line["over"], line["warned_at"]),
                         (4.0, 5.0, -1.0, True, "2026-11-06T10:00:00Z"))

    def test_a_budget_raised_after_the_warning_ends_over_and_keeps_the_time_of_the_warning(self):
        self.over_in_w1()
        self.act("set_budget", budget=100)
        line = self.line("parts")
        self.assertEqual((line["allowance"], line["left"], line["over"], line["warned_at"]), (40.0, 35.0, False, "2026-11-06T10:00:00Z"))

    def test_a_new_budget_shows_at_once_without_a_pass(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.line("parts")["allowance"], None)
        self.act("set_budget", budget=100)
        self.act("set_percent", project="parts", percent=40)
        line = self.line("parts")
        self.assertEqual((self.share()["budget"], line["percent"], line["allowance"], line["use"], line["left"], line["over"]),
                         (100.0, 40.0, 40.0, 10.0, 30.0, False))

    def test_without_a_budget_the_use_is_there_and_no_allowance(self):
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        v = self.share()
        line = self.line("parts")
        self.assertEqual((v["budget"], v["used"], line["percent"], line["use"], line["allowance"], line["left"], line["over"]),
                         (None, 10.0, 40.0, 10.0, None, None, False))

    def test_before_the_first_pass_of_a_new_week_the_use_is_zero_and_updated_is_none(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.at("2026-11-13T10:00:00Z")
        v = self.share()
        self.assertEqual((v["week"], v["used"], v["updated"], v["past"]), ({"start": W2, "end": "2026-11-19T21:00:00Z"}, 0, None, []))
        self.assertEqual([l["use"] for l in v["projects"]], [0, 0])

    def test_past_weeks_come_newest_first_with_the_numbers_they_were_closed_with(self):
        self.act("set_budget", budget=10)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 5)
        self.turn("b", self.curl, "2026-11-06T09:00:00Z", 1)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.at("2026-11-12T22:00:00Z")
        self.collect()
        self.act("set_budget", budget=100)
        self.act("set_percent", project="parts", percent=10)
        v = self.share()
        [past] = v["past"]
        self.assertEqual((past["start"], past["end"], past["budget"], past["used"]), (W1, W2, 10.0, 6.0))
        self.assertEqual([(l["id"], l["name"], l["percent"], l["allowance"], l["use"], l["over"]) for l in past["lines"]],
                         [("parts", "PartHub", 40.0, 4.0, 5.0, True), ("curl", "Curlberry", None, None, 1.0, False)])
        self.assertEqual(past["lines"][0]["warned_at"] is not None, True)

    def test_at_most_12_past_weeks_are_given(self):
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        start = dt.datetime(2026, 10, 29, 21, tzinfo=dt.timezone.utc)
        weeks = [(CM.share.iso(start - dt.timedelta(days=7 * i)), CM.share.iso(start - dt.timedelta(days=7 * (i - 1))), None) for i in range(14)]
        self.closed_rows(*weeks)
        past = self.share()["past"]
        self.assertEqual((len(past), past[0]["start"], past[-1]["start"]), (12, weeks[0][0], weeks[11][0]))

    def test_a_project_no_longer_in_the_config_keeps_its_id_as_its_name_in_a_past_week(self):
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.closed_rows((W0, W1, 10.0))
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            db.execute("INSERT INTO claude_management_share_use (week, project, cost, percent) VALUES (?, 'gone', 3.0, 50.0)", (W0,))
        [line] = self.share()["past"][0]["lines"]
        self.assertEqual((line["id"], line["name"], line["allowance"], line["use"], line["over"]), ("gone", "gone", 5.0, 3.0, False))

    def test_an_id_that_comes_twice_is_listed_once_and_a_project_named_unassigned_holds_the_unassigned_line(self):
        self.cfg["projects"] += [{"id": "parts", "name": "Again", "repos": []}, {"id": "unassigned", "name": "Mine", "repos": []}]
        self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        v = self.share()
        self.assertEqual([(l["id"], l["name"]) for l in v["projects"]], [("parts", "PartHub"), ("curl", "Curlberry"), ("unassigned", "Mine")])
        self.assertEqual(v["projects"][2]["use"], 2.0)
        self.assertIsNone(v["unassigned"])

    def test_without_project_management_there_is_no_project_line(self):
        self.act("set_budget", budget=200)
        [pm] = [c for c in C.the_core().components if c.name == "project_management"]
        with mock.patch.dict(pm.offers, {"projects": lambda ctx, **a: [], "sessions": lambda ctx, **a: []}):
            self.at("2026-11-06T10:00:00Z")
            self.collect()
            v = self.share()
        self.assertEqual((v["projects"], v["unassigned"], v["budget"]), ([], None, 200.0))


class RouteTest(ShareHome):
    def setUp(self):
        super().setUp()
        C.the_core()                                     # the components are imported on this thread, as `serve` does
        self.serve()

    def serve(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)     # port 0: never 7777
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.port = srv.server_address[1]
        self.addCleanup(thread.join)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def call(self, method, path, body=None, headers=None):
        raw = body if isinstance(body, str) or body is None else json.dumps(body)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, raw, {"Content-Type": "application/json", **(headers or {})})
            r = c.getresponse()
            data = r.read()
            return r.status, (json.loads(data) if data[:1] == b"{" else data.decode())     # a refusal of the server itself is plain text
        finally:
            c.close()

    def test_get_gives_the_answer_of_the_offer(self):
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.assertEqual(self.call("GET", "/api/share"), (200, self.share()))

    def test_a_post_without_apply_true_is_a_dry_run(self):
        code, r = self.call("POST", "/api/share/budget", {"budget": 200, "apply": False})
        self.assertEqual((code, r["applied"], r["ok"], r["steps"], r["manifest"]), (200, False, True, ["weekly budget: not set becomes $200.00"], None))
        self.assertIsNone(self.call("GET", "/api/share")[1]["budget"])
        self.assertEqual(self.log(), [])

    def test_the_three_posts_apply_and_each_writes_a_manifest(self):
        code, r = self.call("POST", "/api/share/budget", {"budget": 200, "apply": True})
        self.assertEqual((code, r["applied"], Path(r["manifest"]).is_file()), (200, True, True))
        code, r = self.call("POST", "/api/share/week-start", {"day": "monday", "time": "05:30", "utc_offset": "+05:30", "apply": True})
        self.assertEqual((code, r["result"]["new"]), (200, [0, 0]))
        code, r = self.call("POST", "/api/share/percent", {"project": "parts", "percent": 40, "apply": True})
        self.assertEqual(code, 200)
        v = self.call("GET", "/api/share")[1]
        self.assertEqual((v["budget"], v["week_start"], v["projects"][0]["percent"]), (200.0, {"day": "monday", "hour": 0}, 40.0))
        code, r = self.call("POST", "/api/share/percent", {"project": "parts", "percent": None, "apply": True})
        self.assertEqual((code, self.call("GET", "/api/share")[1]["projects"][0]["percent"]), (200, None))

    def test_a_refused_value_answers_400_with_the_sentence(self):
        code, r = self.call("POST", "/api/share/budget", {"budget": "200", "apply": True})
        self.assertEqual((code, r["error"]), (400, "The weekly budget must be a number above 0, or none to remove it."))
        self.assertEqual((r["ok"], r["applied"]), (False, True))
        code, r = self.call("POST", "/api/share/week-start", {"day": "friday", "time": "02:00", "utc_offset": "+05:30", "apply": True})
        self.assertEqual(code, 400)
        self.assertIn("The nearest times that work are 01:30 and 02:30.", r["error"])

    def test_a_number_too_large_for_a_float_is_refused_like_any_bad_value(self):
        code, r = self.call("POST", "/api/share/budget", '{"budget": 1' + "0" * 400 + ', "apply": true}')
        self.assertEqual((code, r["error"]), (400, "The weekly budget must be a number above 0, or none to remove it."))
        code, r = self.call("POST", "/api/share/percent", '{"project": "parts", "percent": 1' + "0" * 400 + ', "apply": true}')
        self.assertEqual((code, r["error"]), (400, "The percent must be a number from 0 to 100, or none to remove the share."))

    def test_a_body_that_lacks_a_key_or_a_true_or_false_apply_answers_400(self):
        for path, body in (("/api/share/budget", {"budget": 200}), ("/api/share/budget", {"budget": 200, "apply": "yes"}),
                           ("/api/share/budget", {"apply": True}), ("/api/share/week-start", {"day": "friday", "apply": True}),
                           ("/api/share/week-start", {"time": "02:30", "apply": True}),
                           ("/api/share/percent", {"project": "parts", "apply": True}), ("/api/share/percent", {"percent": 4, "apply": True})):
            self.assertEqual(self.call("POST", path, body)[0], 400, (path, body))
        code, r = self.call("POST", "/api/share/budget", {"budget": 200, "apply": "yes"})
        self.assertEqual(r["error"], '"apply" must be true or false')
        self.assertIn('"budget"', self.call("POST", "/api/share/budget", {"apply": True})[1]["error"])
        self.assertIsNone(self.call("GET", "/api/share")[1]["budget"])

    def test_another_origin_is_refused_and_another_path_is_not_found(self):
        self.assertEqual(self.call("POST", "/api/share/budget", {"budget": 200, "apply": True}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/share", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/share/nothing"), (404, {"error": "no such share endpoint"}))
        self.assertEqual(self.call("POST", "/api/share", {"apply": True})[0], 404)
        self.assertIsNone(self.call("GET", "/api/share")[1]["budget"])

    def test_with_the_store_down_get_answers_503_in_plain_words(self):
        db = self.stx / "stellaxis.db"
        db.unlink()
        db.mkdir()
        C._core = None                                   # the next core opens the store as it is now
        C.the_core()
        code, r = self.call("GET", "/api/share")
        self.assertEqual(code, 503)
        self.assertTrue(r["error"].startswith("The store is down"))


class CommandTest(ShareHome):
    def run_cli(self, *args):
        out = io.StringIO()
        self.exit_text = None
        with mock.patch.object(sys, "argv", ["share.py", *args]), contextlib.redirect_stdout(out):
            try:
                CLI.main()
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                self.exit_text = e.code if isinstance(e.code, str) else None
        return code, out.getvalue()

    def test_show_with_no_budget(self):
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
        self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2.5)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        code, out = self.run_cli("show")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [
            "Week: 2026-11-05 21:00 UTC to 2026-11-12 21:00 UTC (a new week starts every Thursday 21:00 UTC)",
            "Budget: not set. Used so far: $12.50.",
            "PartHub (parts): $10.00 used, no share",
            "Curlberry (curl): $0.00 used, no share",
            "Unassigned (unassigned): $2.50 used, no share"])

    def test_show_with_a_budget_and_a_project_over_its_share(self):
        self.act("set_budget", budget=20)
        self.act("set_percent", project="parts", percent=40)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        code, out = self.run_cli("show")
        self.assertIn("Budget: $20.00 a week. Used so far: $10.00.", out)
        self.assertIn("PartHub (parts): $10.00 used of $8.00 (40% of the budget), -$2.00 left. All of its share is used.", out)
        self.assertNotIn("Past weeks:", out)

    def test_show_with_a_percent_and_no_budget_and_with_past_weeks(self):
        self.act("set_percent", project="parts", percent=40)
        self.act("set_budget", budget=200)
        self.turn("a", self.parts, "2026-11-06T09:00:00Z", 31)
        self.at("2026-11-06T10:00:00Z")
        self.collect()
        self.at("2026-11-12T22:00:00Z")
        self.collect()
        self.act("set_budget", budget=None)
        self.turn("a", self.parts, "2026-11-12T22:00:00Z", 1)
        self.collect()
        code, out = self.run_cli("show")
        lines = out.splitlines()
        self.assertIn("Budget: not set. Used so far: $1.00.", lines)
        self.assertIn("PartHub (parts): $1.00 used, 40% of a budget that is not set", lines)
        self.assertEqual(lines[lines.index("Past weeks:") + 1:], ["2026-11-05 21:00 UTC to 2026-11-12 21:00 UTC: $31.00 used of $200.00"])

    def test_a_dry_run_prints_the_plan_and_changes_nothing(self):
        code, out = self.run_cli("budget", "200")
        self.assertEqual(code, 0)
        self.assertIn("  [dry-run] weekly budget: not set becomes $200.00", out)
        self.assertIn("Nothing changed. Re-run with --apply to execute.", out)
        self.assertEqual(self.log(), [])
        self.assertFalse((self.claude / "_archive").exists())

    def test_apply_prints_the_manifest_and_the_undo_line_and_the_undo_works(self):
        code, out = self.run_cli("budget", "200", "--apply")
        self.assertIn("  weekly budget: not set becomes $200.00", out)
        self.assertIn("Manifest: ", out)
        undo_line = [l for l in out.splitlines() if l.startswith("Undo:")][0]
        self.assertIn("prune.py restore ", undo_line)
        self.assertTrue(undo_line.endswith(" --apply"))
        stamp = undo_line.split()[-2]
        with contextlib.redirect_stdout(io.StringIO()):
            P.restore(stamp, True)
        self.assertIsNone(self.share()["budget"])

    def test_none_removes_and_the_week_start_and_percent_commands_work(self):
        self.run_cli("budget", "200", "--apply")
        self.assertEqual(self.share()["budget"], 200.0)
        self.run_cli("budget", "none", "--apply")
        self.assertIsNone(self.share()["budget"])
        code, out = self.run_cli("week-start", "friday", "02:30", "--utc-offset", "+05:30")
        self.assertIn("week start: stays Thursday 21:00 UTC", out)
        code, out = self.run_cli("week-start", "monday", "05:30", "--utc-offset", "+05:30", "--apply")
        self.assertEqual(self.share()["week_start"], {"day": "monday", "hour": 0})
        code, out = self.run_cli("percent", "parts", "40", "--apply")
        self.assertEqual(self.share()["projects"][0]["percent"], 40.0)
        code, out = self.run_cli("percent", "parts", "none", "--apply")
        self.assertIsNone(self.share()["projects"][0]["percent"])

    def test_a_refusal_prints_its_sentence_and_exits_1(self):
        code, out = self.run_cli("week-start", "friday", "02:00", "--utc-offset", "+05:30", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("The nearest times that work are 01:30 and 02:30.", out)
        self.assertNotIn("Nothing changed", out)
        code, out = self.run_cli("budget", "lots")
        self.assertEqual(code, 1)
        self.assertIn("The weekly budget must be a number above 0, or none to remove it.", out)
        self.assertIsNone(self.share()["budget"])

    def test_with_the_store_down_show_says_so_and_exits_1(self):
        db = self.stx / "stellaxis.db"
        db.unlink(missing_ok=True)
        db.mkdir()
        C._core = None
        code, out = self.run_cli("show")
        self.assertEqual((code, out, self.exit_text), (1, "", "The share cannot be read: the store is down"))
