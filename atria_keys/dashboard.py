from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .keystore import KeyStore
from .state import StateStore


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Atria Keys</title><style>
body{font:15px system-ui,sans-serif;background:#101318;color:#e9eef5;margin:2rem;max-width:1100px}
h1{font-size:1.5rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}
.card{background:#1b212b;border:1px solid #303a48;border-radius:10px;padding:1rem}pre{white-space:pre-wrap}
.muted{color:#9ba9ba}table{border-collapse:collapse;width:100%}td,th{padding:.45rem;text-align:left;border-bottom:1px solid #303a48}
</style></head><body><h1>Atria key operations</h1><p class="muted">Read-only local view · refreshes every 3s</p>
<div id="app">Loading…</div><script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function draw(){let d=await fetch('/api/status').then(r=>r.json());let rate=d.challenge_first_pass_rate;
document.querySelector('#app').innerHTML=`<div class="grid"><div class="card"><b>Runs in flight</b><h2>${d.in_flight}</h2></div>
<div class="card"><b>Keys minted</b><h2>${d.key_count}</h2></div><div class="card"><b>Challenge first-pass</b><h2>${rate==null?'—':(rate*100).toFixed(1)+'%'}</h2></div></div>
<h2>Keys</h2><table><tr><th>ID</th><th>Key</th><th>Email</th><th>Created</th></tr>${d.keys.map(k=>`<tr><td>${esc(k.key_id)}</td><td>${esc(k.api_key)}</td><td>${esc(k.email)}</td><td>${esc(k.created_at)}</td></tr>`).join('')}</table>
<h2>Stage outcomes</h2><pre>${esc(JSON.stringify(d.stages,null,2))}</pre><h2>Failure reasons</h2><pre>${esc(JSON.stringify(d.failure_reasons,null,2))}</pre>`}
draw();setInterval(draw,3000);</script></body></html>"""


class Dashboard:
    def __init__(self, state: StateStore, keystore: KeyStore, port: int = 8686):
        self.state = state
        self.keystore = keystore
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        result = self.state.dashboard_snapshot()
        result["keys"] = self.keystore.masked_records()
        result["key_count"] = len(result["keys"])
        return result

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/":
                    payload, content_type = PAGE.encode(), "text/html; charset=utf-8"
                elif self.path == "/api/status":
                    payload = json.dumps(dashboard.snapshot(), separators=(",", ":")).encode()
                    content_type = "application/json"
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler

    def start(self) -> None:
        if self.server:
            return
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def serve_forever(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self.server.serve_forever()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
