"""The use of Claude: what each transcript line adds to its file's state, and the session rows made from the files."""
from __future__ import annotations

import collections
import datetime as dt
import json
import re

from core import transcripts
from core.util import short_path

BYTES_PER_TOKEN = 3.7  # rough estimate for prose/JSON; measured baselines use real API token counts


def parse_ts(s) -> dt.datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def tok(n_bytes: int) -> int:
    return int(round((n_bytes or 0) / BYTES_PER_TOKEN))


def norm_mcp(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name or "")


def price_for(model: str, pricing: dict):
    best = None
    for prefix in pricing:
        if (model or "").startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return pricing.get(best) if best else None


def rebuild_extra(model: str, w5: int, w1: int, pricing: dict) -> float:
    """What rewriting these tokens cost above reading them from the cache."""
    written = cost_of(model, [0, w5, w1, 0, 0], pricing)
    return max(0.0, written - cost_of(model, [0, 0, 0, w5 + w1, 0], pricing))


def cost_of(model: str, t: list, pricing: dict) -> float:
    """t = [input, cache_write_5m, cache_write_1h, cache_read, output]"""
    p = price_for(model, pricing)
    if not p:
        return 0.0
    i, w5, w1, r, o = t
    return (i * p["in"] + w5 * p["in"] * 1.25 + w1 * p["in"] * 2.0
            + r * p.get("cache_read", p["in"] * 0.1) + o * p["out"]) / 1e6


CMD_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
LISTING_LINE = re.compile(r"^- (\S+?)(?::\s|:$| \(|$)")
# Measured on real transcripts: turns under an hour apart keep the cache, past an hour 84% rebuild it.
CACHE_TTL_SECONDS = 3600
CACHE_LOSS_MIN_CONTEXT = 20000


def fresh() -> dict:
    """The usage keys of a file's state, after the head facts the transcript helper keeps."""
    return {"models": {}, "hourly": {}, "baseline": None, "calls": {}, "mcp_tools": {}, "agent_tokens": {},
            "context": {}, "recent": {}, "pending": {}, "turns": 0, "prompts": 0, "last_turn": None, "cache_losses": []}


def _bump(d: dict, k: str, n: int = 1):
    d[k] = d.get(k, 0) + n


def _parse_listing(lines) -> dict:
    out, cur = {}, None
    for line in lines:
        m = LISTING_LINE.match(line)
        if m:
            cur = m.group(1)
            out[cur] = out.get(cur, 0) + len(line.encode()) + 1
        elif cur:
            out[cur] += len(line.encode()) + 1
    return out


def _attachment(st: dict, a: dict):
    t = a.get("type")
    ctx = st["context"]
    if t == "skill_listing" and isinstance(a.get("content"), str):
        listing = _parse_listing(a["content"].splitlines())
        ctx["skills"] = listing if a.get("isInitial") else {**ctx.get("skills", {}), **listing}
    elif t == "agent_listing_delta":
        lines = a.get("addedLines") or []
        lines = lines if isinstance(lines, list) else str(lines).splitlines()
        ctx.setdefault("agents", {}).update(_parse_listing(lines))
        if a.get("builtInTypes"):
            ctx["agents_builtin"] = sorted(set(ctx.get("agents_builtin", [])) | set(a["builtInTypes"]))
    elif t == "mcp_instructions_delta":
        names, blocks = a.get("addedNames") or [], a.get("addedBlocks") or []
        mi = ctx.setdefault("mcp_instructions", {})
        for n, b in zip(names, blocks):
            mi[norm_mcp(n)] = len(str(b).encode())
    elif t == "deferred_tools_delta":
        names = ctx.setdefault("deferred_names", {})  # by name: a resumed session announces the same tools again
        names.update((n, len(n) + 1) for n in (a.get("addedNames") or []) + (a.get("readdedNames") or []))
        for n in a.get("removedNames") or []:
            names.pop(n, None)
        dfr = ctx["deferred"] = {}
        for name, size in names.items():
            parts = name.split("__")
            key = norm_mcp(parts[1]) if name.startswith("mcp__") and len(parts) > 2 else "builtin"
            dfr[key] = dfr.get(key, 0) + size
        for k in ("pendingMcpServers", "needsAuthMcpServers"):
            if a.get(k):
                ctx[k] = sorted(set(a[k]))
    elif t == "prompt_snapshot" and isinstance(a.get("tools"), list):
        tools = {}
        for x in a["tools"]:
            if not isinstance(x, dict):
                continue
            name = x.get("name", "")
            srv = x.get("server")
            if not srv and name.startswith("mcp__"):
                srv = name.split("__")[1]
            key = norm_mcp(srv) if srv else "builtin"
            tools[key] = tools.get(key, 0) + len(json.dumps(x).encode())
        sysp = a.get("systemPrompt")
        sys_bytes = len(json.dumps(sysp).encode()) if sysp is not None else 0
        if sum(tools.values()) >= sum(ctx.get("tools", {}).values()):
            ctx["tools"] = tools
            ctx["system"] = sys_bytes


def _cache_loss(st: dict, ts, model: str, row: list):
    """Record a turn that rebuilt the cache after idling past its TTL: the previous context came back as writes."""
    last, st["last_turn"] = st["last_turn"], [ts, row[1] + row[2] + row[3]]
    if not last or last[1] < CACHE_LOSS_MIN_CONTEXT or row[1] + row[2] <= last[1] / 2 or row[3] >= last[1] / 2:
        return
    a, b = parse_ts(last[0]), parse_ts(ts)
    if a and b and (b - a).total_seconds() >= CACHE_TTL_SECONDS:
        st["cache_losses"].append([ts, round((b - a).total_seconds() / 60), model, row[1], row[2]])


def line(st: dict, d: dict, ids: dict, owner: int):
    t = d.get("type")
    ts = d.get("timestamp")
    if t == "assistant":
        m = d.get("message") or {}
        mid = m.get("id") or d.get("requestId") or d.get("uuid")
        u = m.get("usage") or {}
        if mid and u and mid in st["recent"]:
            # Streamed copies of one call share an id; only the last carries the final output count.
            model, hour, out = st["recent"][mid]
            extra = (u.get("output_tokens") or 0) - out
            if extra > 0:
                st["recent"][mid][2] += extra
                st["models"][model][4] += extra
                if hour:
                    st["hourly"][hour][model][4] += extra
        elif mid and u and mid[-16:] in ids:
            return  # resumed and forked sessions replay old calls, in this file or another one
        elif mid and u:
            cc = u.get("cache_creation") or {}
            cw_total = u.get("cache_creation_input_tokens") or 0
            w1 = cc.get("ephemeral_1h_input_tokens") or 0
            w5 = cc.get("ephemeral_5m_input_tokens") if cc else cw_total
            w5 = w5 or max(0, cw_total - w1)
            row = [u.get("input_tokens") or 0, w5, w1, u.get("cache_read_input_tokens") or 0,
                   u.get("output_tokens") or 0]
            model = m.get("model") or "unknown"
            if model == "<synthetic>":
                return
            ids[mid[-16:]] = owner
            st["recent"][mid] = [model, (ts or "")[:13], row[4]]
            if len(st["recent"]) > 60:
                del st["recent"][next(iter(st["recent"]))]
            mm = st["models"].setdefault(model, [0, 0, 0, 0, 0, 0])
            for i in range(5):
                mm[i] += row[i]
            mm[5] += 1
            hour = (ts or "")[:13]
            if hour:
                hm = st["hourly"].setdefault(hour, {}).setdefault(model, [0, 0, 0, 0, 0])
                for i in range(5):
                    hm[i] += row[i]
            st["turns"] += 1
            if ts and not d.get("isSidechain"):
                _cache_loss(st, ts, model, row)
            if st["baseline"] is None and not d.get("isSidechain"):
                st["baseline"] = {"tokens": row[0] + row[1] + row[2] + row[3], "model": model, "ts": ts}
        for b in m.get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name, inp = b.get("name") or "", b.get("input") or {}
            if name == "Skill":
                _bump(st["calls"], "skill:" + str(inp.get("skill") or inp.get("name") or "?").lstrip("/"))
            elif name == "SlashCommand":
                _bump(st["calls"], "slash:" + str(inp.get("command") or "?").lstrip("/").split()[0])
            elif name in ("Agent", "Task"):
                typ = str(inp.get("subagent_type") or "general-purpose")
                _bump(st["calls"], "agent:" + typ)
                if b.get("id"):
                    st["pending"][b["id"]] = typ
                    if len(st["pending"]) > 200:
                        st["pending"].pop(next(iter(st["pending"])))
            elif name.startswith("mcp__"):
                parts = name.split("__")
                srv = norm_mcp(parts[1]) if len(parts) > 2 else name
                _bump(st["calls"], "mcp:" + srv)
                _bump(st["mcp_tools"].setdefault(srv, {}), "__".join(parts[2:]) or "?")
            _bump(st["calls"], "tool:" + name)
    elif t == "user":
        m = d.get("message") or {}
        c = m.get("content")
        texts = [c] if isinstance(c, str) else [x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") == "text"]
        for txt in texts:
            for cmd in CMD_RE.findall(txt or ""):
                _bump(st["calls"], "slash:" + cmd)
        if d.get("origin", {}).get("kind") == "human" or (isinstance(c, str) and not d.get("isMeta")):
            st["prompts"] += 1
        tur = d.get("toolUseResult")
        if isinstance(c, list) and isinstance(tur, dict) and (tur.get("totalTokens") or tur.get("agentId")):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in st["pending"]:
                    typ = st["pending"].pop(x["tool_use_id"])
                    _bump(st["agent_tokens"], typ, int(tur.get("totalTokens") or 0))
    elif t == "attachment" and isinstance(d.get("attachment"), dict):
        _attachment(st, d["attachment"])


LINES = transcripts.Consumer(fresh=fresh, line=line)


def sessions(files: dict, pricing: dict) -> list:
    """The session rows of the snapshot, newest first, each still holding `_hourly`, `_ctx` and `_losses`."""
    # --- merge transcript files into sessions
    sessions = {}
    for sid, one in transcripts.sessions(files).items():
        s = sessions[sid] = {"id": sid, "project": one["cwd"], "first_ts": one["first_ts"], "last_ts": one["last_ts"],
                             "models": {}, "calls": collections.Counter(), "mcp_tools": {},
                             "agent_tokens": collections.Counter(), "baseline": None, "context": {},
                             "subagent_files": one["subagent_files"], "entrypoint": one["entrypoint"],
                             "version": one["version"], "branch": one["branch"], "turns": 0, "prompts": 0, "hourly": {},
                             "cache_losses": []}
        for st in one["files"]:
            if not st["sidechain"]:
                s["baseline"] = s["baseline"] or st.get("baseline")
                if st["context"]:
                    s["context"] = st["context"]
                s["prompts"] += st["prompts"]
            for model, row in st["models"].items():
                mm = s["models"].setdefault(model, [0] * 6)
                for i in range(6):
                    mm[i] += row[i]
            for hour, per in st["hourly"].items():
                hh = s["hourly"].setdefault(hour, {})
                for model, row in per.items():
                    hm = hh.setdefault(model, [0] * 5)
                    for i in range(5):
                        hm[i] += row[i]
            s["calls"].update(st["calls"])
            s["agent_tokens"].update(st["agent_tokens"])
            for srv, tools in st["mcp_tools"].items():
                c = s["mcp_tools"].setdefault(srv, collections.Counter())
                c.update(tools)
            s["turns"] += st["turns"]
            s["cache_losses"] += st["cache_losses"]

    # --- per-session totals
    sess_list = []
    for s in sessions.values():
        tokens = [0] * 5
        cost = 0.0
        for model, row in s["models"].items():
            for i in range(5):
                tokens[i] += row[i]
            cost += cost_of(model, row[:5], pricing)
        ctx = s["context"]
        measured = None
        if ctx:
            measured = {
                "system": tok(ctx.get("system", 0)),
                "tools_builtin": tok((ctx.get("tools") or {}).get("builtin", 0)),
                "tools_mcp": {k: tok(v) for k, v in (ctx.get("tools") or {}).items() if k != "builtin"},
                "skills": tok(sum((ctx.get("skills") or {}).values())),
                "agents": tok(sum((ctx.get("agents") or {}).values())),
                "mcp_instructions": tok(sum((ctx.get("mcp_instructions") or {}).values())),
                "deferred": tok(sum((ctx.get("deferred") or {}).values())),
                "skill_count": len(ctx.get("skills") or {}),
                "pending_mcp": ctx.get("pendingMcpServers") or [], "needs_auth_mcp": ctx.get("needsAuthMcpServers") or [],
            }
        losses = [{"session": s["id"], "project": short_path(s["project"]) if s["project"] else "(unknown)", "ts": ts,
                   "idle_min": idle, "model": model, "tokens": w5 + w1,
                   "extra_cost": round(rebuild_extra(model, w5, w1, pricing), 4)}
                  for ts, idle, model, w5, w1 in s["cache_losses"]]
        sess_list.append({
            "id": s["id"], "project": short_path(s["project"]) if s["project"] else "(unknown)",
            "first_ts": s["first_ts"], "last_ts": s["last_ts"], "entrypoint": s["entrypoint"], "version": s["version"],
            "branch": s["branch"], "models": sorted(s["models"]), "turns": s["turns"], "prompts": s["prompts"],
            "subagent_files": s["subagent_files"],
            "tokens": {"input": tokens[0], "cache_write": tokens[1] + tokens[2], "cache_read": tokens[3],
                       "output": tokens[4], "total": sum(tokens)},
            "cost": round(cost, 4),
            "baseline_tokens": (s["baseline"] or {}).get("tokens"),
            "cache_lost": {"count": len(losses), "tokens": sum(x["tokens"] for x in losses),
                           "extra_cost": round(sum(x["extra_cost"] for x in losses), 4)} if losses else None,
            "_losses": losses,
            "measured_context": measured,
            "skills": {k[6:]: v for k, v in s["calls"].items() if k.startswith("skill:")},
            "slash": {k[6:]: v for k, v in s["calls"].items() if k.startswith("slash:")},
            "agents": {k[6:]: v for k, v in s["calls"].items() if k.startswith("agent:")},
            "agent_tokens": dict(s["agent_tokens"]),
            "mcp": {k[4:]: v for k, v in s["calls"].items() if k.startswith("mcp:")},
            "mcp_tools": {k: dict(v) for k, v in s["mcp_tools"].items()},
            "tools": {k[5:]: v for k, v in s["calls"].items() if k.startswith("tool:")},
            "_hourly": s["hourly"], "_ctx": ctx,
        })
    sess_list.sort(key=lambda x: x["last_ts"] or "", reverse=True)
    return sess_list
