#!/usr/bin/env python3
"""Local inbox for reel pages. fetch_reel.js redirects here with queue items in ?d=, which saves retyping signed URLs.
Listens on 127.0.0.1 only and appends to R/.tools/inbox.jsonl. Run it while capturing, then stop it.
Any web page can reach localhost, so each request must carry the token printed at start: /r?t=<token>&d=<json>."""
import hmac, http.server, json, os, secrets, urllib.parse

R = os.path.expanduser(os.environ.get("REELS_HOME", "~/Knowledge/reels"))
INBOX = os.path.join(R, ".tools", "inbox.jsonl")
TOKEN = ""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not TOKEN or not hmac.compare_digest(q.get("t", [""])[0], TOKEN):
            self.send_error(403)
            return
        raw = q.get("d", [""])[0]
        try:
            items = json.loads(raw)
            items = items if isinstance(items, list) else [items]
            with open(INBOX, "a", encoding="utf-8") as f:
                f.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in items)
            body = f"saved {len(items)}".encode()
        except ValueError:
            body = b"ignored"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    TOKEN = secrets.token_urlsafe(16)
    print("token:", TOKEN, flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", 7788), Handler).serve_forever()
