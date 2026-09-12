"""HTTP server and web dashboard for OminiRTKSync with Basic Auth and Cron Scheduler."""

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from .config import Settings
from .database import get_all_combos, get_all_connections


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        ex = sys.exc_info()[1]
        if isinstance(ex, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class OminiDashboardHandler(BaseHTTPRequestHandler):
    settings: Optional[Settings] = None
    db_path: str = ""
    omniroute_url: str = ""
    sync_callback: Optional[Callable[[], Dict[str, Any]]] = None
    cron_scheduler: Optional[Any] = None
    _last_gw_check: float = 0.0
    _last_gw_ok: bool = True

    def log_message(self, format, *args):
        pass

    def check_auth(self) -> bool:
        if not self.settings:
            return True

        expected_user, expected_pass = self.settings.get_auth_credentials()
        auth_header = self.headers.get("Authorization", "")
        if not auth_header or not auth_header.startswith("Basic "):
            return False

        try:
            b64_val = auth_header[6:].strip()
            decoded = base64.b64decode(b64_val).decode("utf-8")
            if ":" not in decoded:
                return False
            user, pwd = decoded.split(":", 1)
            return user == expected_user and pwd == expected_pass
        except Exception:
            return False

    def require_auth(self) -> bool:
        if self.check_auth():
            return True

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="OminiRTKSync Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentication required. Default credentials: admin / pathbit")
        return False

    def do_GET(self):
        if self.path == "/healthz":
            self.serve_healthz()
            return

        if not self.require_auth():
            return

        if self.path in ("/", "/index.html"):
            self.serve_html()
        elif self.path == "/api/status":
            self.serve_status()
        elif self.path == "/api/cron-status":
            self.serve_cron_status()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self.require_auth():
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"

        if self.path == "/api/sync":
            self.handle_sync()
        elif self.path == "/api/test-gateway":
            self.handle_test_gateway()
        elif self.path == "/api/change-password":
            self.handle_change_password(raw_body)
        elif self.path == "/api/cron-run":
            self.handle_cron_run()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def serve_healthz(self):
        db_ok = bool(self.db_path and os.path.exists(self.db_path))
        router_ok = True
        if self.omniroute_url:
            now = time.time()
            if now - OminiDashboardHandler._last_gw_check < 15.0:
                router_ok = OminiDashboardHandler._last_gw_ok
            else:
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
                OminiDashboardHandler._last_gw_check = now
                OminiDashboardHandler._last_gw_ok = router_ok

        if db_ok and router_ok:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            reason = "DATABASE_NOT_READY" if not db_ok else "OMNIROUTE_SERVICE_UNREACHABLE"
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(reason.encode("utf-8"))

    def serve_status(self):
        conns = []
        combos = []
        if self.db_path and os.path.exists(self.db_path):
            try:
                conns = get_all_connections(self.db_path)
            except Exception:
                conns = []
            try:
                combos = get_all_combos(self.db_path)
            except Exception:
                combos = []

        cron_info = self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False}
        is_default = self.settings.is_default_password() if self.settings else False
        cur_user, _ = self.settings.get_auth_credentials() if self.settings else ("admin", "pathbit")

        payload = {
            "status": "online",
            "omnirouteUrl": self.omniroute_url,
            "dbPath": self.db_path,
            "currentUser": cur_user,
            "isDefaultPassword": is_default,
            "cron": cron_info,
            "connections": conns,
            "combos": combos,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_cron_status(self):
        cron_info = self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False}
        body = json.dumps(cron_info, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_test_gateway(self):
        start_t = time.time()
        gateway_ok = False
        status_code = 0
        gateway_err = ""
        target_url = self.omniroute_url

        try:
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "OminiRTKSync-Tester/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status_code = resp.status
                gateway_ok = status_code < 500
        except urllib.error.HTTPError as e:
            status_code = e.code
            gateway_ok = e.code < 500
        except Exception as ex:
            gateway_err = str(ex)

        latency_ms = int((time.time() - start_t) * 1000)

        db_exists = bool(self.db_path and os.path.exists(self.db_path))
        conns_count = 0
        combos_count = 0
        if db_exists:
            try:
                conns = get_all_connections(self.db_path)
                combos = get_all_combos(self.db_path)
                conns_count = len(conns)
                combos_count = len(combos)
            except Exception:
                pass

        result = {
            "success": gateway_ok and db_exists,
            "gatewayUrl": target_url,
            "gatewayStatus": "online" if gateway_ok else "offline",
            "httpStatusCode": status_code,
            "latencyMs": latency_ms,
            "gatewayError": gateway_err if not gateway_ok else None,
            "dbStatus": "ok" if db_exists else "not_found",
            "dbPath": self.db_path,
            "connectionsCount": conns_count,
            "combosCount": combos_count,
            "message": "OmniRoute gateway and storage.sqlite database 100% operational!" if (gateway_ok and db_exists) else "Failed to connect to OmniRoute or database unavailable",
        }

        body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_change_password(self, raw_body: bytes):
        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            new_user = str(data.get("newUser") or "admin").strip()
            new_pass = str(data.get("newPassword") or "").strip()

            if not new_pass or len(new_pass) < 4:
                body = json.dumps({"success": False, "error": "Password must be at least 4 characters long."}).encode("utf-8")
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.settings:
                ok = self.settings.update_auth_credentials(new_user, new_pass)
                if ok:
                    body = json.dumps({
                        "success": True,
                        "message": "Credentials updated successfully!",
                        "newUser": new_user,
                    }).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to save credentials")
        except Exception as e:
            body = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def handle_sync(self):
        if self.sync_callback:
            try:
                res = self.sync_callback()
                body = json.dumps({"success": True, "result": res}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)

    def handle_cron_run(self):
        if self.cron_scheduler:
            try:
                entry = self.cron_scheduler.trigger_now()
                body = json.dumps({"success": True, "cycle": entry}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                err = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
        self.handle_sync()

    def serve_html(self):
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OminiRTKSync · OmniRoute Universal Token & Connection Synchronizer</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-bright: #f0f6fc;
      --accent: #58a6ff;
      --success: #3fb950;
      --warning: #d29922;
      --danger: #f85149;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      line-height: 1.5;
      padding: 24px;
    }
    .container { max-width: 1120px; margin: 0 auto; }
    
    .alert-banner {
      display: none;
      background: rgba(210, 153, 34, 0.15);
      border: 1px solid var(--warning);
      color: #e3b341;
      padding: 12px 18px;
      border-radius: 6px;
      margin-bottom: 20px;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
    }
    .alert-banner.show { display: flex; }
    .btn-warning {
      background: #9e6a03;
      color: #fff;
      border: none;
      padding: 6px 14px;
      border-radius: 4px;
      cursor: pointer;
      font-weight: 600;
      font-size: 12px;
    }
    .btn-warning:hover { background: #b07807; }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }
    .brand h1 {
      font-size: 24px;
      color: var(--text-bright);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .brand p { font-size: 13px; color: #8b949e; }
    .header-actions { display: flex; gap: 10px; align-items: center; }
    .btn {
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid rgba(240, 246, 252, 0.1);
      transition: all 0.2s;
    }
    .btn-primary { background: #238636; color: #fff; }
    .btn-primary:hover { background: #2ea043; }
    .btn-secondary { background: #21262d; color: var(--text-bright); border-color: var(--border); }
    .btn-secondary:hover { background: #30363d; }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }

    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px;
    }
    .stat-card .label { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-card .value { font-size: 26px; font-weight: 700; color: var(--text-bright); margin-top: 4px; }
    
    .grid-two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }
    @media (max-width: 800px) { .grid-two { grid-template-columns: 1fr; } }

    .section-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--text-bright);
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      margin-bottom: 24px;
      padding: 16px;
    }
    .card-nopad { padding: 0; }

    .diag-box {
      background: #090d13;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px 16px;
      margin-top: 12px;
      font-family: var(--mono);
      font-size: 12px;
    }
    .diag-row { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed #21262d; }
    .diag-row:last-child { border-bottom: none; }

    .cron-info { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .pulse-dot {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--success);
      box-shadow: 0 0 8px var(--success);
      margin-right: 6px;
    }

    table { width: 100%; border-collapse: collapse; text-align: left; }
    th {
      background: #090d13;
      padding: 10px 14px;
      font-size: 11px;
      color: #8b949e;
      text-transform: uppercase;
      border-bottom: 1px solid var(--border);
    }
    td {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }
    tr:last-child td { border-bottom: none; }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .badge-ok { background: rgba(63, 185, 80, 0.2); color: var(--success); }
    .badge-warn { background: rgba(210, 153, 34, 0.2); color: var(--warning); }
    .badge-err { background: rgba(248, 81, 73, 0.2); color: var(--danger); }
    .badge-provider { background: #21262d; color: var(--text-bright); font-family: var(--mono); font-size: 11px; }
    .mono { font-family: var(--mono); font-size: 12px; }

    .modal-overlay {
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.7);
      backdrop-filter: blur(2px);
      z-index: 1000;
      align-items: center;
      justify-content: center;
    }
    .modal-overlay.open { display: flex; }
    .modal {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      width: 420px;
      max-width: 90%;
      padding: 24px;
    }
    .modal h2 { font-size: 18px; margin-bottom: 16px; color: var(--text-bright); }
    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 12px; color: #8b949e; margin-bottom: 4px; }
    .form-control {
      width: 100%;
      background: #090d13;
      border: 1px solid var(--border);
      color: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
    }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }

    footer { text-align: center; color: #8b949e; font-size: 12px; margin-top: 32px; padding-bottom: 20px; }
  </style>
</head>
<body>
  <div class="container">
    
    <div id="alertDefaultPass" class="alert-banner">
      <div>
        ⚠️ <strong>Security Notice:</strong> You are using the factory default credentials (<code>admin</code> / <code>pathbit</code>). It is strongly recommended to change your password to secure the dashboard.
      </div>
      <button class="btn-warning" onclick="openPasswordModal()">Change Credentials</button>
    </div>

    <header>
      <div class="brand">
        <h1>⚡ OminiRTKSync</h1>
        <p>OminiRoute Universal Token & Connection Synchronizer (<a href="https://github.com/diegosouzapw/OmniRoute" target="_blank" style="color:var(--accent);text-decoration:none;">OmniRoute</a>)</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" onclick="openPasswordModal()">🔐 Access</button>
        <button id="syncBtn" class="btn btn-primary" onclick="triggerSync()">🔄 Sync Now</button>
      </div>
    </header>

    <div class="stats">
      <div class="stat-card">
        <div class="label">Total Connections</div>
        <div class="value" id="statTotal">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Active OAuth Accounts</div>
        <div class="value" id="statOAuth" style="color:var(--accent);">-</div>
      </div>
      <div class="stat-card">
        <div class="label">API Key Providers</div>
        <div class="value" id="statApiKeys">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Registered Combos</div>
        <div class="value" id="statCombos" style="color:var(--success);">-</div>
      </div>
    </div>

    <div class="grid-two">
      <div class="card">
        <div class="section-title">
          <span>🔌 OmniRoute Gateway Connection</span>
          <button id="testGwBtn" class="btn btn-secondary" style="font-size:11px;padding:4px 10px;" onclick="testGateway()">Test Connection</button>
        </div>
        <p style="font-size:13px;color:#8b949e;">Validates HTTP response, latency, and access to the storage.sqlite database.</p>
        <div id="gwDiagBox" class="diag-box">
          <div class="diag-row"><span>OmniRoute URL:</span><span id="gwUrl">-</span></div>
          <div class="diag-row"><span>Gateway Status:</span><span id="gwStatus" style="color:#8b949e;">Awaiting test...</span></div>
          <div class="diag-row"><span>HTTP Latency:</span><span id="gwLatency">-</span></div>
          <div class="diag-row"><span>SQLite Database:</span><span id="gwDb">-</span></div>
          <div class="diag-row"><span>Diagnostic:</span><span id="gwMsg" style="color:#8b949e;">Click 'Test Connection'</span></div>
        </div>
      </div>

      <div class="card">
        <div class="section-title">
          <span>⏰ Cron Scheduler (Continuous Refresh)</span>
          <button id="cronRunBtn" class="btn btn-primary" style="font-size:11px;padding:4px 10px;" onclick="runCronNow()">Run Cron Now</button>
        </div>
        <div class="cron-info">
          <div style="font-size:13px;">
            <span class="pulse-dot"></span> <strong id="cronActiveLabel">Active</strong>
            <span style="color:#8b949e;margin-left:4px;">(every <span id="cronInterval">300</span>s)</span>
          </div>
          <span style="font-size:11px;color:#8b949e;">Total cycles: <strong id="cronTotalRuns">0</strong></span>
        </div>
        <div class="diag-box">
          <div class="diag-row"><span>Last Run:</span><span id="cronLastRun">-</span></div>
          <div class="diag-row"><span>Next Run:</span><span id="cronNextRun">-</span></div>
          <div class="diag-row"><span>Tokens Refreshed:</span><span id="cronTotalRenewals" style="color:var(--success);">0</span></div>
          <div class="diag-row"><span>Last Result:</span><span id="cronLastResult" style="color:var(--text-bright);">-</span></div>
        </div>
      </div>
    </div>

    <div class="section-title">🔌 Monitored Connections in OmniRoute</div>
    <div class="card card-nopad">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Name</th>
            <th>Type</th>
            <th>Status</th>
            <th>Remaining Validity</th>
          </tr>
        </thead>
        <tbody id="connsTableBody">
          <tr><td colspan="5" style="text-align:center;color:#8b949e;">Loading connections from storage.sqlite...</td></tr>
        </tbody>
      </table>
    </div>

    <div class="section-title">📋 Cron Cycle History</div>
    <div class="card card-nopad">
      <table>
        <thead>
          <tr>
            <th>Date & Time (UTC)</th>
            <th>Trigger</th>
            <th>Duration</th>
            <th>Accounts Inspected</th>
            <th>OAuth Refreshed</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="cronHistoryBody">
          <tr><td colspan="6" style="text-align:center;color:#8b949e;">No historical cycles recorded yet.</td></tr>
        </tbody>
      </table>
    </div>

    <footer>
      OminiRTKSync · OmniRoute Universal Token & Connection Synchronizer · <a href="https://pathbit.co/" target="_blank" style="color:#8b949e;text-decoration:none;">Developed with ❤️ by Pathbit</a>
    </footer>
  </div>

  <div id="passwordModal" class="modal-overlay">
    <div class="modal">
      <h2>🔐 Dashboard Credentials</h2>
      <p style="font-size:13px;color:#8b949e;margin-bottom:16px;">
        Change the HTTP Basic Auth credentials for your dashboard.
      </p>
      <div class="form-group">
        <label>Username</label>
        <input type="text" id="newUsernameInput" class="form-control" value="admin">
      </div>
      <div class="form-group">
        <label>New Password</label>
        <input type="password" id="newPasswordInput" class="form-control" placeholder="Minimum 4 characters">
      </div>
      <div class="form-group">
        <label>Confirm New Password</label>
        <input type="password" id="confirmPasswordInput" class="form-control" placeholder="Repeat password">
      </div>
      <div id="modalMsg" style="font-size:12px;margin-top:8px;"></div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closePasswordModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitPasswordChange()">Save Credentials</button>
      </div>
    </div>
  </div>

  <script>
    let currentData = {};

    async function loadStatus() {
      try {
        const res = await fetch('/api/status');
        if (res.status === 401) return;
        currentData = await res.json();
        renderDashboard(currentData);
      } catch (err) {
        console.error('Error loading status:', err);
      }
    }

    function renderDashboard(data) {
      const alertBanner = document.getElementById('alertDefaultPass');
      if (data.isDefaultPassword) {
        alertBanner.classList.add('show');
      } else {
        alertBanner.classList.remove('show');
      }

      document.getElementById('newUsernameInput').value = data.currentUser || 'admin';
      document.getElementById('gwUrl').innerText = data.omnirouteUrl || 'http://127.0.0.1:20128';

      const conns = data.connections || [];
      const combos = data.combos || [];
      document.getElementById('statTotal').innerText = conns.length;
      document.getElementById('statOAuth').innerText = conns.filter(c => c.isOAuth).length;
      document.getElementById('statApiKeys').innerText = conns.filter(c => c.hasApiKey).length;
      document.getElementById('statCombos').innerText = combos.length;

      const cron = data.cron || {};
      document.getElementById('cronInterval').innerText = cron.intervalSeconds || 300;
      document.getElementById('cronTotalRuns').innerText = cron.totalRuns || 0;
      document.getElementById('cronTotalRenewals').innerText = cron.totalRenewals || 0;
      document.getElementById('cronLastRun').innerText = cron.lastRunAt || 'None yet';
      document.getElementById('cronNextRun').innerText = cron.nextRunAt || 'Calculating...';
      
      if (cron.lastResult) {
        const lr = cron.lastResult;
        document.getElementById('cronLastResult').innerText = 
          `${lr.totalInspected} evaluated · ${lr.refreshedCount} refreshed (${lr.durationMs}ms)`;
      }

      const connsBody = document.getElementById('connsTableBody');
      if (conns.length === 0) {
        connsBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#8b949e;">No connections registered in OmniRoute.</td></tr>';
      } else {
        connsBody.innerHTML = conns.map(c => `
          <tr>
            <td><span class="badge badge-provider">${c.provider}</span></td>
            <td><strong>${c.name}</strong></td>
            <td>${c.isOAuth ? 'OAuth 2.0' : 'API Key'}</td>
            <td><span class="badge badge-ok">${c.testStatus || 'active'}</span></td>
            <td class="mono">${c.isOAuth ? 'Monitored via Cron' : 'Unlimited'}</td>
          </tr>
        `).join('');
      }

      const histBody = document.getElementById('cronHistoryBody');
      const history = cron.history || [];
      if (history.length === 0) {
        histBody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#8b949e;">No historical cycles recorded yet.</td></tr>';
      } else {
        histBody.innerHTML = history.map(h => `
          <tr>
            <td class="mono" style="font-size:11px;">${h.timestamp}</td>
            <td><span class="badge badge-provider">${h.reason}</span></td>
            <td class="mono">${h.durationMs}ms</td>
            <td>${h.totalInspected} accounts</td>
            <td style="color:${h.refreshedCount > 0 ? 'var(--success)' : 'inherit'};"><strong>${h.refreshedCount}</strong></td>
            <td><span class="badge ${h.success ? 'badge-ok' : 'badge-err'}">${h.success ? 'SUCCESS' : 'FAILURE'}</span></td>
          </tr>
        `).join('');
      }
    }

    async function testGateway() {
      const btn = document.getElementById('testGwBtn');
      btn.disabled = true;
      btn.innerText = 'Testing...';
      try {
        const res = await fetch('/api/test-gateway', { method: 'POST' });
        const diag = await res.json();
        
        document.getElementById('gwStatus').innerHTML = diag.gatewayStatus === 'online' 
          ? `<span style="color:var(--success);font-weight:bold;">ONLINE (HTTP ${diag.httpStatusCode})</span>`
          : `<span style="color:var(--danger);font-weight:bold;">OFFLINE (${diag.gatewayError || 'Error'})</span>`;
        document.getElementById('gwLatency').innerText = `${diag.latencyMs} ms`;
        document.getElementById('gwDb').innerText = diag.dbStatus === 'ok' 
          ? `Operational (${diag.connectionsCount} connections, ${diag.combosCount} combos)`
          : 'Inaccessible / Not found';
        document.getElementById('gwMsg').innerText = diag.message;
        document.getElementById('gwMsg').style.color = diag.success ? 'var(--success)' : 'var(--danger)';
      } catch (err) {
        alert('Error testing gateway: ' + err);
      } finally {
        btn.disabled = false;
        btn.innerText = 'Test Connection';
      }
    }

    async function runCronNow() {
      const btn = document.getElementById('cronRunBtn');
      btn.disabled = true;
      btn.innerText = 'Running...';
      try {
        await fetch('/api/cron-run', { method: 'POST' });
        await loadStatus();
      } catch (err) {
        alert('Error running cron: ' + err);
      } finally {
        btn.disabled = false;
        btn.innerText = 'Run Cron Now';
      }
    }

    async function triggerSync() {
      const btn = document.getElementById('syncBtn');
      btn.disabled = true;
      btn.innerText = 'Synchronizing...';
      try {
        await fetch('/api/sync', { method: 'POST' });
        await loadStatus();
      } catch (err) {
        alert('Error synchronizing: ' + err);
      } finally {
        btn.disabled = false;
        btn.innerText = '🔄 Sync Now';
      }
    }

    function openPasswordModal() {
      document.getElementById('passwordModal').classList.add('open');
      document.getElementById('modalMsg').innerText = '';
    }

    function closePasswordModal() {
      document.getElementById('passwordModal').classList.remove('open');
    }

    async function submitPasswordChange() {
      const user = document.getElementById('newUsernameInput').value.trim();
      const pass = document.getElementById('newPasswordInput').value;
      const conf = document.getElementById('confirmPasswordInput').value;
      const msg = document.getElementById('modalMsg');

      if (!pass || pass.length < 4) {
        msg.innerText = '❌ Password must be at least 4 characters long.';
        msg.style.color = 'var(--danger)';
        return;
      }
      if (pass !== conf) {
        msg.innerText = '❌ Passwords do not match.';
        msg.style.color = 'var(--danger)';
        return;
      }

      try {
        const res = await fetch('/api/change-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ newUser: user, newPassword: pass })
        });
        const d = await res.json();
        if (d.success) {
          msg.innerText = '✅ ' + d.message;
          msg.style.color = 'var(--success)';
          setTimeout(() => {
            closePasswordModal();
            loadStatus();
          }, 1500);
        } else {
          msg.innerText = '❌ ' + (d.error || 'Error updating credentials');
          msg.style.color = 'var(--danger)';
        }
      } catch (e) {
        msg.innerText = '❌ ' + e.message;
        msg.style.color = 'var(--danger)';
      }
    }

    loadStatus();
    setInterval(loadStatus, 15000);
    setTimeout(testGateway, 1000);
  </script>
</body>
</html>"""
        content = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def start_omini_web(
    host: str,
    port: int,
    db_path: str,
    omniroute_url: str = "",
    sync_callback: Optional[Callable[[], Dict[str, Any]]] = None,
    settings: Optional[Settings] = None,
    cron_scheduler: Optional[Any] = None,
) -> HTTPServer:
    OminiDashboardHandler.db_path = db_path
    OminiDashboardHandler.omniroute_url = omniroute_url
    OminiDashboardHandler.sync_callback = sync_callback
    OminiDashboardHandler.settings = settings
    OminiDashboardHandler.cron_scheduler = cron_scheduler

    server = QuietThreadingHTTPServer((host, port), OminiDashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server

