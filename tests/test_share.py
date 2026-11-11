"""Tests of the weekly share: the tables, the week arithmetic, and the actions that set the budget, the week start and a percent.

  python3 -m unittest tests.test_share
"""
import datetime as dt
import json
import os
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
sys.path.insert(0, str(REPO / "tests"))
from core.app import Core

import prune as P
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
