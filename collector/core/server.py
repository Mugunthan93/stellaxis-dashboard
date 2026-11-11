from __future__ import annotations

import http.server
import json
import traceback

from core.app import Core


def host_ok(handler) -> bool:
    # reject DNS-rebinding and cross-site requests: only our own localhost origin may read or write
    port = handler.server.server_address[1]
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if handler.headers.get("Host", "") not in allowed:
        return False
    origin = handler.headers.get("Origin")
    return origin is None or origin.split("://", 1)[-1] in allowed


def _json(handler, status: int, obj) -> None:
    body = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle(core: Core, handler, method: str, cfg: dict) -> bool:
    """Answer GET /api/core, else offer the request to each component's `handle`. True when answered."""
    if method == "GET" and handler.path.split("?")[0] == "/api/core":
        _json(handler, 200, core.describe())
        return True
    for c in sorted(core.components, key=lambda c: c.name):
        if c.handle is None or c.error:
            continue
        try:
            if c.handle(handler, method, core.ctx(c.name, cfg)):
                return True
        except Exception as e:
            traceback.print_exc()
            _json(handler, 500, {"error": f"{type(e).__name__}: {e}"})
            return True
    return False


def serve(core: Core, port: int, cfg: dict) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, method: str) -> None:
            if not host_ok(self):
                return self.send_error(403)
            if not handle(core, self, method, cfg):
                self.send_error(404)

        def do_GET(self):
            self._answer("GET")

        def do_POST(self):
            self._answer("POST")

        def log_message(self, *args):
            pass

    return http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
