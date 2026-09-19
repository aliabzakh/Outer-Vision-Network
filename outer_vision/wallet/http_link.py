"""The phone link over HTTP (LAN, or via the marketplace dev server's /rig proxy). Used when Web
Bluetooth isn't available. Every call that changes authority is owner-signed, so the transport itself
needs no secret; events are polled with GET /wallet/events?since=<t_ms>."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .link import MAX_MESSAGE, Link


def serve(wallet, port: int, host="0.0.0.0"):
    link = Link(wallet)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes):
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-headers", "content-type")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self._send(204, b"")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.rstrip("/") == "/wallet/events":
                try:
                    since = int(parse_qs(u.query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                self._send(200, json.dumps([e for e in wallet.events if e["t_ms"] > since]).encode())
            elif u.path.rstrip("/") == "/wallet":
                self._send(200, json.dumps(link.handle({"op": "status"})).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if urlparse(self.path).path.rstrip("/") != "/wallet":
                return self._send(404, b'{"error":"not found"}')
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_MESSAGE:
                return self._send(413, b'{"ok":false,"error":"message too large"}')
            self._send(200, link.handle_bytes(self.rfile.read(n)))

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
