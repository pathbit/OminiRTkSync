"""CLI e orquestrador do OminiRTKSync para OmniRoute."""

import argparse
import os
import signal
import sys
import time
from datetime import datetime

from .config import Settings
from .database import get_all_combos, get_all_connections, update_connection
from .normalizer import parse_expiry_to_ms
from .providers import GoogleProvider
from .web import start_omini_web


def log_msg(prefix: str, text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{prefix}] {text}", flush=True)


class OmniSyncEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.google_provider = GoogleProvider(credential_paths=settings.credential_paths)

    def sync_all(self):
        if not os.path.exists(self.settings.db_path):
            log_msg("AVISO", f"Aguardando banco do OmniRoute em: {self.settings.db_path}")
            return {"success": False, "error": "db_not_found"}

        conns = get_all_connections(self.settings.db_path)
        log_msg("INFO", f"Inspecionando {len(conns)} conexões no OmniRoute ({self.settings.db_path})...")

        refreshed = 0
        now_ms = int(time.time() * 1000)

        for c in conns:
            provider = c["provider"]
            cid = c["id"]
            name = c["name"]

            # Google / Antigravity OAuth
            if provider in ("antigravity", "gemini-cli"):
                # Verifica se há credencial local no host
                local = self.google_provider.read_local_credential()
                if local and local.get("access_token") and local.get("access_token") != c.get("accessToken"):
                    exp_ms = now_ms + (3599 * 1000)
                    update_connection(
                        self.settings.db_path,
                        cid,
                        access_token=local["access_token"],
                        refresh_token=local.get("refresh_token", c.get("refreshToken", "")),
                        expires_at_ms=exp_ms,
                    )
                    refreshed += 1
                    log_msg("SUCESSO", f"[{provider} · {name}] Token atualizado via credencial local do host")
                    continue

                # Verifica expiração
                exp_ms = parse_expiry_to_ms(c.get("expiresAt"))
                rem_sec = int((exp_ms - now_ms) / 1000) if exp_ms else 0

                if rem_sec <= self.settings.refresh_margin:
                    ref_tok = c.get("refreshToken")
                    if ref_tok:
                        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
                        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
                        if local:
                            client_id = local.get("client_id") or client_id
                            client_secret = local.get("client_secret") or client_secret

                        if client_id and client_secret:
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
                                log_msg("SUCESSO", f"[{provider} · {name}] OAuth renovado com sucesso ({exp_in}s)")
                            else:
                                log_msg("FALHA", f"[{provider} · {name}] Erro ao renovar OAuth: {err}")
                else:
                    log_msg("OK", f"[{provider} · {name}] Token válido por mais {rem_sec // 60} min")

        return {"success": True, "total": len(conns), "refreshed": refreshed}


def print_status(settings: Settings):
    try:
        conns = get_all_connections(settings.db_path)
        combos = get_all_combos(settings.db_path)
    except Exception as e:
        print(f"❌ Erro ao consultar banco SQLite ({settings.db_path}): {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 74)
    print("⚡ OMINIRTKSYNC · STATUS DAS CONEXÕES DO OMNIROUTE")
    print(f"   Banco de Dados: {settings.db_path}")
    print("=" * 74)

    print(f"\n🔌 Conexões Registradas ({len(conns)}):")
    print(f"  {'PROVEDOR':<16} {'NOME':<26} {'TIPO':<10} {'STATUS':<10}")
    print("  " + "-" * 72)

    for c in conns:
        tipo = "OAuth 2.0" if c["isOAuth"] else ("API Key" if c["hasApiKey"] else "Outro")
        st = c.get("testStatus", "ativo")
        print(f"  {c['provider']:<16} {c['name'][:25]:<26} {tipo:<10} ✅ {st:<8}")

    if combos:
        print(f"\n🔀 Combos Cadastrados ({len(combos)}):")
        for cb in combos:
            print(f"  • {cb['name']} ({len(cb['models'])} modelos)")

    print("\n" + "=" * 74 + "\n")


def run_daemon(settings: Settings):
    engine = OmniSyncEngine(settings)
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        print(f"\n[!] Sinal {sig} recebido. Encerrando OminiRTKSync...", flush=True)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=" * 74, flush=True)
    print("⚡ OMINIRTKSYNC · OMNIROUTE UNIVERSAL TOKEN & CONNECTION SYNCHRONIZER", flush=True)
    print(f"   Banco SQLite: {settings.db_path}", flush=True)
    print(f"   Gateway URL:  {settings.omniroute_url}", flush=True)
    print("=" * 74, flush=True)

    if settings.enable_web:
        try:
            start_omini_web(
                settings.web_host,
                settings.web_port,
                settings.db_path,
                omniroute_url=settings.omniroute_url,
                sync_callback=engine.sync_all,
            )
            print(f"🌐 Dashboard Web ativo em: http://{settings.web_host}:{settings.web_port}", flush=True)
        except Exception as e:
            print(f"⚠️ Não foi possível iniciar dashboard web na porta {settings.web_port}: {e}", flush=True)

    engine.sync_all()

    while running:
        for _ in range(settings.sync_interval):
            if not running:
                break
            time.sleep(1)
        if running:
            engine.sync_all()

    print("[*] OminiRTKSync encerrado.", flush=True)


def main():
    parser = argparse.ArgumentParser(
        prog="ominirtksync",
        description="OminiRTKSync · OmniRoute Universal Token & Connection Sync",
    )
    parser.add_argument("--db-path", dest="db_path", help="Caminho para o storage.sqlite do OmniRoute")
    parser.add_argument("--status", action="store_true", help="Exibe status das conexões do OmniRoute e sai")
    parser.add_argument("--once", action="store_true", help="Executa uma rodada única de sincronização e sai")
    parser.add_argument("--daemon", action="store_true", help="Executa em modo daemon perpétuo")
    parser.add_argument("--interval", type=int, help="Intervalo de checagem em segundos (padrão: 300)")
    parser.add_argument("--margin", type=int, help="Margem de renovação em segundos (padrão: 900)")
    parser.add_argument("--no-web", action="store_true", help="Desativa dashboard web")
    parser.add_argument("--port", type=int, help="Porta do dashboard web (padrão: 9191)")

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

    if args.status:
        print_status(settings)
        return

    if args.once:
        engine = OmniSyncEngine(settings)
        res = engine.sync_all()
        print(f"[*] Sincronização OmniRoute concluída: {res.get('total', 0)} conexões inspecionadas, {res.get('refreshed', 0)} renovadas.")
        return

    run_daemon(settings)


if __name__ == "__main__":
    main()
