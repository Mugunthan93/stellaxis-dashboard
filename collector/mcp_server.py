#!/usr/bin/env python3
"""Stellaxis MCP server (stdio, stdlib only). One tool, so its always-on cost stays tiny.

Register it in the Claude desktop app only (not in Claude Code), so the dashboard artifact can read
live data via `host:stellaxis`:

  ~/Library/Application Support/Claude/claude_desktop_config.json
  {"mcpServers": {"stellaxis": {"command": "python3", "args": ["<repo>/collector/mcp_server.py"]}}}
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C  # noqa: E402

TOOL = {
    "name": "stellaxis_snapshot",
    "description": "Claude setup usage snapshot (skills, agents, plugins, MCP; tokens, cost). view: dashboard|summary|items|sessions|projects|history.",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "enum": ["dashboard", "summary", "items", "sessions", "projects", "history"]},
            "limit": {"type": "integer"},
            "project": {"type": "string"},
            "refresh": {"type": "boolean"},
        },
    },
}


def snapshot(refresh=False) -> dict:
    path = C.STX_HOME / "snapshot.json"
    cfg = C.load_config()
    stale = not path.exists() or time.time() - path.stat().st_mtime > 2 * max(30, C.parse_duration(cfg["interval"]))
    if refresh or stale:
        return C.collect(cfg)
    return C.read_json(path, {}) or {}


def view(args: dict) -> dict:
    s = snapshot(bool(args.get("refresh")))
    v = args.get("view") or "summary"
    lim = int(args.get("limit") or 0)
    proj = args.get("project")
    sessions = [x for x in s.get("sessions", []) if not proj or x["project"] == proj]
    head = {k: s.get(k) for k in ("generated_at", "host", "interval", "collector_version", "totals", "latest_context")}
    if v == "dashboard":
        return {**s, "sessions": sessions[: lim or 200]}
    if v == "summary":
        return {**head, "top_items": s.get("items", [])[: lim or 25], "duplicates": s.get("duplicates", []),
                "projects": [{k: p.get(k) for k in ("path", "sessions", "tokens", "cost", "est_always_tokens",
                                                     "baseline_median", "last_active")} for p in s.get("projects", [])]}
    if v == "items":
        return {**head, "items": s.get("items", [])[: lim or None]}
    if v == "sessions":
        return {**head, "sessions": sessions[: lim or 50]}
    if v == "projects":
        return {**head, "projects": s.get("projects", [])}
    if v == "history":
        return {**head, "history": s.get("history", [])[-(lim or 500):], "series": s.get("series", [])}
    raise ValueError(f"unknown view {v}")


def handle(msg: dict):
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"protocolVersion": ver, "capabilities": {"tools": {}},
                "serverInfo": {"name": "stellaxis", "version": C.VERSION}}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        p = msg.get("params") or {}
        if p.get("name") != TOOL["name"]:
            raise ValueError(f"unknown tool {p.get('name')}")
        try:
            data = view(p.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(data, default=list)}], "structuredContent": data}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"stellaxis error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is None:
        return None
    raise LookupError(method)


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") is None:
            continue  # notification
        try:
            res = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as e:
            res = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"method not found: {e}"}}
        except Exception as e:
            res = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(res, default=list) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
