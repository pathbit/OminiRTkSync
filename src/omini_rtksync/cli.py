"""CLI e orquestrador do OminiRTKSync para OmniRoute."""

import argparse
import os
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List

from .config import Settings
from .credential_check import STATE_INVALID, STATE_VALID, check_oauth_token
from .logs import get_logger, setup_logging
from .cron import CronScheduler
from .database import (
    get_all_combos,
    get_all_connections,
    normalize_expiry_format,
    update_connection,
    update_connection_health,
)
from .discovery import HostDiscoveryEngine
from .normalizer import parse_expiry_to_ms
from .providers import ApiKeyProvider, GenericOAuthProvider, GoogleProvider, LocalProvider
from .web import start_omini_web


# Prefixos que descrevem falha. Emitir tudo em INFO fazia com que
# LOG_LEVEL=WARNING escondesse justamente os eventos que motivaram o log
# persistente: quem sobe o nível para reduzir ruído perdia toda falha.
PREFIXOS_DE_ERRO = {"FALHA", "ERRO", "ERROR", "FAILURE"}
PREFIXOS_DE_AVISO = {"AVISO", "WARN", "WARNING"}


def log_msg(prefix: str, text: str):
    """Registra um evento no log persistente (e no stdout, se LOG_TO_STDOUT permitir).

    O nível segue o prefixo: falha vai como ERROR, aviso como WARNING, o resto
    como INFO.
    """
    logger = get_logger()
    mensagem = f"[{prefix}] {text}"
    alvo = str(prefix).upper()
    if alvo in PREFIXOS_DE_ERRO:
        logger.error(mensagem)
    elif alvo in PREFIXOS_DE_AVISO:
        logger.warning(mensagem)
    else:
        logger.info(mensagem)


class OmniSyncEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        # O botao da tela e o cron chamam esta mesma instancia, e o servidor web
        # atende cada requisicao em uma thread. Sem isto, duas renovacoes OAuth
        # concorrentes podem sobrescrever um token recem-rotacionado.
        self._sync_lock = threading.Lock()
        self.discovery = HostDiscoveryEngine(
            host_home=settings.host_home,
            extra_paths=settings.credential_paths,
        )
        self.google_provider = GoogleProvider(credential_paths=settings.credential_paths, discovery=self.discovery)
        self.oauth_provider = GenericOAuthProvider(discovery=self.discovery)
        self.api_provider = ApiKeyProvider(
            discovery=self.discovery,
            validate_credentials=settings.validate_credentials,
            validation_timeout=settings.validation_timeout,
        )
        self.local_provider = LocalProvider()

    def sync_all(self):
        """Executa um ciclo completo, serializado.

        Uma execucao manual pela tela e uma execucao agendada nunca podem se
        sobrepor, ou duas renovacoes OAuth concorrentes sobrescrevem o token uma
        da outra.
        """
        with self._sync_lock:
            return self._sync_all_locked()

    def _sync_all_locked(self):
        if not os.path.exists(self.settings.db_path):
            log_msg("AVISO", f"Aguardando banco do OmniRoute em: {self.settings.db_path}")
            return {"success": False, "error": "db_not_found"}

        conns = get_all_connections(self.settings.db_path)
        log_msg("INFO", f"Inspecionando {len(conns)} conexões no OmniRoute ({self.settings.db_path})...")

        refreshed = 0
        normalized = 0
        now_ms = int(time.time() * 1000)
        detalhes: List[Dict[str, Any]] = []
        erros: List[str] = []

        for c in conns:
            provider = c["provider"]
            cid = c["id"]
            name = c["name"]
            # O histórico da tela consome esta lista. Sem ela, todo ciclo bem
            # sucedido aparecia com log vazio e "nenhum ciclo executado ainda".
            detalhe: Dict[str, Any] = {
                "id": cid,
                "provider": provider,
                "name": name,
                "actions": [],
            }
            detalhes.append(detalhe)

            # Cura o formato de expiração para QUALQUER provedor OAuth, não só
            # para o ramo do Antigravity. Um epoch numérico em texto é Invalid
            # Date para o OmniRoute; se a renovação falhar — refresh token
            # revogado, client credentials ausentes — o valor ilegível
            # permanecia para sempre justamente no caso em que mais importa.
            bruto_expiracao = str(c.get("expiresAt") or "")
            if bruto_expiracao.isdigit():
                exp_curado = parse_expiry_to_ms(c.get("expiresAt"))
                if exp_curado and normalize_expiry_format(self.settings.db_path, cid, exp_curado):
                    normalized += 1
                    nota = "expires_at normalizado para ISO-8601"
                    log_msg("STATUS", f"[{provider} · {name}] {nota}")
                    detalhe["actions"].append(nota)

            # 1. Google / Antigravity OAuth
            if provider in ("antigravity", "gemini-cli"):
                local = self.google_provider.read_local_credential()
                ref_tok = c.get("refreshToken")
                if not ref_tok and local:
                    ref_tok = local.get("refresh_token")

                exp_ms = parse_expiry_to_ms(c.get("expiresAt"))
                rem_sec = int((exp_ms - now_ms) / 1000) if exp_ms else 0

                # Pergunta ao Google se o token ainda vale, em vez de deduzir
                # isso da validade guardada. Um token revogado cuja expiração
                # gravada ainda está no futuro continuava sendo exibido como
                # ativo — que é exatamente o caso que o painel precisa mostrar.
                if self.settings.validate_credentials and c.get("accessToken"):
                    veredito = check_oauth_token(
                        str(c.get("accessToken")), timeout=self.settings.validation_timeout
                    )
                    if veredito.state == STATE_INVALID:
                        nota = f"Token de acesso RECUSADO pelo Google ({veredito.detail})"
                        log_msg("FALHA", f"[{provider} · {name}] {nota}")
                        detalhe["actions"].append(nota)
                        update_connection_health(
                            self.settings.db_path,
                            cid,
                            test_status="invalid",
                            credential_state=veredito.state,
                            last_error=veredito.detail,
                        )
                        # Recusado é motivo para renovar agora, não daqui a pouco.
                        rem_sec = 0
                    elif veredito.state == STATE_VALID:
                        update_connection_health(
                            self.settings.db_path, cid, credential_state=veredito.state
                        )

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
                            detalhe["actions"].append(f"OAuth renovado ({exp_in}s)")
                            log_msg("SUCESSO", f"[{provider} · {name}] OAuth renovado com sucesso ({exp_in}s)")
                            continue
                        else:
                            nota = f"Erro ao renovar OAuth: {err}"
                            log_msg("FALHA", f"[{provider} · {name}] {nota}")
                            detalhe["actions"].append(nota)
                            erros.append(f"[{provider} · {name}] {nota}")
                else:
                    log_msg("OK", f"[{provider} · {name}] Token válido por mais {rem_sec // 60} min")
                continue

            # 2. Demais Provedores OAuth (Claude, GitHub, Codex, Kiro)
            if self.oauth_provider.can_handle(c):
                mod, data, notes = self.oauth_provider.check_and_refresh(c, margin_seconds=self.settings.refresh_margin)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                    detalhe["actions"].append(note)
                if mod and data:
                    update_connection(
                        self.settings.db_path,
                        cid,
                        access_token=data["accessToken"],
                        refresh_token=data.get("refreshToken", c.get("refreshToken", "")),
                        expires_at_ms=data.get("expiresAt", now_ms + 3600000),
                    )
                    refreshed += 1
                    detalhe["actions"].append("Credenciais OAuth atualizadas")
                    log_msg("SUCESSO", f"[{provider} · {name}] Credenciais OAuth atualizadas no storage.sqlite")
                continue

            # 3. Provedores de API Key (Groq, Mistral, OpenRouter, Gemini, OpenAI, etc.)
            if self.api_provider.can_handle(c):
                renovou, data, notes = self.api_provider.check_and_refresh(c)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                    detalhe["actions"].append(note)
                if data:
                    # O resultado da sondagem tem de ir para o banco. Sem isto o
                    # painel recarregava a linha antiga e uma chave recusada
                    # continuava verde na tela.
                    update_connection_health(
                        self.settings.db_path,
                        cid,
                        test_status=data.get("testStatus"),
                        credential_state=data.get("credentialState"),
                        last_error=data.get("lastError"),
                        # O provider ja apagou a trava vencida de `data`, entao
                        # inferir "limpar" da ausencia dela invertia o sentido e
                        # preservava justamente a trava que devia sair. Quem diz
                        # e a mensagem do provider.
                        clear_rate_limit=any("rateLimitedUntil" in n for n in notes),
                    )
                if renovou:
                    refreshed += 1
                    log_msg("SUCESSO", f"[{provider} · {name}] Chave de API sincronizada no storage.sqlite")
                continue

            # 4. Provedores Locais (Ollama, proxies locais)
            if self.local_provider.can_handle(c):
                _, data, notes = self.local_provider.check_and_refresh(c)
                for note in notes:
                    log_msg("STATUS", f"[{provider} · {name}] {note}")
                    detalhe["actions"].append(note)
                if data:
                    update_connection_health(
                        self.settings.db_path,
                        cid,
                        test_status=data.get("testStatus"),
                        discovered_models=data.get("discoveredModels"),
                        last_error=data.get("lastError"),
                    )
                continue

            log_msg("INFO", f"[{provider} · {name}] Conexão preservada sem pendências")

        return {
            "success": not erros,
            "total": len(conns),
            "refreshed": refreshed,
            "normalized": normalized,
            # O histórico por execução da tela lê estes dois campos.
            "details": detalhes,
            "errors": erros,
        }


def print_status(settings: Settings):
    try:
        conns = get_all_connections(settings.db_path)
        combos = get_all_combos(settings.db_path)
    except Exception as e:
        print(f"[ERRO] Erro ao consultar banco SQLite ({settings.db_path}): {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 74)
    print("[*] OMINIRTKSYNC · STATUS DAS CONEXÕES DO OMNIROUTE")
    print(f"   Banco de Dados: {settings.db_path}")
    print("=" * 74)

    print(f"\n[*] Conexões Registradas ({len(conns)}):")
    print(f"  {'PROVEDOR':<16} {'NOME':<26} {'TIPO':<10} {'STATUS':<10}")
    print("  " + "-" * 72)

    for c in conns:
        tipo = "OAuth 2.0" if c["isOAuth"] else ("API Key" if c["hasApiKey"] else "Outro")
        st = c.get("testStatus", "ativo")
        print(f"  {c['provider']:<16} {c['name'][:25]:<26} {tipo:<10} [ok] {st:<8}")

    if combos:
        print(f"\n[*] Combos Cadastrados ({len(combos)}):")
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
    print("[*] OMINIRTKSYNC · OMNIROUTE UNIVERSAL TOKEN & CONNECTION SYNCHRONIZER", flush=True)
    print(f"   Banco SQLite: {settings.db_path}", flush=True)
    print(f"   Gateway URL:  {settings.omniroute_url}", flush=True)
    print(f"   Host Home:    {engine.discovery.host_home}", flush=True)
    print("=" * 74, flush=True)

    # Varredura inicial de credenciais disponíveis no host
    discovered = engine.discovery.discover_all()
    found_any = False
    for prov, info in discovered.items():
        if info:
            found_any = True
            log_msg("DISCOVERY", f"Credencial detectada no host: [{prov}] -> {info.get('source_path')}")
    if not found_any:
        log_msg("DISCOVERY", f"Nenhuma credencial local pré-existente em {engine.discovery.host_home}")

    cron_scheduler = CronScheduler(
        sync_callback=engine.sync_all,
        interval_seconds=settings.cron_interval,
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
            print(f"[*] Dashboard Web ativo em: http://{settings.web_host}:{settings.web_port}", flush=True)
        except Exception as e:
            print(f"[!] Não foi possível iniciar dashboard web na porta {settings.web_port}: {e}", flush=True)

    if settings.cron_enabled:
        cron_scheduler.start()
    else:
        print("[*] Agendador automatico desativado (CRON_ENABLED=0); use o disparo manual.", flush=True)

    while running:
        time.sleep(1)

    cron_scheduler.stop()
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
    parser.add_argument("--port", type=int, help="Porta do dashboard web (padrão: 9090)")
    parser.add_argument("--user", type=str, help="Usuário para autenticação no dashboard web (padrão: admin)")
    parser.add_argument("--password", type=str, help="Senha para autenticação no dashboard web (padrão: pathbit)")

    args = parser.parse_args()
    settings = Settings.from_env()

    # Log em arquivo precisa existir antes de qualquer evento do motor de sincronizacao.
    logger = setup_logging(settings.db_path)

    # Credencial de emergencia: gerada uma unica vez, para o operador conseguir
    # voltar ao painel caso esqueca a senha trocada pela tela.
    #
    # O valor NAO vai para o log. Ele e uma credencial funcional, e o stdout do
    # container costuma ser coletado, encaminhado e lido por muita gente; fica
    # apenas no arquivo com modo 0600, e o log diz onde encontra-lo.
    recovery_hash, generated_now = settings.ensure_recovery_hash()
    if generated_now and recovery_hash:
        logger.warning(
            "[AUTH] Credencial de recuperacao gerada para o usuario 'admin'. Leia com: "
            "docker exec <container> cat %s  (ou fixe a sua com DASHBOARD_RECOVERY_HASH)",
            settings.get_recovery_file_path(),
        )

    if args.db_path:
        settings.db_path = args.db_path
    if args.interval:
        settings.sync_interval = args.interval
        # O agendador le cron_interval, ja derivado do ambiente antes de as
        # opcoes chegarem aqui: sem esta linha --interval era um no-op no cron.
        settings.cron_interval = args.interval
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
        print(f"[*] Sincronização OmniRoute concluída: {res.get('total', 0)} conexões inspecionadas, {res.get('refreshed', 0)} renovadas.")
        return

    run_daemon(settings)


if __name__ == "__main__":
    main()
