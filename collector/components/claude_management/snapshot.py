"""The snapshot: the setup joined with its use, priced, with the totals the dashboard shows."""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import socket
import time

import core.util
from core.util import parse_duration, short_path

from .setup import item
from .usage import CACHE_TTL_SECONDS, cost_of, norm_mcp, parse_ts, tok


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def interactive(s: dict) -> bool:
    """Full sessions load Claude's built-in tool schemas. Scripted runs (claude -p, Agent SDK) and one-turn
    desktop helpers don't, and would drag baseline medians down to ~1K."""
    return not (s.get("entrypoint") or "").startswith("sdk") and bool((s.get("measured_context") or {}).get("tools_builtin"))


def build(ctx, rows: list, n_files: int, items: list, projects: list, meta: dict, probes: dict, t0: float) -> dict:
    """The snapshot of a collection from its session rows (the `_` keys are dropped from them here) and its setup."""
    cfg, col, sess_list = ctx.cfg, ctx.inputs["collector"], rows
    pricing = cfg.get("pricing", {})
    now = core.util.now()

    # --- synthesize items seen only in measured context (built-ins, claude.ai connectors)
    by_kind_name = collections.defaultdict(list)
    for it in items:
        for alias in it.get("aliases") or [it["name"]]:
            by_kind_name[(it["kind"], alias)].append(it)
        if it["kind"] == "mcp":
            by_kind_name[("mcp", norm_mcp(it["name"]))].append(it)
            if it.get("plugin"):
                by_kind_name[("mcp", norm_mcp(f"plugin_{it['plugin'].split('@')[0]}_{it['name']}"))].append(it)
                by_kind_name[("mcp", norm_mcp(f"plugin:{it['plugin'].split('@')[0]}:{it['name']}"))].append(it)
    latest_ctx = next((s["_ctx"] for s in sess_list if s["_ctx"].get("skills") or s["_ctx"].get("tools")), {})
    for name, b in (latest_ctx.get("skills") or {}).items():
        if (name, ) and not by_kind_name.get(("skill", name)) and not by_kind_name.get(("command", name)):
            it = item("skill", name, "builtin", None, b, 0, removable=False, manage="bundled with Claude Code / account",
                      measured=True, source="measured")
            items.append(it)
            by_kind_name[("skill", name)].append(it)
    for name, b in (latest_ctx.get("agents") or {}).items():
        if not by_kind_name.get(("agent", name)):
            it = item("agent", name, "builtin", None, b, 0, removable=False, manage="built into Claude Code", measured=True,
                      source="measured")
            items.append(it)
            by_kind_name[("agent", name)].append(it)
    mcp_seen = set((latest_ctx.get("tools") or {}).keys()) | set((latest_ctx.get("deferred") or {}).keys()) | \
        set((latest_ctx.get("mcp_instructions") or {}).keys())
    mcp_seen.discard("builtin")
    for srv in sorted(mcp_seen):
        if not by_kind_name.get(("mcp", srv)):
            it = item("mcp", srv, "connector", None, 0, 0, removable=False,
                      manage="claude.ai → Settings → Connectors (or built into the host)", measured=True)
            items.append(it)
            by_kind_name[("mcp", srv)].append(it)

    # --- always-on bytes for MCP: measured (schemas loaded upfront + deferred names + instructions) or probe
    for it in items:
        if it["kind"] != "mcp":
            continue
        keys = {norm_mcp(it["name"])}
        if it.get("plugin"):
            keys |= {norm_mcp(f"plugin_{it['plugin'].split('@')[0]}_{it['name']}"),
                     norm_mcp(f"plugin:{it['plugin'].split('@')[0]}:{it['name']}")}
        upfront = sum((latest_ctx.get("tools") or {}).get(k, 0) for k in keys)
        deferred = sum((latest_ctx.get("deferred") or {}).get(k, 0) for k in keys)
        instr = sum((latest_ctx.get("mcp_instructions") or {}).get(k, 0) for k in keys)
        pr = probes.get(it.get("conf_hash") or "", {})
        if upfront or deferred or instr:
            it["bytes_always"] = upfront + deferred + instr
            it["source"] = "measured"
            it["detail"] = {"schema_upfront": tok(upfront), "deferred_names": tok(deferred), "instructions": tok(instr)}
        elif pr.get("schema_bytes") is not None:
            it["bytes_always"] = pr["schema_bytes"] + pr.get("instructions_bytes", 0)
            it["source"] = "probe"
            it["detail"] = {"tools": pr.get("tools"), "schema": tok(pr["schema_bytes"]),
                            "instructions": tok(pr.get("instructions_bytes", 0)),
                            "note": "upper bound; with tool search on, only names load until used"}
        elif pr.get("error"):
            it["source"] = "probe-failed"
            it["detail"] = {"error": pr["error"]}
        else:
            it["source"] = "unknown"
        if it.get("enabled") is False:
            it["bytes_always"] = 0
        it["tokens_always"] = tok(it["bytes_always"])
        it.pop("_conf", None)
    for it in items:
        if it["kind"] in ("skill", "agent", "command") and it["scope"] != "builtin":
            listing = (latest_ctx.get("agents") if it["kind"] == "agent" else latest_ctx.get("skills")) or {}
            m = next((listing[a] for a in it.get("aliases") or [it["name"]] if a in listing), None)
            if m:
                it["bytes_always"], it["tokens_always"], it["source"] = m, tok(m), "measured"
            else:
                it.setdefault("source", "estimate")
        it.setdefault("source", "file")
    for it in items:  # plugins roll up their children
        if it["kind"] == "plugin":
            kids = [x for x in items if x["id"] in set(it.get("children") or [])]
            it["bytes_always"] = sum(k["bytes_always"] for k in kids) if it.get("enabled") else 0
            it["tokens_always"] = tok(it["bytes_always"])
            it["source"] = "rollup"

    # --- usage join
    def usage_keys(it):
        n = it["name"]
        short = n.split(":", 1)[1] if ":" in n else n
        k = it["kind"]
        if k in ("skill", "command"):
            names = set(it.get("aliases") or [n])
            if it.get("plugin"):
                names.add(short)
            return [f"{c}:{x}" for x in sorted(names) for c in ("skill", "slash")]
        if k == "agent":
            return [f"agent:{n}"]
        if k == "mcp":
            ks = [f"mcp:{norm_mcp(n)}"]
            if it.get("plugin"):
                p = it["plugin"].split("@")[0]
                ks += [f"mcp:{norm_mcp(f'plugin_{p}_{n}')}", f"mcp:{norm_mcp(f'plugin:{p}:{n}')}"]
            return ks
        return []

    key_to_sessions = collections.defaultdict(list)
    for s in sess_list:
        for cat, field in (("skill", "skills"), ("slash", "slash"), ("agent", "agents"), ("mcp", "mcp")):
            for name, n in s[field].items():
                key_to_sessions[f"{cat}:{name}"].append((s, n))
    for it in items:
        calls, sids, projs, last, tokens = 0, set(), set(), None, 0
        for key in filter(None, usage_keys(it)):
            for s, n in key_to_sessions.get(key, []):
                if s["id"] in sids and key.startswith("slash:"):
                    continue
                calls += n
                sids.add(s["id"])
                projs.add(s["project"])
                last = max(last or "", s["last_ts"] or "")
                if it["kind"] == "agent":
                    tokens += s["agent_tokens"].get(it["name"], 0)
        it["usage"] = {"calls": calls, "sessions": len(sids), "projects": sorted(projs), "last_used": last or None,
                       "days_since_used": (now - parse_ts(last)).days if last and parse_ts(last) else None,
                       "agent_tokens": tokens or None}
    for it in items:
        if it["kind"] == "plugin":
            kids = [x for x in items if x["id"] in set(it.get("children") or [])]
            last = max([k["usage"]["last_used"] or "" for k in kids] or [""]) or None
            it["usage"] = {"calls": sum(k["usage"]["calls"] for k in kids),
                           "sessions": len({p for k in kids for p in k["usage"]["projects"]}),
                           "projects": sorted({p for k in kids for p in k["usage"]["projects"]}),
                           "last_used": last, "days_since_used": (now - parse_ts(last)).days if last else None}

    # --- duplicates (same kind+short name in several scopes)
    dup = collections.defaultdict(list)
    for it in items:
        if it["kind"] in ("skill", "agent", "command", "mcp") and it["scope"] in ("user", "project", "local"):
            dup[(it["kind"], it["name"].split(":")[-1].lower())].append(it["id"])
    duplicates = [{"kind": k[0], "name": k[1], "items": v} for k, v in dup.items() if len(v) > 1]

    # --- projects
    proj_by_path = {p["path"]: p for p in projects}
    for s in sess_list:
        if s["project"] not in proj_by_path:
            proj_by_path[s["project"]] = {"path": s["project"], "abs": None, "items": [], "ancestor_claude_md": [],
                                          "missing": True}
    user_always = sum(it["bytes_always"] for it in items if it["scope"] in ("user", "account", "builtin", "connector")
                      and it["kind"] != "plugin" and (it.get("enabled", True)))
    items_by_id = {it["id"]: it for it in items}
    for p in proj_by_path.values():
        ps = [s for s in sess_list if s["project"] == p["path"]]
        own = sum(items_by_id[i]["bytes_always"] for i in p["items"] if i in items_by_id and items_by_id[i]["kind"] != "plugin")
        bl = [s["baseline_tokens"] for s in ps if s["baseline_tokens"] and interactive(s)][:20]
        p.update({
            "sessions": len(ps), "last_active": ps[0]["last_ts"] if ps else None,
            "tokens": sum(s["tokens"]["total"] for s in ps), "cost": round(sum(s["cost"] for s in ps), 2),
            "own_always_tokens": tok(own), "est_always_tokens": tok(own + user_always),
            "baseline_median": sorted(bl)[len(bl) // 2] if bl else None,
            "counts": dict(collections.Counter(items_by_id[i]["kind"] for i in p["items"] if i in items_by_id)),
        })
    projects_out = sorted(proj_by_path.values(), key=lambda p: p.get("last_active") or "", reverse=True)

    # --- time series (hourly for series_days) and totals
    cutoff = (now - dt.timedelta(days=int(cfg.get("series_days", 30)))).strftime("%Y-%m-%dT%H")
    hourly = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0.0])
    for s in sess_list:
        for hour, per in s["_hourly"].items():
            if hour < cutoff:
                continue
            h = hourly[hour]
            for model, row in per.items():
                for i in range(5):
                    h[i] += row[i]
                h[5] += cost_of(model, row, pricing)
    series = [{"t": h + ":00Z", "input": v[0], "cache_write": v[1] + v[2], "cache_read": v[3], "output": v[4],
               "cost": round(v[5], 4)} for h, v in sorted(hourly.items())]

    def window(days):
        start = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H")
        rows = [r for r in series if r["t"][:13] >= start]
        return {"tokens": sum(r["input"] + r["cache_write"] + r["cache_read"] + r["output"] for r in rows),
                "cost": round(sum(r["cost"] for r in rows), 2),
                "sessions": sum(1 for s in sess_list if (s["last_ts"] or "")[:13] >= start)}

    recent_bl = [s["baseline_tokens"] for s in sess_list if s["baseline_tokens"] and interactive(s)][:30]
    by_kind = collections.defaultdict(lambda: {"count": 0, "tokens_always": 0, "unused": 0})
    for it in items:
        if it["kind"] == "plugin":
            continue
        k = by_kind[it["kind"]]
        k["count"] += 1
        if it.get("enabled", True) and it["scope"] not in ("project", "local"):
            k["tokens_always"] += it["tokens_always"]
        if not it["usage"]["calls"] and it["kind"] in ("skill", "agent", "command", "mcp"):
            k["unused"] += 1
    active_cut = iso(now - dt.timedelta(seconds=max(300, 3 * parse_duration(cfg.get("interval", "1m")))))

    month = iso(now - dt.timedelta(days=30))
    lost = [x for s in sess_list for x in s["_losses"] if x["ts"] >= month]
    cache_loss = {"ttl_minutes": CACHE_TTL_SECONDS // 60, "days": 30, "count": len(lost),
                  "sessions": len({x["session"] for x in lost}), "tokens": sum(x["tokens"] for x in lost),
                  "extra_cost": round(sum(x["extra_cost"] for x in lost), 2),
                  "worst": sorted(lost, key=lambda x: -x["extra_cost"])[:8]}

    reels = col["reels"]
    for rp in (reels or {}).get("projects", []):
        same = [p for p in projects_out if rp.get("folder") and not p.get("worktree_of")
                and p["path"].rsplit("/", 1)[-1] == rp["folder"]]
        if same:  # backups and archives can share the folder name; ~/code is the home of real projects
            best = max(same, key=lambda p: (p["path"] == f"~/code/{rp['folder']}", p["sessions"]))
            best["reels"] = {"strong": rp["strong"], "total": rp["total"]}
    for s in sess_list:
        s.pop("_hourly", None)
        s.pop("_ctx", None)
        s.pop("_losses", None)
    snap = {
        "schema": 1, "collector_version": col["version"], "generated_at": iso(now), "host": socket.gethostname(),
        "reels": reels, "cache_loss": cache_loss,
        "interval": cfg.get("interval", "1m"), "claude_dir": short_path(ctx.claude_dir),
        "totals": {
            "today": window(1), "week": window(7), "month": window(30),
            "baseline_median": sorted(recent_bl)[len(recent_bl) // 2] if recent_bl else None,
            "baseline_latest": recent_bl[0] if recent_bl else None,
            "global_always_tokens_est": tok(user_always),
            "by_kind": dict(by_kind), "sessions": len(sess_list),
            "projects": sum(1 for p in projects_out if not p.get("worktree_of")),
            "worktrees": sum(1 for p in projects_out if p.get("worktree_of")),
            "active_sessions": sum(1 for s in sess_list if (s["last_ts"] or "") >= active_cut),
        },
        "latest_context": {
            "system": tok(latest_ctx.get("system", 0)),
            "tools_builtin": tok((latest_ctx.get("tools") or {}).get("builtin", 0)),
            "tools_mcp": tok(sum(v for k, v in (latest_ctx.get("tools") or {}).items() if k != "builtin")),
            "skills": tok(sum((latest_ctx.get("skills") or {}).values())),
            "agents": tok(sum((latest_ctx.get("agents") or {}).values())),
            "mcp_instructions": tok(sum((latest_ctx.get("mcp_instructions") or {}).values())),
            "deferred": tok(sum((latest_ctx.get("deferred") or {}).values())),
        } if latest_ctx else None,
        "items": sorted(items, key=lambda x: -x["bytes_always"]),
        "projects": projects_out,
        "project_notes": col["project_notes"],
        "sessions": sess_list[: int(cfg.get("max_sessions", 400))],
        "series": series,
        "duplicates": duplicates,
        "meta": {**{k: v for k, v in meta.items() if k != "settings"}, "elapsed_ms": int((time.time() - t0) * 1000),
                 "transcript_files": n_files, "pricing": pricing},
    }
    return snap


def append_history(home, snap: dict):
    t = snap["totals"]
    row = {"t": snap["generated_at"], "global_always": t["global_always_tokens_est"], "baseline": t["baseline_latest"],
           "baseline_median": t["baseline_median"], "items": {k: v["count"] for k, v in t["by_kind"].items()},
           "always_by_kind": {k: v["tokens_always"] for k, v in t["by_kind"].items()},
           "today_tokens": t["today"]["tokens"], "today_cost": t["today"]["cost"]}
    path = home / "history.jsonl"
    last = None
    if path.exists():
        with open(path, "rb") as f:
            try:
                f.seek(-4096, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().splitlines()
            last = json.loads(tail[-1]) if tail else None
    sig = lambda r: json.dumps({k: v for k, v in (r or {}).items() if k not in ("t", "today_tokens", "today_cost")}, sort_keys=True)
    hour_changed = not last or last["t"][:13] != row["t"][:13]
    if sig(last) != sig(row) or hour_changed:
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")


def load_history(home, limit=2000) -> list:
    path = home / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows

