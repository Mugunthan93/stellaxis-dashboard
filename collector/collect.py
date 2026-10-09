#!/usr/bin/env python3
"""Stellaxis collector: inventory + usage of Claude Code skills, agents, plugins and MCP servers.

Stdlib only. Reads ~/.claude, ~/.claude.json, every folder with Claude sessions or .claude files,
and writes ~/.stellaxis/snapshot.json (+ history.jsonl). Transcripts are parsed incrementally.

  python3 collect.py run            # one pass, print summary
  python3 collect.py serve          # loop every `interval` + dashboard on http://127.0.0.1:7777
  python3 collect.py summary        # print summary of the last snapshot
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

VERSION = "0.1.0"
HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude")).expanduser()
CLAUDE_JSON = (CLAUDE_DIR / ".claude.json") if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json"
STX_HOME = Path(os.environ.get("STELLAXIS_HOME", HOME / ".stellaxis")).expanduser()
REPO_DIR = Path(__file__).resolve().parent.parent
BYTES_PER_TOKEN = 3.7  # rough estimate for prose/JSON; measured baselines use real API token counts

DEFAULT_CONFIG = {
    "interval": "1m",            # collection interval: "30s", "1m", "15m", "1h"
    "port": 7777,
    "scan_roots": ["~"],          # walked for folders containing .claude/ or CLAUDE.md
    "scan_depth": 6,
    "rescan_every": "1h",         # filesystem walk is cached this long
    "exclude_dirs": ["node_modules", ".git", "Library", ".Trash", ".cache", ".npm", ".pnpm-store",
                     "venv", ".venv", "__pycache__", "dist", "build", "Pods", "DerivedData",
                     ".gradle", ".m2", "go", ".cargo", ".rustup", "Applications", "Movies",
                     "Music", "Pictures", ".stellaxis", ".docker", ".local", ".vscode", ".cursor"],
    "probe_mcp": True,            # spawn stdio MCP servers once a day to measure tool-schema size
    "probe_every": "24h",
    "probe_timeout": 20,
    "max_sessions": 400,          # sessions kept in snapshot (newest first)
    "series_days": 30,
    # USD per million tokens. cache_write_5m = 1.25x input, cache_write_1h = 2x input.
    # Longest matching prefix of the model id wins. Notional on a Max plan.
    "pricing": {
        "claude-fable-5-1": {"in": 10.0, "out": 50.0, "cache_read": 0.25},
        "claude-fable-5": {"in": 10.0, "out": 50.0, "cache_read": 1.0},
        "claude-mythos": {"in": 10.0, "out": 50.0, "cache_read": 0.25},
        "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20},
        "claude-opus-5": {"in": 5.0, "out": 25.0, "cache_read": 0.50},
        "claude-opus-4": {"in": 5.0, "out": 25.0, "cache_read": 0.50},
        "claude-sonnet-5": {"in": 2.0, "out": 10.0, "cache_read": 0.20},
        "claude-sonnet-4": {"in": 3.0, "out": 15.0, "cache_read": 0.30},
        "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01},
        "claude-haiku-4": {"in": 1.0, "out": 5.0, "cache_read": 0.10},
    },
}


# ---------------------------------------------------------------- utilities

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def parse_ts(s) -> dt.datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_duration(s, default=60) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(s or ""))
    if not m:
        return default
    return int(float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)])


def tok(n_bytes: int) -> int:
    return int(round((n_bytes or 0) / BYTES_PER_TOKEN))


def read_text(p: Path, limit: int = 2_000_000) -> str:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def read_json(p: Path, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(p: Path, data, indent=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, default=list)
    os.replace(tmp, p)


def short_path(p) -> str:
    s = str(p)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def norm_mcp(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name or "")


_status_at = 0.0


def status(phase: str, force: bool = True, **detail):
    """Write ~/.stellaxis/status.json so a stalled run shows where it is stuck (e.g. a macOS
    folder-permission prompt blocking a read in ~/Documents)."""
    global _status_at
    if not force and time.time() - _status_at < 1:
        return
    _status_at = time.time()
    try:
        write_json(STX_HOME / "status.json", {"phase": phase, "at": iso(now_utc()), "pid": os.getpid(), **detail})
    except OSError:
        pass


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = read_json(STX_HOME / "config.json", {}) or {}
    for k, v in user.items():
        if k == "pricing" and isinstance(v, dict):
            cfg["pricing"].update(v)
        else:
            cfg[k] = v
    return cfg


def price_for(model: str, pricing: dict):
    best = None
    for prefix in pricing:
        if (model or "").startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return pricing.get(best) if best else None


def cost_of(model: str, t: list, pricing: dict) -> float:
    """t = [input, cache_write_5m, cache_write_1h, cache_read, output]"""
    p = price_for(model, pricing)
    if not p:
        return 0.0
    i, w5, w1, r, o = t
    return (i * p["in"] + w5 * p["in"] * 1.25 + w1 * p["in"] * 2.0
            + r * p.get("cache_read", p["in"] * 0.1) + o * p["out"]) / 1e6


# ---------------------------------------------------------------- frontmatter

def frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---\s*$", text[3:], re.M)
    if not end:
        return {}, text
    raw, body = text[3:3 + end.start()], text[3 + end.end():]
    meta, key, block = {}, None, None
    for line in raw.splitlines():
        if block is not None and (line.startswith((" ", "\t")) or not line.strip()):
            meta[key] = (meta[key] + ("\n" if block == "|" else " ") + line.strip()).strip()
            continue
        block = None
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in ("|", ">", "|-", ">-"):
            block, meta[key] = val[0], ""
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta[key] = val
    return meta, body


# ---------------------------------------------------------------- usage (incremental transcript parsing)

CMD_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
LISTING_LINE = re.compile(r"^- (\S+?)(?::\s|:$| \(|$)")


def new_file_state(path: str) -> dict:
    return {"path": path, "offset": 0, "size": 0, "session": None, "cwd": None, "first_ts": None,
            "last_ts": None, "entrypoint": None, "version": None, "branch": None,
            "sidechain": "/subagents/" in path, "models": {}, "hourly": {}, "baseline": None,
            "calls": {}, "mcp_tools": {}, "agent_tokens": {}, "context": {}, "recent_ids": [],
            "pending": {}, "turns": 0, "prompts": 0}


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
        dfr = ctx.setdefault("deferred", {})
        for name in a.get("addedNames") or []:
            parts = name.split("__")
            key = norm_mcp(parts[1]) if name.startswith("mcp__") and len(parts) > 2 else "builtin"
            dfr[key] = dfr.get(key, 0) + len(name) + 1
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


def _line(st: dict, d: dict):
    t = d.get("type")
    ts = d.get("timestamp")
    if ts:
        st["first_ts"] = st["first_ts"] or ts
        st["last_ts"] = ts
    st["session"] = st["session"] or d.get("sessionId")
    st["cwd"] = st["cwd"] or d.get("cwd")
    st["entrypoint"] = st["entrypoint"] or d.get("entrypoint")
    st["version"] = d.get("version") or st["version"]
    st["branch"] = d.get("gitBranch") or st["branch"]
    if t == "assistant":
        m = d.get("message") or {}
        mid = m.get("id") or d.get("requestId") or d.get("uuid")
        u = m.get("usage") or {}
        if mid and mid not in st["recent_ids"] and u:
            st["recent_ids"] = (st["recent_ids"] + [mid])[-60:]
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


def parse_transcripts(cache: dict) -> dict:
    files = cache.setdefault("files", {})
    seen = set()
    proj_root = CLAUDE_DIR / "projects"
    for p in proj_root.rglob("*.jsonl") if proj_root.exists() else []:
        key = str(p)
        seen.add(key)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        st = files.get(key)
        if st is None or size < st["size"] or st.get("v") != VERSION:
            st = files[key] = new_file_state(key)
            st["v"] = VERSION
        if size == st["offset"]:
            continue
        try:
            with open(p, "rb") as f:
                f.seek(st["offset"])
                chunk = f.read(size - st["offset"])
        except OSError:
            continue
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            continue
        for raw in chunk[:last_nl].splitlines():
            try:
                _line(st, json.loads(raw))
            except (ValueError, TypeError, AttributeError):
                continue
        st["offset"] += last_nl + 1
        st["size"] = size
    for k in list(files):
        if k not in seen:
            del files[k]
    return files


# ---------------------------------------------------------------- inventory

def item(kind, name, scope, path=None, always=0, on_use=0, **kw) -> dict:
    scope_key = scope if not kw.get("scope_path") else f"{scope}:{kw['scope_path']}"
    if kw.get("plugin"):
        scope_key += f":{kw['plugin']}"
    d = {"id": f"{kind}:{scope_key}:{name}", "kind": kind, "name": name, "scope": scope,
         "path": short_path(path) if path else None, "bytes_always": always, "bytes_on_use": on_use,
         "tokens_always": tok(always), "tokens_on_use": tok(on_use), "removable": True}
    d.update(kw)
    return d


def resolve_imports(path: Path, depth=0, seen=None) -> tuple[int, list]:
    """Bytes of a CLAUDE.md plus its @imports (recursive)."""
    seen = seen or set()
    rp = path.resolve()
    if rp in seen or depth > 5 or not path.is_file():
        return 0, []
    seen.add(rp)
    text = read_text(path)
    total, imports = len(text.encode()), []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        for m in re.finditer(r"(?:^|\s)@((?:~|\.{1,2})?/?[\w./-]+\.\w+)", line):
            ref = m.group(1)
            target = Path(os.path.expanduser(ref)) if ref.startswith("~") else (path.parent / ref)
            if target.is_file():
                b, sub = resolve_imports(target, depth + 1, seen)
                total += b
                imports.append(short_path(target))
                imports += sub
    return total, imports


def scan_skill_dir(base: Path, scope: str, scope_path=None, plugin=None, prefix="") -> list:
    out = []
    if not base.is_dir():
        return out
    for sk in sorted(base.iterdir()):
        f = sk / "SKILL.md"
        if not f.is_file():
            continue
        text = read_text(f)
        meta, body = frontmatter(text)
        name = prefix + (meta.get("name") or sk.name)
        desc = meta.get("description", "")
        listing = len(f"- {name}: {desc}".encode())
        if str(meta.get("disable-model-invocation", "")).lower() == "true":
            listing = 0
        aliases = sorted({name, prefix + sk.name} | ({"anthropic-skills:" + sk.name} if scope == "account" else set()))
        out.append(item("skill", name, scope, f, listing, len(text.encode()), scope_path=scope_path,
                        plugin=plugin, description=desc[:300], dir=short_path(sk), aliases=aliases))
    return out


def scan_md_dir(base: Path, kind: str, scope: str, scope_path=None, plugin=None, prefix="") -> list:
    out = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        text = read_text(f)
        meta, body = frontmatter(text)
        rel = f.relative_to(base).with_suffix("")
        name = prefix + (meta.get("name") or ":".join(rel.parts))
        desc = meta.get("description", "")
        if kind == "agent":
            tools = meta.get("tools")
            listing = len(f"- {name}: {desc} (Tools: {tools or '*'})".encode())
        else:
            listing = len(f"- {name}: {desc}".encode()) if desc else 0
        out.append(item(kind, name, scope, f, listing, len(text.encode()), scope_path=scope_path,
                        plugin=plugin, description=desc[:300], model=meta.get("model")))
    return out


def mcp_items(servers: dict, scope: str, path: Path, scope_path=None, plugin=None, disabled=()) -> list:
    out = []
    for name, conf in (servers or {}).items():
        if not isinstance(conf, dict):
            continue
        transport = conf.get("type") or ("stdio" if conf.get("command") else "http" if conf.get("url") else "?")
        target = conf.get("url") or " ".join([str(conf.get("command", ""))] + [str(a) for a in conf.get("args", [])][:4])
        out.append(item("mcp", name, scope, path, 0, 0, scope_path=scope_path, plugin=plugin,
                        transport=transport, target=target[:200], enabled=name not in disabled,
                        conf_hash=hashlib.sha1(json.dumps(conf, sort_keys=True).encode()).hexdigest()[:12],
                        _conf=conf))
    return out


def scan_plugin(root: Path, key: str, scope: str, enabled: bool, scope_path=None) -> list:
    manifest = read_json(root / ".claude-plugin" / "plugin.json", {}) or {}
    pname = manifest.get("name") or key.split("@")[0]
    pre = pname + ":"
    children = []
    children += scan_skill_dir(root / "skills", scope, scope_path, plugin=key, prefix=pre)
    children += scan_md_dir(root / "commands", "command", scope, scope_path, plugin=key, prefix=pre)
    children += scan_md_dir(root / "agents", "agent", scope, scope_path, plugin=key, prefix=pre)
    mcp = (read_json(root / ".mcp.json", {}) or {}).get("mcpServers") or {}
    if isinstance(manifest.get("mcpServers"), dict):
        mcp = {**mcp, **manifest["mcpServers"]}
    children += mcp_items(mcp, scope, root / ".mcp.json", scope_path, plugin=key)
    hooks = read_json(root / "hooks" / "hooks.json", {}) or {}
    for c in children:
        c["enabled"] = enabled and c.get("enabled", True)
    always = sum(c["bytes_always"] for c in children) if enabled else 0
    p = item("plugin", key, scope, root, always, 0, scope_path=scope_path, enabled=enabled,
             version=manifest.get("version"), description=(manifest.get("description") or "")[:300],
             children=[c["id"] for c in children], hooks=sorted((hooks.get("hooks") or {}).keys()))
    return [p] + children


def installed_plugins() -> list:
    """[(key, installPath, scope, projectPath)] from installed_plugins.json (v1 or v2)."""
    data = read_json(CLAUDE_DIR / "plugins" / "installed_plugins.json", {}) or {}
    out = []
    for key, val in (data.get("plugins") or {}).items():
        entries = val if isinstance(val, list) else [val]
        for e in entries:
            if isinstance(e, dict) and e.get("installPath"):
                out.append((key, Path(e["installPath"]).expanduser(), e.get("scope", "user"), e.get("projectPath")))
    return out


def worktree_of(P: Path):
    """Main repo of a git worktree (its .git is a file pointing into <repo>/.git/worktrees/<name>)."""
    g = P / ".git"
    if g.is_file():
        m = re.match(r"gitdir:\s*(.+)", read_text(g, 4096).strip())
        if m:
            gd = Path(m.group(1).strip())
            gd = gd if gd.is_absolute() else (P / gd).resolve()
            if gd.parent.name == "worktrees":
                return short_path(gd.parent.parent.parent)
    parts = P.parts
    if ".claude" in parts and "worktrees" in parts:
        i = parts.index(".claude")
        return short_path(Path(*parts[:i]))
    return None


def discover_projects(cfg: dict, cache: dict, transcript_cwds: set, claude_json: dict) -> list:
    roots = set(p for p in transcript_cwds if p)
    roots |= set((claude_json.get("projects") or {}).keys())
    walk = cache.get("walk") or {}
    if time.time() - walk.get("at", 0) > parse_duration(cfg["rescan_every"], 3600):
        found = set()
        excl = set(cfg["exclude_dirs"])
        for r in cfg["scan_roots"]:
            base = Path(os.path.expanduser(r))
            base_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).parts) - base_depth
                if depth <= 2:
                    status("scanning folders", force=False, path=short_path(dirpath))
                dirnames[:] = [d for d in dirnames if d not in excl and not (d.startswith(".") and d != ".claude")]
                if depth >= cfg["scan_depth"]:
                    dirnames[:] = [d for d in dirnames if d == ".claude"]
                if Path(dirpath) == HOME:
                    dirnames[:] = [d for d in dirnames if d != ".claude"]
                    continue
                if Path(dirpath).name == ".claude":
                    dirnames[:] = []
                    continue
                if ".claude" in dirnames or "CLAUDE.md" in filenames or ".mcp.json" in filenames:
                    found.add(dirpath)
        walk = cache["walk"] = {"at": time.time(), "dirs": sorted(found)}
    strong = set(roots)  # session cwds and ~/.claude.json projects always count
    walked = set(walk.get("dirs", []))
    for d in sorted(walked):
        # a folder with only a nested CLAUDE.md inside another project is that project's lazy-loaded file
        inside = any(d != r and d.startswith(r.rstrip(os.sep) + os.sep) for r in strong | walked)
        if not inside or (Path(d) / ".claude").is_dir():
            strong.add(d)
    return sorted(r for r in strong if Path(r).is_dir() and Path(r) != HOME and not str(r).startswith(str(CLAUDE_DIR)))


def inventory(cfg: dict, cache: dict, transcript_cwds: set) -> tuple[list, list, dict]:
    items = []
    cj = read_json(CLAUDE_JSON, {}) or {}
    settings = read_json(CLAUDE_DIR / "settings.json", {}) or {}
    settings_local = read_json(CLAUDE_DIR / "settings.local.json", {}) or {}
    enabled_plugins = {**(settings.get("enabledPlugins") or {}), **(settings_local.get("enabledPlugins") or {})}

    # user-level memory files
    for f in [CLAUDE_DIR / "CLAUDE.md"]:
        if f.is_file():
            b, imports = resolve_imports(f)
            items.append(item("claude_md", "~/.claude/CLAUDE.md", "user", f, b, 0, imports=imports))
    rules = CLAUDE_DIR / "rules"
    if rules.is_dir():
        for f in sorted(rules.rglob("*.md")):
            meta, _ = frontmatter(read_text(f))
            b = len(read_text(f).encode())
            cond = bool(meta.get("paths"))
            items.append(item("rule", f.relative_to(rules).as_posix(), "user", f, 0 if cond else b, b if cond else 0,
                              conditional=cond))
    items += scan_skill_dir(CLAUDE_DIR / "skills", "user")
    items += scan_md_dir(CLAUDE_DIR / "agents", "agent", "user")
    items += scan_md_dir(CLAUDE_DIR / "commands", "command", "user")
    items += mcp_items(cj.get("mcpServers"), "user", CLAUDE_JSON)

    # account-synced (claude.ai) skills and plugins — managed in claude.ai, not on disk
    for bucket in sorted((CLAUDE_DIR / "skills" / "synced").glob("*")) if (CLAUDE_DIR / "skills" / "synced").is_dir() else []:
        if bucket.is_dir() and not bucket.name.startswith("."):
            for it in scan_skill_dir(bucket, "account"):
                it.update(removable=False, manage="claude.ai → Settings → Capabilities → Skills")
                items.append(it)
    for bucket in sorted((CLAUDE_DIR / "plugins" / "synced").glob("*")) if (CLAUDE_DIR / "plugins" / "synced").is_dir() else []:
        if bucket.is_dir() and not bucket.name.startswith("."):
            for pdir in sorted(bucket.iterdir()):
                if (pdir / ".claude-plugin" / "plugin.json").is_file():
                    for it in scan_plugin(pdir, pdir.name, "account", True):
                        it.update(removable=False, manage="claude.ai → Settings → Plugins")
                        items.append(it)

    # installed plugins
    for key, ipath, pscope, ppath in installed_plugins():
        if not ipath.is_dir():
            continue
        enabled = bool(enabled_plugins.get(key, False))
        if pscope in ("project", "local") and ppath:
            ps = read_json(Path(ppath) / ".claude" / "settings.json", {}) or {}
            pl = read_json(Path(ppath) / ".claude" / "settings.local.json", {}) or {}
            enabled = bool({**(ps.get("enabledPlugins") or {}), **(pl.get("enabledPlugins") or {})}.get(key, enabled))
        items += scan_plugin(ipath, key, pscope if pscope != "user" else "user", enabled,
                             scope_path=short_path(ppath) if ppath and pscope != "user" else None)

    # projects
    projects = []
    for proj in discover_projects(cfg, cache, transcript_cwds, cj):
        status("reading project", force=False, path=short_path(proj))
        P = Path(proj)
        sp = short_path(P)
        wt = worktree_of(P)
        if wt:
            # a worktree's .claude/ is a checkout of the main repo's files; count its sessions, not its items
            projects.append({"path": sp, "abs": str(P), "items": [], "ancestor_claude_md": [], "has_git": True,
                             "worktree_of": wt})
            continue
        pitems = []
        for rel in ["CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"]:
            f = P / rel
            if f.is_file():
                b, imports = resolve_imports(f)
                pitems.append(item("claude_md", rel, "project", f, b, 0, scope_path=sp, imports=imports))
        pr = P / ".claude" / "rules"
        if pr.is_dir():
            for f in sorted(pr.rglob("*.md")):
                meta, _ = frontmatter(read_text(f))
                b = len(read_text(f).encode())
                cond = bool(meta.get("paths"))
                pitems.append(item("rule", f.relative_to(pr).as_posix(), "project", f, 0 if cond else b,
                                   b if cond else 0, scope_path=sp, conditional=cond))
        pitems += scan_skill_dir(P / ".claude" / "skills", "project", sp)
        pitems += scan_md_dir(P / ".claude" / "agents", "agent", "project", sp)
        pitems += scan_md_dir(P / ".claude" / "commands", "command", "project", sp)
        ps = read_json(P / ".claude" / "settings.json", {}) or {}
        pl = read_json(P / ".claude" / "settings.local.json", {}) or {}
        disabled = set(ps.get("disabledMcpjsonServers") or []) | set(pl.get("disabledMcpjsonServers") or [])
        pitems += mcp_items((read_json(P / ".mcp.json", {}) or {}).get("mcpServers"), "project", P / ".mcp.json", sp,
                            disabled=disabled)
        pj = (cj.get("projects") or {}).get(proj) or {}
        pitems += mcp_items(pj.get("mcpServers"), "local", CLAUDE_JSON, sp, disabled=set(pj.get("disabledMcpServers") or []))
        # ancestor CLAUDE.md files are loaded too
        ancestors = []
        for anc in P.parents:
            if anc == HOME or len(anc.parts) <= 1:
                break
            f = anc / "CLAUDE.md"
            if f.is_file():
                ancestors.append(short_path(f))
        # auto-memory
        enc = re.sub(r"[^A-Za-z0-9]", "-", str(P))
        mem = CLAUDE_DIR / "projects" / enc / "memory" / "MEMORY.md"
        if mem.is_file():
            pitems.append(item("memory", "MEMORY.md", "project", mem, min(len(read_text(mem).encode()), 25_000), 0,
                               scope_path=sp))
        nested = 0
        for dirpath, dirnames, filenames in os.walk(P):
            depth = len(Path(dirpath).parts) - len(P.parts)
            dirnames[:] = [d for d in dirnames if d not in cfg["exclude_dirs"] and not d.startswith(".")]
            if depth >= 4:
                dirnames[:] = []
            if depth > 0 and "CLAUDE.md" in filenames:
                f = Path(dirpath) / "CLAUDE.md"
                pitems.append(item("claude_md", str(f.relative_to(P)), "project", f, 0, len(read_text(f).encode()),
                                   scope_path=sp, conditional=True, note="loaded when Claude reads files in that folder"))
                nested += 1
        items += pitems
        projects.append({"path": sp, "abs": str(P), "items": [i["id"] for i in pitems], "ancestor_claude_md": ancestors,
                         "has_git": (P / ".git").exists()})
    return items, projects, {"settings": settings, "claude_json_projects": len(cj.get("projects") or {}),
                             "hooks": sorted((settings.get("hooks") or {}).keys()),
                             "enabled_plugins": enabled_plugins}


# ---------------------------------------------------------------- MCP schema probe (stdio only)

def probe_stdio(conf: dict, timeout: int) -> dict:
    cmd = [conf["command"]] + [str(a) for a in conf.get("args", [])]
    env = {**os.environ, **{k: str(v) for k, v in (conf.get("env") or {}).items()}}
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "stellaxis", "version": VERSION}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, cwd=conf.get("cwd") or None)
    except OSError as e:
        return {"error": str(e)[:200]}
    out = {"error": "timeout"}
    try:
        proc.stdin.write((json.dumps(msgs[0]) + "\n").encode())
        proc.stdin.flush()
        deadline = time.time() + timeout
        sent_list = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("id") == 1 and not sent_list:
                instr = (r.get("result") or {}).get("instructions") or ""
                proc.stdin.write((json.dumps(msgs[1]) + "\n" + json.dumps(msgs[2]) + "\n").encode())
                proc.stdin.flush()
                sent_list = True
            elif r.get("id") == 2:
                tools = (r.get("result") or {}).get("tools") or []
                out = {"tools": len(tools), "schema_bytes": len(json.dumps(tools).encode()),
                       "instructions_bytes": len(instr.encode()), "tool_names": [t.get("name") for t in tools][:200]}
                break
    except OSError as e:
        out = {"error": str(e)[:200]}
    finally:
        try:
            proc.kill()
        except OSError:
            pass
    return out


def run_probes(items: list, cfg: dict, cache: dict, force=False):
    probes = cache.setdefault("probes", {})
    if not cfg.get("probe_mcp"):
        return probes
    max_age = parse_duration(cfg["probe_every"], 86400)
    for it in items:
        conf = it.get("_conf")
        if it["kind"] != "mcp" or not conf or not conf.get("command"):
            continue
        h = it["conf_hash"]
        if not force and h in probes and time.time() - probes[h].get("at", 0) < max_age:
            continue
        status("probing MCP server", server=it["name"], scope=it["scope"])
        res = probe_stdio(conf, int(cfg.get("probe_timeout", 20)))
        res["at"] = time.time()
        probes[h] = res
    return probes


# ---------------------------------------------------------------- snapshot assembly

def build_snapshot(cfg: dict, cache: dict) -> dict:
    t0 = time.time()
    files = parse_transcripts(cache)
    cwds = {st["cwd"] for st in files.values() if st.get("cwd")}
    items, projects, meta = inventory(cfg, cache, cwds)
    probes = run_probes(items, cfg, cache) if cfg.get("_probe_now") else cache.get("probes", {})
    pricing = cfg["pricing"]
    now = now_utc()

    # --- merge transcript files into sessions
    sessions = {}
    for st in files.values():
        sid = st.get("session") or Path(st["path"]).stem
        s = sessions.setdefault(sid, {"id": sid, "project": None, "first_ts": None, "last_ts": None, "models": {},
                                      "calls": collections.Counter(), "mcp_tools": {}, "agent_tokens": collections.Counter(),
                                      "baseline": None, "context": {}, "subagent_files": 0, "entrypoint": None,
                                      "version": None, "branch": None, "turns": 0, "prompts": 0, "hourly": {}})
        if st["sidechain"]:
            s["subagent_files"] += 1
        else:
            s["project"] = s["project"] or st.get("cwd")
            s["baseline"] = s["baseline"] or st.get("baseline")
            if st["context"]:
                s["context"] = st["context"]
            s["entrypoint"] = s["entrypoint"] or st.get("entrypoint")
            s["branch"] = st.get("branch") or s["branch"]
            s["prompts"] += st["prompts"]
        s["project"] = s["project"] or st.get("cwd")
        s["version"] = st.get("version") or s["version"]
        for k in ("first_ts",):
            if st.get(k) and (not s[k] or st[k] < s[k]):
                s[k] = st[k]
        if st.get("last_ts") and (not s["last_ts"] or st["last_ts"] > s["last_ts"]):
            s["last_ts"] = st["last_ts"]
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
        sess_list.append({
            "id": s["id"], "project": short_path(s["project"]) if s["project"] else "(unknown)",
            "first_ts": s["first_ts"], "last_ts": s["last_ts"], "entrypoint": s["entrypoint"], "version": s["version"],
            "branch": s["branch"], "models": sorted(s["models"]), "turns": s["turns"], "prompts": s["prompts"],
            "subagent_files": s["subagent_files"],
            "tokens": {"input": tokens[0], "cache_write": tokens[1] + tokens[2], "cache_read": tokens[3],
                       "output": tokens[4], "total": sum(tokens)},
            "cost": round(cost, 4),
            "baseline_tokens": (s["baseline"] or {}).get("tokens"),
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
        bl = [s["baseline_tokens"] for s in ps[:20] if s["baseline_tokens"]]
        p.update({
            "sessions": len(ps), "last_active": ps[0]["last_ts"] if ps else None,
            "tokens": sum(s["tokens"]["total"] for s in ps), "cost": round(sum(s["cost"] for s in ps), 2),
            "own_always_tokens": tok(own), "est_always_tokens": tok(own + user_always),
            "baseline_median": sorted(bl)[len(bl) // 2] if bl else None,
            "counts": dict(collections.Counter(items_by_id[i]["kind"] for i in p["items"] if i in items_by_id)),
        })
    projects_out = sorted(proj_by_path.values(), key=lambda p: p.get("last_active") or "", reverse=True)

    # --- time series (hourly for series_days) and totals
    cutoff = (now - dt.timedelta(days=int(cfg["series_days"]))).strftime("%Y-%m-%dT%H")
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

    recent_bl = [s["baseline_tokens"] for s in sess_list[:30] if s["baseline_tokens"]]
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
    active_cut = iso(now - dt.timedelta(seconds=max(300, 3 * parse_duration(cfg["interval"]))))

    for s in sess_list:
        s.pop("_hourly", None)
        s.pop("_ctx", None)
    snap = {
        "schema": 1, "collector_version": VERSION, "generated_at": iso(now), "host": socket.gethostname(),
        "interval": cfg["interval"], "claude_dir": short_path(CLAUDE_DIR),
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
        "sessions": sess_list[: int(cfg["max_sessions"])],
        "series": series,
        "duplicates": duplicates,
        "meta": {**{k: v for k, v in meta.items() if k != "settings"}, "elapsed_ms": int((time.time() - t0) * 1000),
                 "transcript_files": len(files)},
    }
    return snap


def append_history(snap: dict):
    t = snap["totals"]
    row = {"t": snap["generated_at"], "global_always": t["global_always_tokens_est"], "baseline": t["baseline_latest"],
           "baseline_median": t["baseline_median"], "items": {k: v["count"] for k, v in t["by_kind"].items()},
           "always_by_kind": {k: v["tokens_always"] for k, v in t["by_kind"].items()},
           "today_tokens": t["today"]["tokens"], "today_cost": t["today"]["cost"]}
    path = STX_HOME / "history.jsonl"
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


def load_history(limit=2000) -> list:
    path = STX_HOME / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


_lock = threading.Lock()


def collect(cfg=None, probe=False) -> dict:
    with _lock:
        cfg = cfg or load_config()
        cfg["_probe_now"] = probe
        STX_HOME.mkdir(parents=True, exist_ok=True)
        cache_path = STX_HOME / "cache.json"
        cache = read_json(cache_path, {}) or {}
        status("reading transcripts")
        if probe:
            # probe needs inventory first; run a pass to get items then probe
            files = parse_transcripts(cache)
            items, _, _ = inventory(cfg, cache, {st["cwd"] for st in files.values() if st.get("cwd")})
            run_probes(items, cfg, cache)
            cfg["_probe_now"] = False
        snap = build_snapshot(cfg, cache)
        snap["history"] = load_history()
        write_json(cache_path, cache)
        write_json(STX_HOME / "snapshot.json", snap)
        append_history(snap)
        snap["history"] = load_history()
        write_json(STX_HOME / "snapshot.json", snap)
        status("idle", last_snapshot=snap["generated_at"], elapsed_ms=snap["meta"]["elapsed_ms"])
        return snap


# ---------------------------------------------------------------- serve

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(REPO_DIR / "dashboard"), **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] in ("/snapshot.json", "/api/snapshot"):
            body = (STX_HOME / "snapshot.json").read_bytes() if (STX_HOME / "snapshot.json").exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] in ("/", "/index.html"):
            # the page is authored as an artifact body; give it the same skeleton the artifact host adds
            page = (REPO_DIR / "dashboard" / "index.html").read_text(encoding="utf-8")
            body = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" '
                    'content="width=device-width,initial-scale=1,viewport-fit=cover"><style>body{margin:0}'
                    '[hidden]{display:none!important}</style></head><body>' + page + "</body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/api/status":
            body = (STX_HOME / "status.json").read_bytes() if (STX_HOME / "status.json").exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/api/refresh":
            threading.Thread(target=collect, daemon=True).start()
            self.send_response(202)
            self.end_headers()
            return
        super().do_GET()


def serve():
    cfg = load_config()
    last_probe = 0.0

    def loop():
        nonlocal last_probe
        while True:
            c = load_config()  # re-read so interval edits apply without restart
            probe = c.get("probe_mcp") and time.time() - last_probe > parse_duration(c["probe_every"], 86400)
            try:
                snap = collect(c, probe=bool(probe))
                if probe:
                    last_probe = time.time()
                print(f"[{snap['generated_at']}] {snap['totals']['sessions']} sessions, "
                      f"{len(snap['items'])} items, {snap['meta']['elapsed_ms']} ms", flush=True)
            except Exception as e:  # keep the daemon alive
                print(f"collect failed: {e!r}", file=sys.stderr, flush=True)
            time.sleep(max(10, parse_duration(c["interval"], 60)))

    threading.Thread(target=loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(cfg["port"])), Handler)
    print(f"Stellaxis dashboard: http://127.0.0.1:{cfg['port']}  (interval {cfg['interval']})", flush=True)
    srv.serve_forever()


def print_summary(snap: dict):
    t = snap["totals"]
    print(f"Stellaxis {snap['generated_at']} host={snap['host']}  sessions={t['sessions']} projects={t['projects']}")
    print(f"  measured baseline (first-turn context): latest={t['baseline_latest']} median={t['baseline_median']} tokens")
    print(f"  global always-on estimate: ~{t['global_always_tokens_est']} tokens")
    if snap.get("latest_context"):
        print("  latest session context breakdown (~tokens):",
              ", ".join(f"{k}={v}" for k, v in snap["latest_context"].items()))
    for k, v in sorted(t["by_kind"].items(), key=lambda kv: -kv[1]["tokens_always"]):
        print(f"  {k:10s} count={v['count']:4d}  always~{v['tokens_always']:6d} tok  never-used={v['unused']}")
    print(f"  today: {t['today']['tokens']:,} tokens ${t['today']['cost']}  |  30d: {t['month']['tokens']:,} tokens ${t['month']['cost']}")
    print("  top always-on items:")
    for it in snap["items"][:15]:
        print(f"    {it['tokens_always']:6d}  {it['id']}  calls={it['usage']['calls']}  [{it.get('source')}]")
    if snap["duplicates"]:
        print(f"  duplicates: {len(snap['duplicates'])} (e.g. {snap['duplicates'][0]['items']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "serve", "summary"])
    ap.add_argument("--probe", action="store_true", help="probe stdio MCP servers for schema size now")
    ap.add_argument("--json", action="store_true", help="print the snapshot JSON")
    a = ap.parse_args()
    if a.cmd == "serve":
        serve()
        return
    snap = collect(probe=a.probe) if a.cmd == "run" else read_json(STX_HOME / "snapshot.json", {})
    if a.json:
        json.dump(snap, sys.stdout, indent=1, default=list)
    elif snap:
        print_summary(snap)
        print(f"\nsnapshot: {short_path(STX_HOME / 'snapshot.json')}")


if __name__ == "__main__":
    main()
