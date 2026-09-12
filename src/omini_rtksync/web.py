"""Servidor HTTP e dashboard web para OminiRTKSync."""

import json
import os
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict

from .database import get_all_combos, get_all_connections


class OminiDashboardHandler(BaseHTTPRequestHandler):
    db_path: str = ""
    omniroute_url: str = ""
    sync_callback: Callable[[], Dict[str, Any]] = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.serve_html()
        elif self.path == "/api/status":
            self.serve_status()
        elif self.path == "/healthz":
            db_ok = bool(self.db_path and os.path.exists(self.db_path))
            router_ok = True
            if self.omniroute_url:
                try:
                    req = urllib.request.Request(
                        self.omniroute_url,
                        headers={"User-Agent": "OminiRTKSync-Healthcheck/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        router_ok = resp.status < 500
                except urllib.error.HTTPError as e:
                    router_ok = e.code < 500
                except Exception:
                    router_ok = False

            if db_ok and router_ok:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                reason = "DATABASE_NOT_READY" if not db_ok else "OMNIROUTE_SERVICE_UNREACHABLE"
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(reason.encode("utf-8"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path == "/api/sync" and self.sync_callback:
            try:
                res = self.sync_callback()
                body = json.dumps({"success": True, "result": res}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def serve_html(self):
        html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>OminiRTKSync · OminiRoute Universal Token & Connection Synchronizer</title>
  <style>
    body { background: #0b0f19; color: #c9d1d9; font-family: -apple-system, sans-serif; padding: 24px; }
    .container { max-width: 1000px; margin: 0 auto; }
    h1 { color: #58a6ff; font-size: 24px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin: 16px 0; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px; border-bottom: 1px solid #30363d; text-align: left; }
    th { color: #8b949e; text-transform: uppercase; font-size: 12px; }
    .btn { background: #238636; color: #fff; padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; }
    .badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; background: rgba(63,185,80,0.2); color: #3fb950; }
  </style>
</head>
<body>
  <div class="container">
    <h1>⚡ OminiRTKSync</h1>
    <p>OminiRoute Universal Token & Connection Synchronizer (<a href="https://github.com/diegosouzapw/OmniRoute" style="color:#58a6ff;">OmniRoute</a>)</p>
    <div class="card">
      <button class="btn" onclick="fetch('/api/sync', {method:'POST'}).then(()=>location.reload())">Sincronizar Agora</button>
    </div>
    <div class="card">
      <h3>Conexões Monitoradas</h3>
      <table id="tbl">
        <thead><tr><th>Provedor</th><th>Nome</th><th>Tipo</th><th>Status</th></tr></thead>
        <tbody id="body"><tr><td colspan="4">Carregando...</td></tr></tbody>
      </table>
    </div>
  </div>
  <script>
    fetch('/api/status').then(r=>r.json()).then(d=>{
      const b = document.getElementById('body');
      b.innerHTML = (d.connections||[]).map(c => `
        <tr>
          <td><strong>${c.provider}</strong></td>
          <td>${c.name}</td>
          <td>${c.isOAuth ? 'OAuth 2.0' : 'API Key'}</td>
          <td><span class="badge">${c.testStatus||'ativo'}</span></td>
        </tr>
      `).join('') || '<tr><td colspan="4">Nenhuma conexão registrada.</td></tr>';
    });
  </script>
</body>
</html>"""
        content = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_status(self):
        try:
            conns = get_all_connections(self.db_path)
            combos = get_all_combos(self.db_path)
            body = json.dumps({"connections": conns, "combos": combos}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)


def start_omini_web(
    host: str,
    port: int,
    db_path: str,
    omniroute_url: str = "",
    sync_callback: Callable[[], Dict[str, Any]] = None,
) -> HTTPServer:
    OminiDashboardHandler.db_path = db_path
    OminiDashboardHandler.omniroute_url = omniroute_url
    OminiDashboardHandler.sync_callback = sync_callback
    server = HTTPServer((host, port), OminiDashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
