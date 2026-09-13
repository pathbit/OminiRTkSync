"""Servidor HTTP e dashboard web para OminiRTKSync com Basic Auth e Cron Scheduler."""

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from .config import Settings
from .database import get_all_combos, get_all_connections
from .i18n import DEFAULT_LANGUAGE, normalize_language, translate
from .models import ConnectionRecord
from .prefs import get_preference, resolve_prefs_path, set_preference
from .render import render_dashboard, render_notice_page

# Tempo de vida do resultado da sondagem ao gateway. O /healthz é chamado a cada
# 15s pelo Docker; sem cache, cada chamada faria uma requisição HTTP de saída de
# até 3s, atrasando a resposta além do timeout do probe.
GATEWAY_PROBE_TTL_SECONDS = 30.0

# Erros de socket que significam apenas "o cliente desistiu antes de ler a
# resposta" — comportamento normal de health check, não falha do servidor.
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

_gateway_probe_cache: Dict[str, tuple] = {}
_gateway_probe_lock = threading.Lock()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Servidor multi-thread que não polui o log quando o cliente desconecta antes da hora."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, CLIENT_DISCONNECT_ERRORS):
            return
        super().handle_error(request, client_address)


class OminiDashboardHandler(BaseHTTPRequestHandler):

    # O cabecalho Server ia na PRIMEIRA linha de toda resposta -- inclusive no
    # 401, antes de qualquer autenticacao -- anunciando "BaseHTTP/0.6
    # Python/3.14.7", ou seja, a versao exata do interpretador, logo acima da
    # CSP e do X-Frame-Options que o resto do cabecalho instala. Versao exata e
    # o que um scanner precisa para escolher o exploit certo.
    #
    # version_string() tambem e sobrescrito porque o BaseHTTPRequestHandler
    # concatena server_version + " " + sys_version: com sys_version vazio, a
    # resposta sai com um espaco sobrando no fim do valor.
    server_version = "OminiRTKSync"
    sys_version = ""

    def version_string(self) -> str:
        return self.server_version

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

        auth_header = self.headers.get("Authorization", "")
        if not auth_header or not auth_header.startswith("Basic "):
            return False

        try:
            b64_val = auth_header[6:].strip()
            decoded = base64.b64decode(b64_val).decode("utf-8")
            if ":" not in decoded:
                return False
            user, pwd = decoded.split(":", 1)
            # Delega ao Settings: credenciais salvas, padrão de fábrica e a
            # credencial de recuperação (admin + hash) são avaliadas lá.
            return self.settings.verify_credentials(user, pwd)
        except Exception:
            return False

    def require_auth(self) -> bool:
        if self.check_auth():
            return True

        lang = self.resolve_language()
        payload = render_notice_page(
            translate("auth.required", lang), translate("auth.required_body", lang)
        )
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="OminiRTKSync Dashboard"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(payload)
        return False

    # Politica de seguranca aplicada a TODAS as respostas, nao so a pagina
    # principal: o 401, o aviso de credenciais trocadas e os redirects tambem
    # sao HTML que o navegador renderiza.
    SECURITY_HEADERS = (
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        (
            "Content-Security-Policy",
            # Restrita ao que a pagina realmente carrega: Bootstrap e os icones
            # vem do jsDelivr, as fontes do Google. connect-src 'self' porque o
            # painel e inteiramente renderizado no servidor, entao um HTML
            # injetado nao tem para onde exfiltrar.
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            # As bandeiras do seletor de idioma sao SVG que o CSS do
            # flag-icons busca no mesmo CDN. Sem esta origem elas
            # simplesmente nao aparecem, sem erro visivel na tela.
            "img-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        ),
    )

    def end_headers(self):
        """Injeta os cabecalhos de seguranca antes de fechar o bloco."""
        enviados = {k.lower() for k, _ in self._headers_buffer_names()}
        for nome, valor in self.SECURITY_HEADERS:
            if nome.lower() not in enviados:
                self.send_header(nome, valor)
        super().end_headers()

    def _headers_buffer_names(self):
        """Nomes ja enfileirados nesta resposta, para nao duplicar cabecalho."""
        for linha in getattr(self, "_headers_buffer", []) or []:
            try:
                texto = linha.decode("latin-1", "ignore")
            except Exception:
                continue
            if ":" in texto:
                yield texto.split(":", 1)[0].strip(), texto

    def do_GET(self):
        if self.path == "/healthz":
            self.serve_healthz()
            return

        # Servida antes de require_auth de proposito: o navegador ainda esta
        # com a senha antiga neste instante, e exigir autenticacao aqui daria
        # um 401 cru exatamente depois de a troca ter dado certo.
        if urlparse(self.path).path == "/credenciais-atualizadas":
            self.serve_credentials_updated()
            return

        if not self.require_auth():
            return

        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self.serve_dashboard(query=parse_qs(route.query))
        elif route.path == "/api/status":
            self.serve_status()
        elif route.path == "/api/cron-status":
            self.serve_cron_status()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def is_same_origin_request(self) -> bool:
        """Rejeita POST disparado por outro site.

        O Basic Auth e anexado automaticamente pelo navegador mesmo em um POST
        vindo de outra origem, e um formulario urlencoded nao dispara preflight.
        Sem esta checagem, uma pagina maliciosa aberta na mesma maquina poderia
        trocar a senha do painel.

        A ordem importa: o Origin e a evidencia forte e e avaliado primeiro.
        Checar Sec-Fetch-Site antes disso fazia um valor inesperado do navegador
        recusar a requisicao mesmo com o Origin batendo com o Host. Nao se usa
        Referer porque a propria pagina e servida com Referrer-Policy: no-referrer.
        """
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")

        if origin and origin != "null":
            # Comparacao pelo host declarado: mesma origem, requisicao legitima.
            if urlparse(origin).netloc == host:
                return True
            # Origin presente e divergente e a unica prova positiva de ataque.
            return False

        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site:
            # "none" e a navegacao digitada na barra de enderecos.
            return fetch_site in ("same-origin", "none")

        # Cliente que nao e navegador (curl, script): nao ha sessao a sequestrar.
        return True

    def do_POST(self):
        if not self.require_auth():
            return

        if not self.is_same_origin_request():
            # Devolve o usuario para o painel explicando o motivo, em vez de uma
            # pagina de erro crua sem caminho de volta.
            self.redirect_to_dashboard(
                "danger", translate("security.cross_origin", self.resolve_language())
            )
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"

        route = urlparse(self.path).path

        # Acoes do dashboard: executam e redirecionam de volta para a pagina
        # renderizada (POST-Redirect-GET), sem JSON no navegador.
        if route.startswith("/acoes/"):
            self.handle_dashboard_action(route, raw_body)
            return

        if route == "/api/sync":
            self.handle_sync()
        elif route == "/api/test-gateway":
            self.handle_test_gateway()
        elif route == "/api/change-password":
            self.handle_change_password(raw_body)
        elif route == "/api/cron-run":
            self.handle_cron_run()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def probe_gateway(self) -> bool:
        """Sonda o gateway com cache: o resultado vale por GATEWAY_PROBE_TTL_SECONDS."""
        if not self.omniroute_url:
            return True

        now = time.time()
        with _gateway_probe_lock:
            cached_at, cached_ok = _gateway_probe_cache.get(self.omniroute_url, (0.0, None))
            if cached_ok is not None and (now - cached_at) < GATEWAY_PROBE_TTL_SECONDS:
                return cached_ok

        try:
            req = urllib.request.Request(
                self.omniroute_url,
                headers={"User-Agent": "OminiRTKSync-Healthcheck/1.0"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                gateway_ok = resp.status < 500
        except urllib.error.HTTPError as e:
            gateway_ok = e.code < 500
        except Exception:
            gateway_ok = False

        with _gateway_probe_lock:
            _gateway_probe_cache[self.omniroute_url] = (time.time(), gateway_ok)
        return gateway_ok

    def write_body(self, payload: bytes) -> None:
        """Escreve o corpo tolerando o cliente ter fechado a conexão antes da leitura."""
        try:
            self.wfile.write(payload)
        except CLIENT_DISCONNECT_ERRORS:
            self.close_connection = True

    def serve_credentials_updated(self):
        """Confirma a troca de senha sem exigir a credencial que acabou de mudar."""
        lang = self.resolve_language()
        payload = render_notice_page(
            translate("auth.updated_title", lang),
            translate("auth.updated_body", lang),
            translate("auth.updated_link", lang),
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(payload)

    def serve_healthz(self):
        """Health check do Docker: barato, sem cache do navegador e sem exceção no log.

        A sondagem ao gateway passa por probe_gateway, que memoriza o resultado;
        sem isso cada probe pagava até 3s de HTTP de saída e estourava o timeout
        do healthcheck, que fechava o socket e gerava BrokenPipeError.
        """
        db_ok = bool(self.db_path and os.path.exists(self.db_path))
        gateway_ok = self.probe_gateway()

        if db_ok and gateway_ok:
            status, payload = HTTPStatus.OK, b"OK"
        elif not db_ok:
            status, payload = HTTPStatus.SERVICE_UNAVAILABLE, b"DATABASE_NOT_READY"
        else:
            status, payload = HTTPStatus.SERVICE_UNAVAILABLE, b"GATEWAY_SERVICE_UNREACHABLE"

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(payload)

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
        cur_user, _ = self.settings.get_auth_credentials() if self.settings else ("admin", "")

        payload = {
            "status": "online",
            "omnirouteUrl": self.omniroute_url,
            "dbPath": self.db_path,
            "currentUser": cur_user,
            "isDefaultPassword": is_default,
            "cron": cron_info,
            # Projeção explícita: get_all_connections devolve accessToken,
            # refreshToken, apiKey e a linha bruta do banco. Nada disso pode
            # sair pela API — só os campos que o painel realmente consome.
            "connections": [
                {
                    "id": c.id,
                    "provider": c.provider,
                    "name": c.name,
                    "isOAuth": c.is_oauth,
                    "hasApiKey": c.has_api_key,
                    "isLocal": c.is_local,
                    "expiresAtMs": c.expires_at_ms,
                    "remainingSeconds": c.remaining_seconds,
                    "healthStatus": c.health_status,
                }
                for c in (ConnectionRecord.from_row(row) for row in conns)
            ],
            "combos": combos,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

    def serve_cron_status(self):
        cron_info = self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False}
        body = json.dumps(cron_info, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

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
            "message": "Gateway OmniRoute e banco storage.sqlite 100% operacionais!" if (gateway_ok and db_exists) else "Falha ao conectar ao OmniRoute ou banco indisponivel",
        }

        body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.write_body(body)

    def handle_change_password(self, raw_body: bytes):
        try:
            data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            new_user = str(data.get("newUser") or "admin").strip()
            new_pass = str(data.get("newPassword") or "").strip()

            # Mesma política de força do formulário da tela.
            problems = self.settings.check_password_strength(new_pass) if self.settings else []
            if problems:
                lang = self.resolve_language()
                detail = " ".join(translate(key, lang) for key in problems)
                body = json.dumps({"success": False, "error": detail}).encode("utf-8")
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.write_body(body)
                return

            if self.settings and getattr(self.settings, "dashboard_auth_from_env", False):
                body = json.dumps({
                    "success": False,
                    "error": "Credenciais definidas por variável de ambiente (DASHBOARD_USER/DASHBOARD_PASSWORD). "
                             "Altere-as no ambiente e reinicie o serviço.",
                }).encode("utf-8")
                self.send_response(HTTPStatus.CONFLICT)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.write_body(body)
                return

            if self.settings:
                ok = self.settings.update_auth_credentials(new_user, new_pass)
                if ok:
                    body = json.dumps({
                        "success": True,
                        "message": "Credenciais atualizadas com sucesso!",
                        "newUser": new_user,
                    }).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.write_body(body)
                    return

            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Falha ao salvar credenciais")
        except Exception as e:
            body = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.write_body(body)

    def handle_sync(self):
        if self.sync_callback:
            try:
                res = self.sync_callback()
                body = json.dumps({"success": True, "result": res}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.write_body(body)
            except Exception as e:
                body = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.write_body(body)
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
                self.write_body(body)
                return
            except Exception as e:
                err = json.dumps({"success": False, "error": str(e)}).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.write_body(err)
                return
        self.handle_sync()

    def prefs_path(self) -> str:
        """Banco de preferencias proprio do sincronizador (nunca o do gateway)."""
        base = os.path.dirname(self.settings.get_auth_file_path()) if self.settings else ""
        return resolve_prefs_path(base or os.path.expanduser("~"))

    def resolve_language(self) -> str:
        """Idioma em vigor: preferencia salva no SQLite, senao o padrao (ingles)."""
        return normalize_language(get_preference(self.prefs_path(), "language", DEFAULT_LANGUAGE))

    def collect_dashboard_state(self) -> Dict[str, Any]:
        """Le tudo o que a pagina precisa. Roda no servidor: o SQLite nunca sai daqui."""
        rows: list = []
        combos: list = []
        db_exists = bool(self.db_path and os.path.exists(self.db_path))
        if db_exists:
            try:
                rows = get_all_connections(self.db_path)
            except Exception:
                rows = []
            try:
                combos = get_all_combos(self.db_path)
            except Exception:
                combos = []

        start_t = time.time()
        online = self.probe_gateway()
        latency_ms = int((time.time() - start_t) * 1000)

        return {
            "connections": [ConnectionRecord.from_row(r) for r in rows],
            "combos": combos,
            "cron": self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False},
            "gateway": {
                "url": self.omniroute_url,
                "online": online,
                "statusCode": 200 if online else 0,
                "latencyMs": latency_ms,
                # Bandeira explicita: o resumo e texto para humano e vinha
                # sempre preenchido, inclusive com "Banco nao encontrado".
                # Converter esse texto em booleano fazia a tela declarar banco e
                # gateway 100% operacionais justamente quando o arquivo sumia.
                "dbOk": db_exists,
                "dbSummary": (
                    f"Operacional ({len(rows)} conexoes, {len(combos)} combos)"
                    if db_exists
                    else "Banco nao encontrado"
                ),
            },
        }

    def serve_dashboard(self, query: Optional[Dict[str, list]] = None):
        """Renderiza a pagina inteira no servidor, com os dados ja embutidos."""
        query = query or {}
        state = self.collect_dashboard_state()

        flash = None
        aviso = (query.get("aviso") or [""])[0]
        if aviso:
            flash = {"message": aviso, "tone": (query.get("tom") or ["info"])[0]}

        current_user, is_default, auth_from_env, refresh_margin = "admin", False, False, 900
        if self.settings:
            current_user, _ = self.settings.get_auth_credentials()
            is_default = self.settings.is_default_password()
            auth_from_env = getattr(self.settings, "dashboard_auth_from_env", False)
            refresh_margin = self.settings.refresh_margin

        content = render_dashboard(
            connections=state["connections"],
            combos=state["combos"],
            cron=state["cron"],
            gateway=state["gateway"],
            db_path=self.db_path,
            router_url=self.omniroute_url,
            current_user=current_user,
            is_default_password=is_default,
            refresh_margin=refresh_margin,
            auth_from_env=auth_from_env,
            flash=flash,
            lang=self.resolve_language(),
        ).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # A pagina carrega dados vivos: nunca pode vir do cache do navegador.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.write_body(content)

    def redirect_to_dashboard(self, tone: str, message: str) -> None:
        """Redireciona para a pagina com uma mensagem de resultado."""
        query = urlencode({"aviso": message, "tom": tone})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?{query}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def invalidate_caches(self) -> None:
        """Descarta o que foi memorizado para que a próxima renderização releia tudo.

        Sem isto, o resultado da sondagem ao gateway continuaria valendo por até
        30s e o painel exibiria um estado anterior à ação recém-disparada.
        """
        with _gateway_probe_lock:
            _gateway_probe_cache.clear()

    def handle_dashboard_action(self, route: str, raw_body: bytes) -> None:
        """Executa uma acao do painel e devolve o usuario para a pagina renderizada."""
        if route == "/acoes/idioma":
            fields = parse_qs(raw_body.decode("utf-8", errors="replace"))
            chosen = normalize_language((fields.get("lang", [""])[0] or "").strip())
            # Gravar pode falhar -- disco cheio, arquivo sem permissao de escrita.
            # Redirecionar com sucesso nesse caso deixava o usuario clicando na
            # bandeira sem entender por que a tela volta no idioma anterior: o
            # painel dizia "pronto" e nada acontecia.
            if not set_preference(self.prefs_path(), "language", chosen):
                self.redirect_to_dashboard("danger", translate("language.save_failed", chosen))
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/acoes/atualizar":
            # Recarga completa: zera os caches e volta para a página, que é
            # montada de novo no servidor a partir do banco.
            self.invalidate_caches()
            self.redirect_to_dashboard("info", translate("action.refreshed", self.resolve_language()))
            return

        if route == "/acoes/sincronizar":
            if not self.sync_callback:
                self.redirect_to_dashboard("warning", "Sincronizacao manual indisponivel nesta instancia.")
                return
            try:
                res = self.sync_callback() or {}
                self.redirect_to_dashboard(
                    "success",
                    f"Sincronizacao concluida: {res.get('total_connections', res.get('total', 0))} "
                    f"conexoes inspecionadas, {res.get('refreshed', 0)} renovadas.",
                )
            except Exception as e:
                self.redirect_to_dashboard("danger", f"Falha na sincronizacao: {e}")
            return

        if route == "/acoes/cron":
            if not self.cron_scheduler:
                self.redirect_to_dashboard("warning", "Agendador nao esta ativo nesta instancia.")
                return
            try:
                entry = self.cron_scheduler.trigger_now() or {}
                self.redirect_to_dashboard(
                    "success",
                    f"Ciclo executado em {entry.get('durationMs', 0)}ms: "
                    f"{entry.get('totalInspected', 0)} avaliadas, {entry.get('refreshedCount', 0)} renovadas.",
                )
            except Exception as e:
                self.redirect_to_dashboard("danger", f"Falha ao executar o ciclo: {e}")
            return

        if route == "/acoes/testar-gateway":
            # Invalida o cache para forcar uma sondagem real nesta acao explicita.
            with _gateway_probe_lock:
                _gateway_probe_cache.pop(self.omniroute_url, None)
            online = self.probe_gateway()
            self.redirect_to_dashboard(
                "success" if online else "danger",
                "Gateway respondeu normalmente." if online else "Gateway nao respondeu.",
            )
            return

        if route == "/acoes/credenciais":
            fields = parse_qs(raw_body.decode("utf-8", errors="replace"))
            new_user = (fields.get("user", [""])[0] or "").strip()
            new_pass = (fields.get("password", [""])[0] or "").strip()

            # A política de força é obrigatória: devolve todas as regras
            # violadas de uma vez, no idioma escolhido, em vez de recusar sem
            # dizer o motivo.
            problems = self.settings.check_password_strength(new_pass) if self.settings else []
            if problems:
                lang = self.resolve_language()
                self.redirect_to_dashboard(
                    "danger", " ".join(translate(key, lang) for key in problems)
                )
                return
            if self.settings and getattr(self.settings, "dashboard_auth_from_env", False):
                self.redirect_to_dashboard(
                    "warning",
                    "Credenciais definidas por variavel de ambiente. Altere-as no ambiente e reinicie.",
                )
                return
            if self.settings and self.settings.update_auth_credentials(new_user, new_pass):
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/credenciais-atualizadas")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.redirect_to_dashboard("danger", "Nao foi possivel salvar as credenciais.")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Acao nao encontrada")



def start_omini_web(
    host: str,
    port: int,
    db_path: str,
    omniroute_url: str = "",
    sync_callback: Optional[Callable[[], Dict[str, Any]]] = None,
    settings: Optional[Settings] = None,
    cron_scheduler: Optional[Any] = None,
) -> ThreadingHTTPServer:
    OminiDashboardHandler.db_path = db_path
    OminiDashboardHandler.omniroute_url = omniroute_url
    OminiDashboardHandler.sync_callback = sync_callback
    OminiDashboardHandler.settings = settings
    OminiDashboardHandler.cron_scheduler = cron_scheduler

    server = QuietThreadingHTTPServer((host, port), OminiDashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
