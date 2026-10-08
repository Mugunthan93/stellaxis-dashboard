#!/usr/bin/env python3
"""Turn a Claude Code Remote session listing into cloud.json for the dashboard's Cloud tab.

The listing comes from the `list_sessions` tool (claude-code-remote MCP), which only a Claude session
can call. Run from such a session:

  python3 cloud_import.py <list_sessions output file> [-o cloud.json]

`anthropic_cloud` sessions ran in claude.ai/code containers; their transcripts never reach the Mac.
`bridge` sessions are Remote Control sessions running on a computer, so that computer's collector
already counts them.
"""
import argparse
import collections
import datetime as dt
import json
import re
import sys


def load_listing(path: str) -> list:
    text = open(path, encoding="utf-8").read()
    start = text.find('{"ccr"')
    if start < 0:
        start = text.find("{")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return (data.get("ccr") or data).get("data") or []


def repo_of(s: dict) -> str:
    for src in (s.get("session_context") or {}).get("sources") or []:
        url = (src.get("git_repository") or {}).get("url")
        if url:
            return re.sub(r"^https?://(www\.)?", "", url).removesuffix(".git")
    return ""


def convert(rows: list) -> dict:
    out = []
    for s in rows:
        em = s.get("external_metadata") or {}
        usage = em.get("usage") or {}
        kind = s.get("environment_kind") or "unknown"
        out.append({
            "id": s.get("id"), "title": s.get("title") or "", "repo": repo_of(s),
            "where": "cloud" if kind == "anthropic_cloud" else "remote-control" if kind == "bridge" else kind,
            "origin": s.get("origin") or "", "status": (s.get("session_status") or "").replace("SESSION_STATUS_", "").lower(),
            "created": s.get("created_at"), "updated": s.get("updated_at"),
            "model": em.get("last_served_model") or (s.get("session_context") or {}).get("model") or s.get("configured_model"),
            "cc_version": em.get("container_cc_version"),
            "context_used": (em.get("context_usage") or {}).get("used_tokens"),
            "tokens": {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"),
                       "cache_write": usage.get("cache_write_tokens"), "cache_read": usage.get("cache_read_tokens")} if usage else None,
            "cost": round(usage["cost_usd"], 4) if usage.get("cost_usd") is not None else None,
        })
    out.sort(key=lambda r: r["updated"] or "", reverse=True)
    by_where = collections.Counter(r["where"] for r in out)
    by_repo = collections.defaultdict(lambda: {"sessions": 0, "cloud": 0, "remote_control": 0, "last": None, "cost": 0.0})
    for r in out:
        b = by_repo[r["repo"] or "(no repo)"]
        b["sessions"] += 1
        b["cloud" if r["where"] == "cloud" else "remote_control"] += 1
        b["last"] = max(b["last"] or "", r["updated"] or "") or None
        b["cost"] = round(b["cost"] + (r["cost"] or 0), 4)
    return {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {"sessions": len(out), "cloud": by_where.get("cloud", 0), "remote_control": by_where.get("remote-control", 0),
                   "cloud_cost": round(sum(r["cost"] or 0 for r in out if r["where"] == "cloud"), 2),
                   "first": min((r["created"] for r in out if r["created"]), default=None)},
        "repos": sorted(({"repo": k, **v} for k, v in by_repo.items()), key=lambda x: -x["sessions"]),
        "sessions": out,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("listing")
    ap.add_argument("-o", "--out", default="-")
    a = ap.parse_args()
    data = convert(load_listing(a.listing))
    text = json.dumps(data, indent=1)
    if a.out == "-":
        sys.stdout.write(text + "\n")
    else:
        open(a.out, "w", encoding="utf-8").write(text + "\n")
        t = data["totals"]
        print(f"{t['sessions']} sessions: {t['cloud']} cloud, {t['remote_control']} remote-control; cloud cost ${t['cloud_cost']}")


if __name__ == "__main__":
    main()
