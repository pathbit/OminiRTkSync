"""CLI and orchestrator of OminiRTKSync for OmniRoute."""

import argparse
import os
import signal
import sys
import time
from datetime import datetime

from .config import Settings
from .cron import CronScheduler
from .database import get_all_combos, get_all_connections, update_connection
from .discovery import HostDiscoveryEngine
from .normalizer import parse_expiry_to_ms
from .providers import ApiKeyProvider, GenericOAuthProvider, GoogleProvider, LocalProvider
from .web import start_omini_web


def log_msg(prefix: str, text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{prefix}] {text}", flush=True)


class OmniSyncEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.discovery = HostDiscoveryEngine(
            host_home=settings.host_home,
            extra_paths=settings.credential_paths,
        )
        self.google_provider = GoogleProvider(credential_paths=settings.credential_paths, discovery=self.discovery)
        self.oauth_provider = GenericOAuthProvider(discovery=self.discovery)
        self.api_provider = ApiKeyProvider(discovery=self.discovery)
        self.local_provider = LocalProvider()

    def sync_all(self):
        if not os.path.exists(self.settings.db_path):
            log_msg("WARNING", f"Waiting for OmniRoute database at: {self.settings.db_path}")
            return {"success": False, "error": "db_not_found"}

        conns = get_all_connections(self.settings.db_path)
        log_msg("INFO", f"Inspecting {len(conns)} connections in OmniRoute ({self.settings.db_path})...")

        refreshed = 0
        now_ms = int(time.time() * 1000)

        for c in conns:
            provider = c["provider"]
            cid = c["id"]
            name = c["name"]

            # 1. Google / Antigravity OAuth
            if provider in ("antigravity", "gemini-cli"):
                local = self.google_provider.read_local_credential()
                ref_tok = c.get("refreshToken")
                if not ref_tok and local:
                    ref_tok = local.get("refresh_token")

                exp_ms = parse_expiry_to_ms(c.get("expiresAt"))
                rem_sec = int((exp_ms - now_ms) / 1000) if exp_ms else 0

                if rem_sec <= self.settings.refresh_margin or not c.get("accessToken"):
                    if ref_tok:
                        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
                        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
                        if local:
                            client_id = local.get("client_id") or client_id
                            client_secret = local.get("client_secret") or client_secret
                        if not client_id or not client_secret:
                            data_dir = os.environ.get("DATA_DIR", "/app/data")
                            candidate_files = [
                                os.path.join(data_dir, "shared.js"),
                                "/app/data/shared.js",
                                "/app/open-sse/providers/shared.js",
                                "/app/open-sse/providers/registry/antigravity.js",
                            ]
                            for fpath in candidate_files:
                                if os.path.exists(fpath):
                                    try:
                                        with open(fpath, "r", encoding="utf-8") as f:
                                            content = f.read()
                                        import re
                                        m_id = re.search(r'clientId:\s*["\']([^"\']+)["\']', content)
                                        m_sec = re.search(r'clientSecret:\s*["\']([^"\']+)["\']', content)
                                        if m_id and not client_id:
                                            client_id = m_id.group(1)
                                        if m_sec and not client_secret:
                                            client_secret = m_sec.group(1)
                                        if client_id and client_secret:
                                            break
                                    except Exception:
                                        pass

                        ok, resp, err = self.google_provider.refresh(ref_tok, client_id, client_secret)
                        if ok and resp:
                            exp_in = int(resp.get("expires_in", 3599))
                            new_exp_ms = now_ms + (exp_in * 1000)
                            update_connection(
                                self.settings.db_path,
                                cid,
                                access_token=resp["access_token"],
                                refresh_token=resp.get("refresh_token", ref_tok),
                                expires_at_ms=new_exp_ms,
                            )
                            refreshed += 1
                            log_msg("SUCCESS", f"[{provider} · {name}] OAuth refreshed successfully ({exp_in}s)")
                            continue
                        else:
                            log_msg("FAILURE", f"[{provider} · {name}] Error refreshing OAuth: {err}")
                else:
                    log_msg("OK", f"[{provider} · {name}] Token valid for another {rem_sec // 60} min")
                continue

            # 2. Other OAuth Providers (Claude, GitHub, Codex, Kiro)
            if self.oauth_provider.can_handle(c):
                mod, data, notes = self.oauth_provider.check_and_refresh(c, margin_seconds=self.settings.refresh_margin)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                if mod and data:
                    update_connection(
                        self.settings.db_path,
                        cid,
                        access_token=data["accessToken"],
                        refresh_token=data.get("refreshToken", c.get("refreshToken", "")),
                        expires_at_ms=data.get("expiresAt", now_ms + 3600000),
                    )
                    refreshed += 1
                    log_msg("SUCCESS", f"[{provider} · {name}] OAuth credentials updated in storage.sqlite")
                continue

            # 3. API Key Providers (Groq, Mistral, OpenRouter, Gemini, OpenAI, etc.)
            if self.api_provider.can_handle(c):
                mod, data, notes = self.api_provider.check_and_refresh(c)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                if mod and data:
                    refreshed += 1
                    log_msg("SUCCESS", f"[{provider} · {name}] API key synchronized in storage.sqlite")
                continue

            # 4. Local Providers (Ollama, local proxies)
            if self.local_provider.can_handle(c):
                _, _, notes = self.local_provider.check_and_refresh(c)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                continue

            log_msg("INFO", f"[{provider} · {name}] Connection preserved with no pending actions")

        return {"success": True, "total": len(conns), "refreshed": refreshed}


def print_status(settings: Settings):
    try:
        conns = get_all_connections(settings.db_path)
        combos = get_all_combos(settings.db_path)
    except Exception as e:
        print(f"❌ Error querying SQLite database ({settings.db_path}): {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 74)
    print("⚡ OMINIRTKSYNC · OMNIROUTE CONNECTION STATUS")
    print(f"   Database: {settings.db_path}")
    print("=" * 74)

    print(f"\n🔌 Registered Connections ({len(conns)}):")
    print(f"  {'PROVIDER':<16} {'NAME':<26} {'TYPE':<10} {'STATUS':<10}")
    print("  " + "-" * 72)

    for c in conns:
        tipo = "OAuth 2.0" if c["isOAuth"] else ("API Key" if c["hasApiKey"] else "Other")
        st = c.get("testStatus", "active")
        print(f"  {c['provider']:<16} {c['name'][:25]:<26} {tipo:<10} ✅ {st:<8}")

    if combos:
        print(f"\n🔀 Registered Combos ({len(combos)}):")
        for cb in combos:
            print(f"  • {cb['name']} ({len(cb['models'])} models)")

    print("\n" + "=" * 74 + "\n")


def run_daemon(settings: Settings):
    engine = OmniSyncEngine(settings)
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        print(f"\n[!] Signal {sig} received. Shutting down OminiRTKSync...", flush=True)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=" * 74, flush=True)
    print("⚡ OMINIRTKSYNC · OMNIROUTE UNIVERSAL TOKEN & CONNECTION SYNCHRONIZER", flush=True)
    print(f"   SQLite Database: {settings.db_path}", flush=True)
    print(f"   Gateway URL:     {settings.omniroute_url}", flush=True)
    print(f"   Host Home:       {engine.discovery.host_home}", flush=True)
    print("=" * 74, flush=True)

    # Initial scan of available credentials on host
    discovered = engine.discovery.discover_all()
    found_any = False
    for prov, info in discovered.items():
        if info:
            found_any = True
            log_msg("DISCOVERY", f"Host credential detected: [{prov}] -> {info.get('source_path')}")
    if not found_any:
        log_msg("DISCOVERY", f"No pre-existing local credentials in {engine.discovery.host_home}")

    cron_scheduler = CronScheduler(
        sync_callback=engine.sync_all,
        interval_seconds=settings.sync_interval,
        name="OminiRTKSync-CronScheduler",
    )

    if settings.enable_web:
        try:
            start_omini_web(
                settings.web_host,
                settings.web_port,
                settings.db_path,
                omniroute_url=settings.omniroute_url,
                sync_callback=engine.sync_all,
                settings=settings,
                cron_scheduler=cron_scheduler,
            )
            print(f"🌐 Web Dashboard active at: http://{settings.web_host}:{settings.web_port}", flush=True)
        except Exception as e:
            print(f"⚠️ Could not start web dashboard on port {settings.web_port}: {e}", flush=True)

    cron_scheduler.start()

    while running:
        time.sleep(1)

    cron_scheduler.stop()
    print("[*] OminiRTKSync terminated.", flush=True)


def main():
    parser = argparse.ArgumentParser(
        prog="ominirtksync",
        description="OminiRTKSync · OmniRoute Universal Token & Connection Synchronizer",
    )
    parser.add_argument("--db-path", dest="db_path", help="Path to OmniRoute storage.sqlite database")
    parser.add_argument("--status", action="store_true", help="Display OmniRoute connection status and exit")
    parser.add_argument("--once", action="store_true", help="Run a single synchronization pass and exit")
    parser.add_argument("--daemon", action="store_true", help="Run in perpetual daemon mode")
    parser.add_argument("--interval", type=int, help="Check interval in seconds (default: 300)")
    parser.add_argument("--margin", type=int, help="Refresh margin in seconds (default: 900)")
    parser.add_argument("--no-web", action="store_true", help="Disable web dashboard")
    parser.add_argument("--port", type=int, help="Web dashboard port (default: 9191)")
    parser.add_argument("--user", type=str, help="Web dashboard authentication username (default: admin)")
    parser.add_argument("--password", type=str, help="Web dashboard authentication password (default: pathbit)")

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.db_path:
        settings.db_path = args.db_path
    if args.interval:
        settings.sync_interval = args.interval
    if args.margin:
        settings.refresh_margin = args.margin
    if args.no_web:
        settings.enable_web = False
    if args.port:
        settings.web_port = args.port
    if args.user:
        settings.dashboard_user = args.user
    if args.password:
        settings.dashboard_password = args.password

    if args.status:
        print_status(settings)
        return

    if args.once:
        engine = OmniSyncEngine(settings)
        res = engine.sync_all()
        print(f"[*] OmniRoute synchronization complete: {res.get('total', 0)} connections inspected, {res.get('refreshed', 0)} refreshed.")
        return

    run_daemon(settings)


if __name__ == "__main__":
    main()
