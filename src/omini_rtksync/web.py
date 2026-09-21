"""Painel deste sincronizador, renderizado inteiramente no servidor.

Este módulo cuida do transporte — rotas, autenticação, cabeçalhos e ações. Todo
o HTML vive em `render.py` e tudo o que sabe de QUAL gateway se trata vive em
`gateway.py`: foi a mistura dessas três coisas num arquivo só que fez os painéis
irmãos divergirem sem que ninguém percebesse.

Mesma postura nos três, pelas mesmas razões:

- nada de JavaScript buscando dado: a página chega pronta, então não existe
  endpoint público servindo estado do gateway;
- cabeçalhos de segurança em **toda** resposta, inclusive no corpo do 401 — que
  é o que o navegador mostra quando se aperta ESC no diálogo do Basic Auth;
- nenhuma credencial aparece em corpo de resposta, log ou banner;
- POST de outra origem é recusado, porque o navegador anexa o Basic Auth
  sozinho num formulário de terceiro;
- rota que este servidor não serve responde 404 ANTES de qualquer exigência de
  sessão: "você precisa entrar" e "isso não existe" são respostas diferentes
  para perguntas diferentes.
"""

import base64
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from .config import Settings
from .i18n import DEFAULT_LANGUAGE, LANGUAGES, normalize_language, translate
from .identidade import NOME_DO_GATEWAY, NOME_DO_PRODUTO
from .logs import get_logger
from .prefs import get_preference, set_preference
from . import protecao, sessao, sso
from .render import render_dashboard, render_login_page, render_notice_page
from .gateway import (
    get_all_api_keys,
    get_all_combos,
    get_all_connections,
    get_all_registered_models,
)
from .models import ConnectionRecord, RegisteredModelRecord, VirtualKeyRecord

# Tempo de vida do resultado da sondagem ao gateway. O /healthz é chamado a cada
# 15s pelo Docker; sem cache, cada chamada faria uma requisição HTTP de saída de
# até 3s, atrasando a resposta além do timeout do probe.
GATEWAY_PROBE_TTL_SECONDS = 30.0

# Erros de socket que significam apenas "o cliente desistiu antes de ler a
# resposta" — comportamento normal de health check, não falha do servidor.
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

# Cache da sondagem ao gateway, compartilhado entre as threads do servidor.
_gateway_probe_cache: Dict[str, tuple] = {}
_gateway_probe_lock = threading.Lock()


def strip_markup(text: str) -> str:
    """Tira as etiquetas de um texto do catálogo destinado a virar aviso puro."""
    return re.sub(r"<[^>]+>", "", text)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Servidor multi-thread que não polui o log quando o cliente desconecta antes da hora."""

    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, CLIENT_DISCONNECT_ERRORS):
            return
        super().handle_error(request, client_address)


class DashboardHandler(BaseHTTPRequestHandler):
    """Rotas do painel, da autenticação à renderização, sem uma linha de HTML."""

    # O cabeçalho Server ia na PRIMEIRA linha de toda resposta -- inclusive no
    # 401, antes de qualquer autenticação -- anunciando "BaseHTTP/0.6
    # Python/3.14.7", ou seja, a versão exata do interpretador, logo acima da
    # CSP e do X-Frame-Options que o resto do cabeçalho instala. Versão exata é
    # o que um scanner precisa para escolher o exploit certo.
    #
    # version_string() também é sobrescrito porque o BaseHTTPRequestHandler
    # concatena server_version + " " + sys_version: com sys_version vazio, a
    # resposta sai com um espaço sobrando no fim do valor.
    server_version = NOME_DO_PRODUTO
    sys_version = ""

    def version_string(self) -> str:
        return self.server_version

    settings: Optional[Settings] = None
    cron_scheduler: Optional[Any] = None
    # Quem entrou nesta requisição. Só o nome: a senha morre na conferência, e
    # guardar o par inteiro a deixaria ao alcance de qualquer trecho de
    # renderização.
    authenticated_user: str = ""
    db_path: str = ""
    router_url: str = ""
    sync_callback: Optional[Callable[[], Dict[str, Any]]] = None

    def log_message(self, format, *args):
        # O log de acesso do http.server escreve em stderr sem passar pelo
        # logger, e carrega a linha de requisição inteira. Silenciado.
        return

    # -- autenticação -------------------------------------------------------

    def check_auth(self) -> bool:
        """Duas portas, e elas NÃO servem ao mesmo visitante.

        O cookie é a porta do navegador, e é a única que tem tranca do lado de
        dentro: "Sair" apaga o cookie e acabou. O Basic Auth não tem logout --
        o navegador guarda a credencial e a reenvia sozinho até a janela
        fechar, e não existe cabeçalho que mande ele esquecer. Enquanto a
        navegação aceitava Basic, o botão Sair apagava o cookie e a próxima
        visita entrava de novo pela outra porta: o botão mentia.

        Por isso quem pede HTML (um navegador) precisa de SESSÃO, e só. Quem
        não pede HTML -- curl, script, monitoramento -- continua com Basic
        Auth, que é o esquema que essas ferramentas sabem usar sem guardar
        estado, e para as quais "sair" não quer dizer nada.
        """
        if not self.settings:
            return True

        usuario = sessao.usuario_da_sessao(
            sessao.ler_do_cabecalho(self.headers.get("Cookie", ""))
        )
        if usuario:
            self.authenticated_user = usuario
            return True

        if "text/html" in self.headers.get("Accept", ""):
            return False

        cabecalho = self.headers.get("Authorization", "")
        if not cabecalho.startswith("Basic "):
            return False
        try:
            decodificado = base64.b64decode(cabecalho[6:].strip()).decode("utf-8")
        except Exception:
            return False
        if ":" not in decodificado:
            return False
        user, password = decodificado.split(":", 1)
        # Delega ao Settings: credenciais salvas, padrão de fábrica e a
        # credencial de recuperação são avaliadas lá, num lugar só.
        if not self.settings.verify_credentials(user, password):
            return False
        self.authenticated_user = user
        return True

    def require_auth(self) -> bool:
        """Deixa passar quem tem sessão ou Basic válido; responde o resto sozinha."""
        if self.check_auth():
            return True

        lang = self.resolve_language()

        # Quem pediu HTML é um navegador: mandamos para o formulário, que é
        # página nossa -- traduzida, com a cara do painel e com logout. O 401
        # com WWW-Authenticate fica para quem NÃO pediu HTML (curl, scripts,
        # monitoramento), que é quem sabe responder a ele.
        if "text/html" in self.headers.get("Accept", "") and urlparse(self.path).path != "/login":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        corpo = render_notice_page(
            translate("auth.required", lang), translate("auth.required_body", lang)
        )
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", f'Basic realm="{NOME_DO_PRODUTO}"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)
        return False

    def is_same_origin_request(self) -> bool:
        """Recusa POST disparado por outro site.

        O Basic Auth é anexado automaticamente pelo navegador mesmo num POST
        vindo de outra origem, e um formulário urlencoded não dispara preflight.
        Sem esta checagem, uma página maliciosa aberta na mesma máquina poderia
        trocar a senha do painel.

        A ordem importa: o Origin é a evidência forte e é avaliado primeiro.
        Checar Sec-Fetch-Site antes disso fazia um valor inesperado do navegador
        recusar a requisição mesmo com o Origin batendo com o Host. Não se usa
        Referer porque a própria página é servida com Referrer-Policy: no-referrer.
        """
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")

        if origin and origin != "null":
            # Comparação pelo host declarado: mesma origem, requisição legítima.
            # Origin presente e divergente é a única prova positiva de ataque.
            return urlparse(origin).netloc == host

        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site:
            # "none" é a navegação digitada na barra de endereços.
            return fetch_site in ("same-origin", "none")

        # Cliente que não é navegador (curl, script): não há sessão a sequestrar.
        return True

    # -- cabeçalhos ---------------------------------------------------------

    # Política de segurança aplicada a TODAS as respostas, não só à página
    # principal: o 401, o aviso de credenciais trocadas e os redirects também
    # são HTML que o navegador renderiza.
    SECURITY_HEADERS = (
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        (
            "Content-Security-Policy",
            # Restrita ao que a página realmente carrega: o Bootstrap e os
            # ícones vêm do jsDelivr, as fontes do Google. connect-src 'self'
            # porque o painel é inteiramente renderizado no servidor, então um
            # HTML injetado não tem para onde exfiltrar.
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            # As bandeiras do seletor de idioma são SVG que o CSS do
            # flag-icons busca no mesmo CDN. Sem esta origem elas simplesmente
            # não aparecem, e não há erro visível na tela para denunciar a falta.
            "img-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        ),
    )

    def end_headers(self):
        """Injeta os cabeçalhos de segurança antes de fechar o bloco.

        Aqui e não na rota: o 401 e a página de aviso também são HTML que o
        navegador renderiza, e emiti-los só na página principal deixava
        justamente essas duas sem proteção alguma.
        """
        enviados = {nome for nome, _ in self._headers_buffer_names()}
        for nome, valor in self.SECURITY_HEADERS:
            if nome.lower() not in enviados:
                self.send_header(nome, valor)
        super().end_headers()

    def _headers_buffer_names(self):
        """Nomes já enfileirados nesta resposta, para não duplicar cabeçalho."""
        for linha in getattr(self, "_headers_buffer", []) or []:
            try:
                texto = linha.decode("latin-1", "ignore")
            except Exception:
                continue
            if ":" in texto:
                yield texto.split(":", 1)[0].strip().lower(), texto

    # -- rotas --------------------------------------------------------------

    # Rotas que este servidor conhece. Serve para uma só decisão, tomada ANTES
    # de exigir sessão: o que não está aqui é 404, e não um convite a fazer
    # login para depois descobrir que a página nunca existiu.
    #
    # Rota REAL e protegida continua mandando para /login -- é a diferença entre
    # "você precisa entrar" e "isso não existe".
    ROTAS_CONHECIDAS = {
        "/", "/index.html", "/healthz", "/login", "/logout", "/robots.txt",
        "/favicon.ico", "/credenciais-atualizadas",
    }
    PREFIXOS_CONHECIDOS = ("/api/", "/acoes/")

    def rota_existe(self, caminho: str) -> bool:
        if caminho in self.ROTAS_CONHECIDAS or caminho.startswith(self.PREFIXOS_CONHECIDOS):
            return True
        # SEM CONFIGURAÇÃO, NADA MUDA: as rotas do acesso federado só existem
        # quando há provedor configurado E ligado. Desligado, elas devolvem 404
        # pelo mesmo caminho de qualquer outra rota que nunca existiu -- um
        # painel que nunca ligou SSO não tem nem superfície nova para sondar.
        return caminho.startswith("/sso/") and self.sso_esta_ligado()

    def recusa_rota_desconhecida(self, caminho: str) -> bool:
        """Devolve True e responde 404 quando a rota não existe neste servidor."""
        if self.rota_existe(caminho):
            return False
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return True

    def do_GET(self):
        # Uma leitura de configuração por requisição: a conexão pode ser
        # reaproveitada, e uma configuração salva no pedido anterior tem de
        # valer no seguinte.
        self._configuracao_sso = None
        path = urlparse(self.path).path
        if self.recusa_rota_desconhecida(path):
            return
        if path == "/healthz":
            self.serve_healthz()
            return

        # Público de propósito, e servido antes da sessão: um rastreador não tem
        # credencial, e a única forma de ele ler a regra é ela não exigir uma. O
        # painel não deve aparecer em índice de busca nenhum.
        if path == "/robots.txt":
            corpo = b"User-agent: *\nDisallow: /\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.write_body(corpo)
            return

        # A página de login é pública por definição: exigir sessão para exibir o
        # formulário que cria a sessão seria um círculo fechado.
        if path == "/login":
            query = parse_qs(urlparse(self.path).query)
            mensagem = ""
            if "logout" in query:
                mensagem = translate("auth.logged_out", self.resolve_language())
            self.serve_login_page(mensagem=mensagem)
            return

        # Servida ANTES do require_auth de propósito: o navegador ainda está com
        # a senha antiga neste instante, e exigir autenticação aqui daria um 401
        # cru exatamente depois de a troca ter dado certo.
        if path == "/credenciais-atualizadas":
            self.serve_credentials_updated()
            return

        # A ida ao provedor de identidade e a volta dele acontecem SEM sessão --
        # é a sessão que elas existem para criar, e quem chama a volta é o
        # provedor, que não tem cookie nosso para apresentar. Todas passam pelo
        # MESMO teto por endereço do formulário de login, e todas só existem
        # quando o acesso federado está ligado (ver `rota_existe`).
        if path == "/sso/oidc/iniciar":
            self.inicia_oidc()
            return
        if path == "/sso/oidc/callback" or path.rstrip("/") == "/sso/callback":
            self.recebe_oidc()
            return

        if path == "/sso/saml/iniciar":
            self.inicia_saml()
            return

        if path == "/favicon.ico":
            self.serve_favicon()
            return

        if not self.require_auth():
            return

        if path in ("/", "/index.html"):
            self.serve_dashboard(parse_qs(urlparse(self.path).query))
        elif path == "/api/status":
            self.serve_api_status()
        elif path == "/sso/saml/metadata":
            # Protegida de propósito: ver serve_saml_metadata.
            self.serve_saml_metadata()
        elif path == "/api/cron-status":
            self.serve_cron_status()
        elif path == "/logout":
            self.handle_logout()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        self._configuracao_sso = None
        rota_inicial = urlparse(self.path).path
        if rota_inicial == "/login":
            self.handle_login()
            return
        if rota_inicial == "/logout":
            self.handle_logout()
            return

        if rota_inicial == "/sso/saml/acs":
            self.recebe_saml()
            return

        if not self.require_auth():
            return

        if not self.is_same_origin_request():
            # Redireciona com aviso em vez de devolver um 403 cru: o operador
            # precisa entender o que houve, e um 403 na tela depois de tentar
            # trocar a senha parece um defeito do painel.
            self.redirect_to_dashboard(
                "danger", translate("security.cross_origin", self.resolve_language())
            )
            return

        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        campos = parse_qs(corpo.decode("utf-8", errors="replace"))

        route = urlparse(self.path).path
        # Ações do painel: executam e redirecionam de volta para a página
        # renderizada (POST-Redirect-GET), sem JSON no navegador. O aviso viaja
        # na querystring e o jQuery do `render.py` o apaga da barra de endereços
        # assim que a página desenha -- senão o F5 traria de volta a mensagem de
        # algo que já aconteceu.
        if route.startswith("/acoes/"):
            self.handle_dashboard_action(route, campos)
            return
        if route == "/api/sync":
            self.handle_sync_request()
        elif route == "/api/test-gateway":
            self.handle_test_gateway()
        elif route == "/api/change-password":
            self.handle_change_password(corpo)
        elif route == "/api/cron-run":
            self.handle_api_cron_run()
        elif route == "/api/sso/test-oidc":
            self.handle_sso_test_oidc(corpo)
        elif route == "/api/sso/test-saml":
            self.handle_sso_test_saml(corpo)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    # -- respostas ----------------------------------------------------------

    def write_body(self, corpo: bytes) -> None:
        """Escreve o corpo tolerando o cliente ter fechado a conexão antes da leitura."""
        try:
            self.wfile.write(corpo)
        except CLIENT_DISCONNECT_ERRORS:
            # O navegador fechou antes de ler. Não é erro do servidor, e deixar
            # subir enchia o log de traceback a cada recarga cancelada.
            self.close_connection = True

    def respond_html(self, corpo: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        """Resposta HTML completa: Content-Length montado antes de qualquer escrita."""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def respond_json(self, corpo: bytes) -> None:
        """Resposta JSON. Nunca é cacheável: o que ela carrega é estado vivo."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def redirect_to_dashboard(self, tone: str, message: str) -> None:
        """Volta para a página com uma mensagem de resultado (POST-Redirect-GET)."""
        query = urlencode({"aviso": message, "tom": tone})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?{query}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def responde_429(self, espere_segundos: int) -> None:
        """Pedidos demais: 429 com Retry-After, que é o que um cliente correto lê."""
        lang = self.resolve_language()
        corpo = render_notice_page(
            translate("auth.too_many", lang),
            translate("auth.too_many_body", lang, seconds=espere_segundos),
        )
        self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        self.send_header("Retry-After", str(espere_segundos))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def serve_credentials_updated(self) -> None:
        """Confirma a troca de senha sem exigir a credencial que acabou de mudar."""
        lang = self.resolve_language()
        self.respond_html(
            render_notice_page(
                translate("auth.updated_title", lang),
                translate("auth.updated_body", lang),
                translate("auth.updated_link", lang),
            )
        )

    def serve_cron_status(self) -> None:
        """Estado do agendador. O histórico só traz contagens e ações, nunca credencial."""
        cron = self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False}
        self.respond_json(json.dumps(cron, ensure_ascii=False, indent=2).encode("utf-8"))

    def serve_healthz(self) -> None:
        """Health check do Docker: barato, sem cache do navegador e sem exceção no log.

        A sondagem ao gateway passa por probe_gateway, que memoriza o resultado;
        sem isso cada probe pagava até 3s de HTTP de saída e estourava o timeout
        do healthcheck, que fechava o socket e gerava BrokenPipeError.
        """
        db_ok = bool(self.db_path and os.path.exists(self.db_path))
        gateway_ok = self.probe_gateway()

        if db_ok and gateway_ok:
            status, corpo = HTTPStatus.OK, b"OK"
        elif not db_ok:
            status, corpo = HTTPStatus.SERVICE_UNAVAILABLE, b"DATABASE_NOT_READY"
        else:
            status, corpo = HTTPStatus.SERVICE_UNAVAILABLE, b"GATEWAY_SERVICE_UNREACHABLE"

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    # -- entrada ------------------------------------------------------------


    def serve_favicon(self) -> None:
        """204: o ícone da aba vem do data URI embutido, não de um arquivo.

        Servir o SVG por aqui o deixaria atrás da autenticação, e a aba ficaria
        sem ícone até o operador entrar -- foi por isso que ele virou data URI.
        Mas devolver 404 faria o navegador registrar um erro em toda visita, já
        que ele pede `/favicon.ico` por conta própria. 204 encerra a conversa
        sem corpo e sem erro.
        """
        self.send_response(HTTPStatus.NO_CONTENT)
        for nome, valor in self.SECURITY_HEADERS:
            self.send_header(nome, valor)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def pagina_de_login(
        self, lang: str, erro: str = "", com_desafio: bool = True, mensagem: str = ""
    ) -> bytes:
        """Monta o formulário de entrada com o desafio que o endereço merece.

        O desafio só entra depois de algumas falhas: quem acerta de primeira
        nunca o vê, e quem insiste passa a pagar CPU por tentativa.

        `com_desafio=False` é a recusa do acesso federado, e o motivo é o
        oposto: lá a página tem de sair IDÊNTICA para toda falha, e um desafio
        sorteado a cada recusa mudaria o corpo -- que é exatamente o sinal que
        conta ao atacante em que ponto do fluxo ele parou.
        """
        desafio = protecao.novo_desafio() if com_desafio else ""
        cfg = sso.carregar(self.prefs_path(), self.sso_base_dir())
        oidc_nome = ""
        if cfg.oidc_esta_ligado() and sso.descobre(cfg.issuer):
            oidc_nome = cfg.nome_do_oidc()
        saml_nome = ""
        if cfg.saml_esta_ligado():
            saml_nome = cfg.nome_do_saml()
        return render_login_page(
            lang,
            erro,
            desafio,
            protecao.DIFICULDADE,
            oidc_nome=oidc_nome,
            saml_nome=saml_nome,
            senha_habilitada=cfg.senha_esta_ligada(),
            mensagem=mensagem,
        )

    def serve_login_page(self, erro: str = "", mensagem: str = "") -> None:
        """Formulário de entrada: a porta do navegador para o painel."""
        self.respond_html(self.pagina_de_login(self.resolve_language(), erro, mensagem=mensagem))

    def eh_conexao_segura(self) -> bool:
        """Determina se a requisição veio por canal seguro (HTTPS).

        Detecta via proxy reverso (cabeçalhos X-Forwarded-Proto, X-Forwarded-Scheme,
        X-Forwarded-Ssl, Front-End-Https) ou quando o SSO foi configurado com uma
        base_url https://.
        """
        proto = (self.headers.get("X-Forwarded-Proto") or "").lower().strip()
        if proto == "https":
            return True
        scheme = (self.headers.get("X-Forwarded-Scheme") or "").lower().strip()
        if scheme == "https":
            return True
        if (self.headers.get("X-Forwarded-Ssl") or "").lower().strip() == "on":
            return True
        if (self.headers.get("Front-End-Https") or "").lower().strip() == "on":
            return True
        try:
            cfg = self.configuracao_sso()
            if cfg and str(cfg.get("base_url") or "").lower().strip().startswith("https://"):
                return True
        except Exception:
            pass
        return False

    def handle_login(self) -> None:
        """Valida a credencial do formulário e emite o cookie de sessão."""
        endereco = protecao.endereco_do_cliente(self.client_address)

        # Teto por janela: o que para o script que tenta mil senhas por minuto.
        pode, espere = protecao.registra_tentativa(endereco)
        if not pode:
            self.responde_429(espere)
            return

        tamanho = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(tamanho) if tamanho > 0 else b""
        campos = parse_qs(corpo.decode("utf-8", "replace"))
        usuario = (campos.get("usuario") or [""])[0]
        senha = (campos.get("senha") or [""])[0]

        cfg = sso.carregar(self.prefs_path(), self.sso_base_dir())
        if not cfg.senha_esta_ligada():
            self.serve_login_page(translate("auth.password_disabled", self.resolve_language()))
            return

        # Desafio captcha para evitar bots automatizados.
        desafio = (campos.get("desafio") or [""])[0]
        resposta = (campos.get("resposta") or [""])[0]
        if desafio or protecao.precisa_de_desafio(endereco):
            if not protecao.resposta_confere(
                desafio, resposta, protecao.dificuldade_para(endereco)
            ):
                protecao.anota_falha(endereco)
                self.serve_login_page(translate("auth.challenge_failed", self.resolve_language()))
                return

        # A espera cresce a cada falha seguida. É do lado do servidor: não há
        # nada no cliente para desligar.
        atraso = protecao.espera_por_falhas(endereco)
        if atraso:
            time.sleep(atraso)

        if not self.settings or not self.settings.verify_credentials(usuario, senha):
            # Mensagem única para usuário errado e senha errada: distinguir os
            # dois conta a quem tenta qual metade já acertou.
            protecao.anota_falha(endereco)
            self.serve_login_page(translate("auth.login_failed", self.resolve_language()))
            return

        protecao.limpa_apos_sucesso(endereco)
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        seguro = self.eh_conexao_segura()
        self.send_header(
            "Set-Cookie", sessao.cabecalho_para_gravar(sessao.emitir(usuario), seguro=seguro)
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_logout(self) -> None:
        """Apaga o cookie. O Basic Auth não tem equivalente disso."""
        self.authenticated_user = ""
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/login?logout=1")
        seguro = self.eh_conexao_segura()
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar(seguro=seguro))
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar_estado(seguro=seguro))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- acesso federado ----------------------------------------------------
    #
    # As rotas públicas a mais, e todas passam pelo MESMO teto por endereço do
    # formulário de login: a ida ao provedor e a volta dele acontecem sem
    # sessão -- é a sessão que elas existem para criar.

    def freio_do_sso(self) -> bool:
        """Aplica o teto por endereço. Devolve True quando já respondeu 429."""
        endereco = protecao.endereco_do_cliente(self.client_address)
        pode, espere = protecao.registra_tentativa(endereco)
        if not pode:
            self.responde_429(espere)
            return True
        return False

    def anota_falha_de_sso(self, motivo: Any) -> None:
        """O motivo vai para o log interno; a tela recebe a mensagem genérica.

        Nada do que passa por aqui carrega credencial: nem o `code`, nem os
        tokens, nem o segredo do cliente. O que se registra é o passo que falhou.
        """
        get_logger().warning("[SSO] fluxo recusado: %s", motivo)

    def recusa_sso(self) -> None:
        """Mensagem ÚNICA para toda falha do fluxo federado.

        Distinguir "state trocado" de "e-mail fora da lista" conta ao atacante
        em que ponto do fluxo ele parou. O formulário local vem junto: quem tem
        senha entra mesmo com o provedor recusando.

        O cookie de estado é de uso único: recusada a volta, ele sai junto, para
        que uma segunda tentativa com o mesmo `state` não encontre nada com que
        comparar.
        """
        lang = self.resolve_language()
        corpo = self.pagina_de_login(lang, translate("sso.failed", lang), com_desafio=False)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        seguro = self.eh_conexao_segura()
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar_estado(seguro=seguro))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def pousa_sessao_federada(self, email: str) -> None:
        """Emite o MESMO cookie assinado do formulário e aterrissa em "/".

        NÃO é um 302: no Chrome, uma cadeia de redirecionamento iniciada em
        outro site não carrega o cookie `SameSite=Strict` no salto seguinte, e o
        operador cairia em `/login` com uma sessão válida no bolso.

        O destino é SEMPRE "/". Nenhum parâmetro de retorno vira destino, aqui
        ou em qualquer lugar: isso seria redirecionamento aberto autenticado.
        """
        lang = self.resolve_language()
        corpo = self.pagina_de_pouso(lang)
        # O prefixo "sso:" distingue no rodapé e no log quem entrou pela porta
        # federada, sem inventar uma segunda forma de sessão: o cookie é o mesmo.
        get_logger().info("[SSO] sessão emitida para uma identidade federada")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        seguro = self.eh_conexao_segura()
        self.send_header(
            "Set-Cookie", sessao.cabecalho_para_gravar(sessao.emitir("sso:" + email), seguro=seguro)
        )
        # O cookie de ida já cumpriu o papel: uso único.
        self.send_header("Set-Cookie", sessao.cabecalho_para_apagar_estado(seguro=seguro))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)

    def confere_senha_local(self, senha: str) -> bool:
        """Confere a senha do PAINEL, nunca a identidade federada da sessão.

        Quem entrou pelo provedor de identidade não tem senha local -- e é
        exatamente por isso que ela é exigida ao salvar a configuração: é o que
        um cookie sequestrado não entrega. A credencial de recuperação entra
        sempre, com provedor vivo ou morto: é ela que salva quem precisa
        DESLIGAR o acesso federado e não lembra a senha do painel.
        """
        if not self.settings or not senha:
            return False
        usuario = self.authenticated_user or getattr(self.settings, "dashboard_user", "admin")
        if self.settings.verify_credentials(usuario, senha):
            return True
        return self.settings.verify_credentials("admin", senha)

    def configuracao_sso(self) -> Optional[Dict[str, Any]]:
        """Configuração em vigor, ou None quando o acesso federado não deve funcionar.

        A leitura é barata mas não é de graça, e o mesmo pedido a consulta na
        autenticação, na tela e no despacho: memorizada por requisição.
        """
        guardada = getattr(self, "_configuracao_sso", None)
        if guardada is None and self.settings:
            guardada = sso.configuracao_efetiva(self.prefs_path(), self.sso_base_dir())
            self._configuracao_sso = guardada
        return guardada

    def sso_esta_ligado(self) -> bool:
        """Há provedor configurado E completo? É o que decide se as rotas existem."""
        return bool(self.configuracao_sso())

    def sso_base_dir(self) -> str:
        """Diretório do segredo do cliente: o mesmo das credenciais locais.

        Nunca $HOME por atalho -- o segredo tem de cair no volume de dados, ou
        ele some quando o container é recriado e o SSO se desliga sozinho.
        """
        if not self.settings:
            return ""
        return os.path.dirname(self.settings.get_auth_file_path())

    def provedor_de_sso(self) -> str:
        """Qual provedor responde agora. Vazio significa desligado."""
        try:
            return sso.provedor_ativo(self.configuracao_sso(), self.sso_base_dir())
        except Exception:
            # Banco ilegível, diretório sumido: acesso federado desligado e o
            # painel segue servindo o formulário local, que é o que não pode faltar.
            return ""

    def nome_do_provedor_sso(self) -> str:
        """Nome exibido no botão da tela de login. Vazio quando não há botão.

        A descoberta é consultada aqui de propósito: se o provedor de identidade
        não responde, o botão SOME em vez de levar a uma falha genérica. O
        formulário local nunca sai da tela.
        """
        config = self.configuracao_sso()
        if self.provedor_de_sso() != "oidc":
            return ""
        try:
            if not sso.descobre(config["oidc_issuer"]):
                return ""
        except Exception:
            return ""
        return sso.nome_do_provedor(config)

    def pagina_de_pouso(self, lang: str) -> bytes:
        """A tela intermediária que carrega o cookie recém-emitido."""
        return render_notice_page(
            translate("sso.signing_in", lang),
            translate("sso.signing_in_body", lang),
            refresh_url="/",
        )

    def inicia_oidc(self) -> None:
        """Sorteia o estado, grava o cookie de ida e manda o navegador ao provedor."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config:
            self.recusa_sso()
            return
        cfg_obj = sso._configuracao_de(config) if isinstance(config, dict) else config
        if not cfg_obj.oidc_esta_ligado():
            self.recusa_sso()
            return

        try:
            documento = sso.descobre(cfg_obj.issuer)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            verificador, desafio = sso.novo_desafio_pkce()
            destino = sso.url_de_autorizacao(config, documento, state, nonce, desafio)
        except Exception as erro:
            # Descoberta quebrada não trava o login local: a tela volta com o
            # formulário de sempre.
            self.anota_falha_de_sso(getattr(erro, "detalhe", type(erro).__name__))
            self.recusa_sso()
            return

        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", destino)
        seguro = self.eh_conexao_segura()
        self.send_header(
            "Set-Cookie",
            sessao.cabecalho_para_gravar_estado(
                sessao.emitir_estado_sso(state, nonce, verificador),
                seguro=seguro,
            ),
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def recebe_oidc(self) -> None:
        """Volta do provedor. Valida TUDO antes de emitir sessão."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config:
            self.recusa_sso()
            return
        cfg_obj = sso._configuracao_de(config) if isinstance(config, dict) else config
        if not cfg_obj.oidc_esta_ligado():
            self.recusa_sso()
            return

        consulta = parse_qs(urlparse(self.path).query)
        try:
            email = sso.conclui_callback(
                config,
                self.sso_base_dir(),
                state_da_query=(consulta.get("state") or [""])[0],
                codigo=(consulta.get("code") or [""])[0],
                erro_da_query=(consulta.get("error") or [""])[0],
                cookie_de_estado=sessao.ler_estado_do_cabecalho(self.headers.get("Cookie", "")),
            )
        except Exception as erro:
            self.anota_falha_de_sso(getattr(erro, "detalhe", type(erro).__name__))
            protecao.anota_falha(protecao.endereco_do_cliente(self.client_address))
            self.recusa_sso()
            return

        protecao.limpa_apos_sucesso(protecao.endereco_do_cliente(self.client_address))
        self.pousa_sessao_federada(email)

    def inicia_saml(self) -> None:
        """AuthnRequest por HTTP-Redirect binding, com o ID guardado no servidor."""
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config:
            self.recusa_sso()
            return
        cfg_obj = sso._configuracao_de(config) if isinstance(config, dict) else config
        if not cfg_obj.saml_esta_ligado():
            self.recusa_sso()
            return
        identificador = sso.novo_id_de_requisicao()
        # No SERVIDOR, e não em cookie: o ACS é um POST vindo de outro site, e
        # `SameSite=Lax` não viaja em POST cross-site.
        sso.registra_pendente(identificador)
        self.send_response(HTTPStatus.FOUND)
        self.send_header(
            "Location",
            sso.url_de_ida_saml(cfg_obj, sso.monta_authn_request(cfg_obj, identificador)),
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def recebe_saml(self) -> None:
        """ACS: recebe a asserção do provedor e valida antes de emitir sessão.

        Não passa pela guarda de mesma origem, e não precisa: por definição este
        POST vem de outro site, e a autenticidade vem da assinatura XML e do
        `InResponseTo`, não do cabeçalho Origin.
        """
        if self.freio_do_sso():
            return
        config = self.configuracao_sso()
        if not config:
            self.recusa_sso()
            return
        cfg_obj = sso._configuracao_de(config) if isinstance(config, dict) else config
        if not cfg_obj.saml_esta_ligado():
            self.recusa_sso()
            return

        # O corpo é lido AQUI, dentro do handler -- nunca no despacho, que roda
        # antes de qualquer decisão sobre quem está do outro lado.
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        campos = parse_qs(corpo.decode("utf-8", errors="replace"))

        try:
            resposta = (campos.get("SAMLResponse") or [""])[0]
            if not resposta:
                raise sso.FalhaDeSSO("POST no ACS sem SAMLResponse")
            identificador = sso.in_response_to(resposta)
            if not sso.consome_pendente(identificador):
                raise sso.FalhaDeSSO("InResponseTo desconhecido, gasto ou fora do prazo")
            email = sso.processa_resposta_saml(cfg_obj, resposta, identificador)
            if not sso.email_autorizado(email, cfg_obj):
                raise sso.FalhaDeSSO("e-mail fora da lista de autorizados")
        except sso.FalhaDeSSO as erro:
            self.anota_falha_de_sso(erro)
            self.recusa_sso()
            return

        protecao.limpa_apos_sucesso(protecao.endereco_do_cliente(self.client_address))
        self.pousa_sessao_federada(email)

    def serve_saml_metadata(self) -> None:
        """Descrição do serviço, servida SÓ com sessão.

        Não aumenta a lista de rotas públicas: o operador baixa o arquivo
        autenticado e o entrega ao provedor, e não há pressa nenhuma nisso.
        """
        config = self.configuracao_sso()
        if not config:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        cfg_obj = sso._configuracao_de(config) if isinstance(config, dict) else config
        if not cfg_obj.saml_esta_ligado():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            corpo = sso.metadata_do_sp(cfg_obj).encode("utf-8")
        except sso.FalhaDeSSO:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.write_body(corpo)

    def sso_view(self) -> Dict[str, Any]:
        """O que a tela de configuração precisa saber. NUNCA o segredo do cliente.

        Um GET de configuração jamais devolve o valor gravado: a tela recebe
        apenas a informação de que EXISTE um segredo, e um campo para substituí-lo.
        """
        if not self.settings:
            return {}
        config = sso.ler_configuracao(self.prefs_path())
        base = sso.normaliza_base_url(config.get("base_url", ""))
        return {
            "config": config,
            "tem_segredo": bool(sso.ler_segredo(self.sso_base_dir())),
            "segredo_do_ambiente": sso.segredo_vem_do_ambiente(),
            "desligado_por_ambiente": sso.desligado_por_ambiente(),
            "saml_disponivel": sso.saml_disponivel(),
            "callback_url": f"{base}{sso.ROTA_CALLBACK}" if base else "",
        }

    def handle_sso_settings(self, campos: Dict[str, List[str]]) -> None:
        """Grava a configuração do acesso federado. Exige a senha local atual.

        Três trancas, e as três são necessárias: sessão (do `do_POST`), guarda
        de mesma origem (idem) e a SENHA LOCAL ATUAL, pedida aqui. A terceira
        existe porque quem sequestra uma sessão de oito horas poderia apontar o
        painel para um provedor hostil e se pôr na lista de autorizados --
        persistência permanente ganha com um cookie roubado.
        """
        lang = self.resolve_language()
        if self.freio_do_sso():
            return

        def campo(nome: str, padrao: str = "") -> str:
            vals = campos.get(nome)
            if not vals:
                return padrao
            if "1" in vals:
                return "1"
            return (vals[-1] or "").strip()

        senha_informada = campo("senha_atual") or campo("senha_local") or campo("senha")
        if not self.confere_senha_local(senha_informada):
            protecao.anota_falha(protecao.endereco_do_cliente(self.client_address))
            self.redirect_to_dashboard("danger", translate("sso.wrong_password", lang))
            return

        if sso.desligado_por_ambiente():
            self.redirect_to_dashboard("warning", translate("sso.disabled_by_env", lang))
            return

        if campo("desligar"):
            sso.gravar(self.prefs_path(), {
                sso.CHAVE_PROVEDOR: "",
                sso.CHAVE_OIDC_HABILITADO: "0",
                sso.CHAVE_SAML_HABILITADO: "0",
            })
            sso.esquece_descobertas()
            self.redirect_to_dashboard("success", translate("sso.turned_off", lang))
            return

        enabled_val = campo("enabled").lower()
        if "password_enabled" in campos:
            senha_hab = "1" if campo("password_enabled") in ("1", "true", "on", "yes") else "0"
        else:
            senha_hab = "1"

        if "oidc_enabled" in campos:
            oidc_hab = "1" if campo("oidc_enabled") in ("1", "true", "on", "yes") else "0"
        elif enabled_val:
            oidc_hab = "1" if enabled_val in ("oidc", "both", "all") else "0"
        else:
            oidc_hab = "0"

        if "saml_enabled" in campos:
            saml_hab = "1" if campo("saml_enabled") in ("1", "true", "on", "yes") else "0"
        elif enabled_val:
            saml_hab = "1" if enabled_val in ("saml", "both", "all") else "0"
        else:
            saml_hab = "0"

        if enabled_val == "" and "oidc_enabled" not in campos and "saml_enabled" not in campos:
            oidc_hab = "0"
            saml_hab = "0"

        novo = {
            "password_enabled": senha_hab,
            "oidc_enabled": oidc_hab,
            "saml_enabled": saml_hab,
            "base_url": campo("base_url"),
            "issuer": campo("issuer") or campo("oidc_issuer"),
            "client_id": campo("client_id") or campo("oidc_client_id"),
            "scopes": campo("scopes") or campo("oidc_scopes") or sso.ESCOPOS_PADRAO,
            "allowed_domains": campo("allowed_domains"),
            "allowed_emails": campo("allowed_emails"),
            "idp_entity_id": campo("saml_idp_entity_id") or campo("idp_entity_id"),
            "idp_sso_url": campo("saml_idp_sso_url") or campo("idp_sso_url"),
            "idp_cert": campo("saml_idp_cert") or campo("idp_cert"),
        }
        if oidc_hab == "1" and saml_hab == "1":
            novo["enabled"] = "both"
        elif oidc_hab == "1":
            novo["enabled"] = "oidc"
        elif saml_hab == "1":
            novo["enabled"] = "saml"
        else:
            novo["enabled"] = ""

        if novo["enabled"] not in sso.PROVEDORES:
            self.redirect_to_dashboard("danger", translate("sso.save_failed", lang))
            return

        base_dir = self.sso_base_dir()
        novo_segredo = campo("client_secret") or campo("oidc_client_secret")
        if novo_segredo and not sso.segredo_vem_do_ambiente():
            if not sso.grava_segredo(base_dir, novo_segredo):
                self.redirect_to_dashboard("danger", translate("sso.secret_failed", lang))
                return

        tem_segredo = bool(sso.ler_segredo(base_dir))
        if oidc_hab == "1":
            problemas = sso.problemas_da_configuracao(novo, tem_segredo)
            if problemas:
                self.redirect_to_dashboard(
                    "danger", " ".join(translate(chave, lang) for chave in problemas)
                )
                return

        if saml_hab == "1":
            problemas = sso.problemas_do_saml(novo)
            if problemas:
                self.redirect_to_dashboard(
                    "danger", " ".join(translate(chave, lang) for chave in problemas)
                )
                return

        if senha_hab == "0" and oidc_hab != "1" and saml_hab != "1":
            self.redirect_to_dashboard("danger", translate("sso.at_least_one_auth", lang))
            return

        if not sso.grava_configuracao(self.prefs_path(), novo):
            self.redirect_to_dashboard("danger", translate("sso.save_failed", lang))
            return

        sso.limpa_cache_descoberta()
        self._configuracao_sso = None
        get_logger().info("[SSO] configuração atualizada")
        self.redirect_to_dashboard("success", translate("sso.saved", lang))

    def handle_sso_test_oidc(self, corpo: bytes) -> None:
        if self.freio_do_sso():
            return
        try:
            dados = json.loads(corpo.decode("utf-8", errors="replace"))
        except Exception:
            dados = {}
        issuer = str(dados.get("issuer") or "").strip()
        client_id = str(dados.get("client_id") or "").strip()
        base_url = str(dados.get("base_url") or "").strip()
        ok, msg = sso.testar_conexao_oidc(issuer, client_id, base_url=base_url)
        self.respond_json(
            json.dumps({"ok": ok, "mensagem": msg}, ensure_ascii=False).encode("utf-8")
        )

    def handle_sso_test_saml(self, corpo: bytes) -> None:
        if self.freio_do_sso():
            return
        try:
            dados = json.loads(corpo.decode("utf-8", errors="replace"))
        except Exception:
            dados = {}
        entity_id = str(dados.get("saml_idp_entity_id") or dados.get("idp_entity_id") or "").strip()
        sso_url = str(dados.get("saml_idp_sso_url") or dados.get("idp_sso_url") or "").strip()
        cert = str(dados.get("saml_idp_cert") or dados.get("idp_cert") or "").strip()
        base_url = str(dados.get("base_url") or "").strip()
        ok, msg = sso.testar_conexao_saml(entity_id, sso_url, cert, base_url=base_url)
        self.respond_json(
            json.dumps({"ok": ok, "mensagem": msg}, ensure_ascii=False).encode("utf-8")
        )

    # -- preferências -------------------------------------------------------

    def prefs_path(self) -> str:
        """Banco de preferências próprio do sincronizador, nunca o do gateway."""
        return self.settings.get_prefs_path() if self.settings else ""

    def resolve_language(self) -> str:
        """Idioma em vigor: query param, cookie do navegador, SQLite, ou padrão (inglês)."""
        query = parse_qs(urlparse(self.path).query)
        if "lang" in query and query["lang"]:
            lang = normalize_language(query["lang"][0])
            if lang in LANGUAGES:
                return lang
        cookie_header = self.headers.get("Cookie", "")
        for parte in cookie_header.split(";"):
            parte = parte.strip()
            if parte.startswith("rtksync_lang="):
                lang = normalize_language(parte.split("=", 1)[1])
                if lang in LANGUAGES:
                    return lang
        if self.settings:
            return normalize_language(
                get_preference(self.prefs_path(), "language", DEFAULT_LANGUAGE)
            )
        return DEFAULT_LANGUAGE

    # -- ações --------------------------------------------------------------

    def invalidate_caches(self) -> None:
        """Descarta o que foi memorizado para que a próxima renderização releia tudo.

        Sem isto, o resultado da sondagem ao gateway continuaria valendo por até
        GATEWAY_PROBE_TTL_SECONDS e o painel exibiria um estado anterior à ação
        que o operador acabou de disparar.
        """
        with _gateway_probe_lock:
            _gateway_probe_cache.clear()

    def handle_dashboard_action(self, route: str, campos: Dict[str, List[str]]) -> None:
        """Executa uma ação do painel e devolve o operador à página renderizada."""
        if route == "/acoes/atualizar":
            # Só recarrega a tela: zera o que foi memorizado e volta para a
            # página, que é montada de novo no servidor. Quem roda um ciclo é
            # "Sync now", que aponta para /acoes/cron -- dois botões vizinhos
            # parecendo fazer a mesma coisa faziam o operador escolher no escuro.
            self.invalidate_caches()
            self.redirect_to_dashboard(
                "info", translate("action.refreshed", self.resolve_language())
            )
        elif route == "/acoes/cron":
            self.handle_cron_action()
        elif route == "/acoes/idioma":
            self.handle_language(campos)
        elif route == "/acoes/testar-gateway":
            self.handle_gateway_test()
        elif route == "/acoes/credenciais":
            self.handle_credentials(campos)
        elif route == "/acoes/sso":
            self.handle_sso_settings(campos)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_language(self, campos: Dict[str, List[str]]) -> None:
        """Grava o idioma escolhido, emite cookie e volta para a página anterior ou raiz."""
        escolhido = normalize_language((campos.get("lang", [""])[0] or "").strip())
        if self.settings:
            set_preference(self.prefs_path(), "language", escolhido)
        referer = self.headers.get("Referer", "")
        destino = "/"
        if referer:
            try:
                parsed = urlparse(referer)
                if parsed.path:
                    destino = parsed.path
                    if parsed.query:
                        q = parse_qs(parsed.query)
                        q.pop("lang", None)
                        if q:
                            destino += "?" + urlencode(q, doseq=True)
            except Exception:
                destino = "/"

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", destino)
        seguro = self.eh_conexao_segura()
        s = "; Secure" if seguro else ""
        self.send_header(
            "Set-Cookie",
            f"rtksync_lang={escolhido}; Path=/; Max-Age=31536000; SameSite=Lax{s}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_cron_action(self) -> None:
        """Dispara o agendador agora, registrando a execução no histórico dele.

        É a ÚNICA rota que roda um ciclo sob demanda. Havia também
        `/acoes/sincronizar`, que fazia exatamente o mesmo trabalho por fora do
        agendador: dois botões para a mesma ação, e o ciclo disparado pelo
        primeiro não aparecia no histórico que a tela mostra.
        """
        lang = self.resolve_language()
        if not self.cron_scheduler:
            self.redirect_to_dashboard("warning", translate("cron.unavailable", lang))
            return
        try:
            entry = self.cron_scheduler.trigger_now() or {}
        except Exception as erro:
            self.redirect_to_dashboard("danger", translate("cron.failed", lang, error=erro))
            return
        # O ciclo mexe no estado do gateway: o que ficou memorizado antes dele
        # deixaria a tela mostrando o mundo anterior ao clique.
        self.invalidate_caches()
        self.redirect_to_dashboard(
            "success" if entry.get("success", True) else "warning",
            translate(
                "action.cron_ran",
                lang,
                duration=entry.get("durationMs", 0),
                inspected=entry.get("totalInspected", 0),
                # O nome do campo ainda muda entre os irmãos -- `findingsCount`
                # de um lado, `refreshedCount` do outro. Unificá-lo é trabalho
                # do `cron.py`, não daqui.
                findings=entry.get("findingsCount", entry.get("refreshedCount", 0)),
            ),
        )

    def handle_gateway_test(self) -> None:
        """Sonda o gateway agora, sem esperar o que estava memorizado expirar."""
        lang = self.resolve_language()
        self.invalidate_caches()
        online = self.probe_gateway()
        self.redirect_to_dashboard(
            "success" if online else "danger",
            f'{translate("gateway.title", lang)}: '
            f'{"ONLINE" if online else translate("gateway.no_response", lang)}',
        )

    def handle_credentials(self, campos: Dict[str, List[str]]) -> None:
        """Troca usuário e senha do painel, com a política de força inteira."""
        lang = self.resolve_language()
        user = (campos.get("user", [""])[0] or "").strip()
        password = (campos.get("password", [""])[0] or "").strip()

        # Todas as regras violadas de uma vez: uma por tentativa faria o
        # operador descobrir a política aos poucos.
        problemas = self.settings.check_password_strength(password) if self.settings else []
        if problemas:
            self.redirect_to_dashboard(
                "danger", " ".join(translate(chave, lang) for chave in problemas)
            )
            return
        if self.settings and getattr(self.settings, "dashboard_auth_from_env", False):
            # O mesmo texto do modal, sem a marcação: o aviso é escapado antes
            # de ir para a tela, e as etiquetas <code> apareceriam cruas ali.
            self.redirect_to_dashboard("warning", strip_markup(translate("auth.env_managed", lang)))
            return
        if self.settings and self.settings.update_auth_credentials(user, password):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/credenciais-atualizadas")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.redirect_to_dashboard("danger", translate("auth.save_failed", lang))

    # -- gateway ------------------------------------------------------------

    def probe_gateway(self) -> bool:
        """Sonda o gateway com cache: o resultado vale por GATEWAY_PROBE_TTL_SECONDS.

        Quem faz a pergunta é o `gateway.py`, que sabe o endereço e o que conta
        como "respondeu" NESTE gateway. Aqui fica só o cache -- e ele é comum
        aos três, porque o /healthz é chamado a cada 15s pelo Docker e sem cache
        cada chamada pagaria uma requisição de saída de até 3s, estourando o
        timeout do probe.
        """
        alvo = self.gateway_url()
        if not alvo:
            return True

        agora = time.time()
        with _gateway_probe_lock:
            medido_em, resultado = _gateway_probe_cache.get(alvo, (0.0, None))
            if resultado is not None and (agora - medido_em) < GATEWAY_PROBE_TTL_SECONDS:
                return resultado

        respondeu = self.sonda_o_gateway(alvo)

        with _gateway_probe_lock:
            _gateway_probe_cache[alvo] = (time.time(), respondeu)
        return respondeu

    def gateway_url(self) -> str:
        """Endereço do gateway que este painel acompanha."""
        return self.router_url

    def sonda_o_gateway(self, alvo: str) -> bool:
        """Pergunta ao gateway se ele respondeu.

        A pergunta em si ainda mora aqui: falta ao `gateway.py` deste irmão um
        `sondar()` como o dos outros. Enquanto ela estiver neste arquivo, o
        transporte continua sabendo de que gateway se trata.
        """
        try:
            req = urllib.request.Request(
                alvo, headers={"User-Agent": f"{NOME_DO_PRODUTO}-Healthcheck/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as erro:
            return erro.code < 500
        except Exception:
            return False

    def contagem_do_banco(self) -> Dict[str, int]:
        """Quantas conexões e combos o banco do gateway tem agora."""
        try:
            return {
                "connections": len(get_all_connections(self.db_path)),
                "combos": len(get_all_combos(self.db_path)),
            }
        except Exception:
            return {}

    # -- renderização -------------------------------------------------------

    def collect_dashboard_state(self) -> Dict[str, Any]:
        """Lê tudo o que a página precisa. Roda no servidor: o SQLite nunca sai daqui."""
        # As quatro leituras ainda saem daqui: falta ao `gateway.py` deste irmão
        # o `carregar_painel()` que os outros já têm, e é para lá que elas vão.
        rows, combos, key_rows, model_rows = [], [], [], []
        db_exists = bool(self.db_path and os.path.exists(self.db_path))
        if db_exists:
            for destino, leitura in (
                ("rows", get_all_connections),
                ("combos", get_all_combos),
                ("keys", get_all_api_keys),
                ("models", get_all_registered_models),
            ):
                try:
                    lido = leitura(self.db_path)
                except Exception:
                    lido = []
                if destino == "rows":
                    rows = lido
                elif destino == "combos":
                    combos = lido
                elif destino == "keys":
                    key_rows = lido
                else:
                    model_rows = lido

        inicio = time.time()
        online = self.probe_gateway()
        latency_ms = int((time.time() - inicio) * 1000)

        conns = [ConnectionRecord.from_row(r) for r in rows]
        # Cada modelo do catálogo declara o id da conexão que o sincronizou; é
        # dessa conexão que a linha herda status, validade e última renovação.
        # Sem o índice, cada modelo faria uma varredura da lista de conexões.
        por_id = {c.id: c for c in conns}

        return {
            "connections": conns,
            "combos": combos,
            "keys": [VirtualKeyRecord.from_row(k) for k in key_rows],
            "models": [
                RegisteredModelRecord.from_row(m, por_id.get(m.get("connectionId", "")))
                for m in model_rows
            ],
            "modelsState": "ok",
            "cron": self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False},
            "gateway": {
                "url": self.gateway_url(),
                "online": online,
                "statusCode": 200 if online else 0,
                "latencyMs": latency_ms,
                # Bandeira explícita: o resumo é texto para humano e vinha
                # sempre preenchido, inclusive com "Banco não encontrado".
                # Converter esse texto em booleano fazia a tela declarar banco e
                # gateway 100% operacionais justamente quando o arquivo sumia.
                "dbOk": db_exists,
                # Números, e não frase pronta: quem conhece o idioma escolhido é
                # o render. Enquanto a frase nascia aqui, a tela em inglês
                # exibia "Operacional (0 conexões, 0 combos)".
                "dbConnections": len(rows),
                "dbCombos": len(combos),
            },
        }

    def serve_dashboard(self, query: Dict[str, List[str]]) -> None:
        """Renderiza a página inteira no servidor, com os dados já embutidos."""
        estado = self.collect_dashboard_state()

        flash = None
        aviso = (query.get("aviso", [""])[0] or "").strip()
        if aviso:
            flash = {"message": aviso, "tone": (query.get("tom", ["info"])[0] or "info").strip()}

        current_user, is_default, auth_from_env, refresh_margin = "admin", False, False, 900
        if self.settings:
            current_user = self.authenticated_user or self.settings.dashboard_user
            is_default = self.settings.is_default_password()
            auth_from_env = getattr(self.settings, "dashboard_auth_from_env", False)
            refresh_margin = self.settings.refresh_margin

        visao = self.sso_view()
        conteudo = render_dashboard(
            connections=estado["connections"],
            combos=estado["combos"],
            keys=estado["keys"],
            models=estado["models"],
            cron=estado["cron"],
            gateway=estado["gateway"],
            db_path=self.db_path,
            router_url=self.gateway_url(),
            current_user=current_user,
            is_default_password=is_default,
            refresh_margin=refresh_margin,
            auth_from_env=auth_from_env,
            flash=flash,
            lang=self.resolve_language(),
            sso_config=visao.get("config") or {},
            # Booleano, e nunca o valor: um GET de configuração que devolvesse
            # o segredo seria o mesmo que publicá-lo no HTML da página.
            sso_tem_segredo=visao.get("tem_segredo", False),
            sso_segredo_do_ambiente=visao.get("segredo_do_ambiente", False),
            sso_desligado_pelo_ambiente=visao.get("desligado_por_ambiente", False),
            sso_endereco_de_retorno=visao.get("callback_url", ""),
        ).encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # A página carrega dados vivos: nunca pode vir do cache do navegador.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(conteudo)))
        self.end_headers()
        self.write_body(conteudo)

    def serve_api_status(self) -> None:
        """Projeção explícita: só os campos que o painel realmente consome.

        `get_all_connections` devolve accessToken, refreshToken, apiKey e a
        linha bruta do banco. Nada disso pode sair pela API.
        """
        conns, combos = [], []
        if self.db_path and os.path.exists(self.db_path):
            try:
                conns = [ConnectionRecord.from_row(r) for r in get_all_connections(self.db_path)]
            except Exception:
                conns = []
            try:
                combos = get_all_combos(self.db_path)
            except Exception:
                combos = []

        payload = {
            "status": "online",
            "gatewayUrl": self.gateway_url(),
            "dbPath": self.db_path,
            "currentUser": self.authenticated_user or "admin",
            "isDefaultPassword": self.settings.is_default_password() if self.settings else False,
            "cron": self.cron_scheduler.get_status() if self.cron_scheduler else {"active": False},
            "connections": [
                {
                    "id": c.id,
                    "provider": c.provider,
                    "name": c.name,
                    "isOAuth": c.is_oauth,
                    "hasApiKey": c.has_api_key,
                    # `getattr` porque o registro de conexao ainda nao e o mesmo
                    # nos tres: um declara `is_local`, o outro `updated_at`, e
                    # perder o campo seria quebrar quem le esta API la fora.
                    "isLocal": getattr(c, "is_local", False),
                    "expiresAtMs": c.expires_at_ms,
                    "remainingSeconds": c.remaining_seconds,
                    "healthStatus": c.health_status,
                    "updatedAt": getattr(c, "updated_at", None),
                }
                for c in conns
            ],
            "combos": combos,
        }
        self.respond_json(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    # -- endpoints JSON -----------------------------------------------------
    #
    # Nenhum deles é desenhado na tela: existem para script, monitoramento e
    # diagnóstico, que é quem sabe falar Basic Auth sem guardar estado.

    def handle_sync_request(self) -> None:
        """Dispara uma sincronização e devolve o resultado cru."""
        if not self.sync_callback:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Synchronizer unavailable")
            return
        try:
            resultado = self.sync_callback()
        except Exception as erro:
            self.responde_erro_json(str(erro))
            return
        self.invalidate_caches()
        self.respond_json(
            json.dumps({"success": True, "result": resultado}, ensure_ascii=False).encode("utf-8")
        )

    def handle_api_cron_run(self) -> None:
        """Roda um ciclo do agendador. Sem agendador, cai na sincronização direta."""
        if not self.cron_scheduler:
            self.handle_sync_request()
            return
        try:
            entry = self.cron_scheduler.trigger_now()
        except Exception as erro:
            self.responde_erro_json(str(erro))
            return
        self.invalidate_caches()
        self.respond_json(
            json.dumps({"success": True, "cycle": entry}, ensure_ascii=False).encode("utf-8")
        )

    def handle_test_gateway(self) -> None:
        """Diagnóstico do gateway e do banco, em uma resposta só."""
        inicio = time.time()
        alvo = self.gateway_url()
        gateway_ok = False
        status_code = 0
        gateway_err = ""

        try:
            req = urllib.request.Request(
                alvo, headers={"User-Agent": f"{NOME_DO_PRODUTO}-Tester/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status_code = resp.status
                gateway_ok = status_code < 500
        except urllib.error.HTTPError as erro:
            status_code = erro.code
            gateway_ok = erro.code < 500
        except Exception as erro:
            gateway_err = str(erro)

        latency_ms = int((time.time() - inicio) * 1000)
        db_exists = bool(self.db_path and os.path.exists(self.db_path))
        contagem = self.contagem_do_banco() if db_exists else {}

        resultado = {
            "success": gateway_ok and db_exists,
            "gatewayUrl": alvo,
            "gatewayStatus": "online" if gateway_ok else "offline",
            "httpStatusCode": status_code,
            "latencyMs": latency_ms,
            "gatewayError": gateway_err if not gateway_ok else None,
            "dbStatus": "ok" if db_exists else "not_found",
            "dbPath": self.db_path,
            "connectionsCount": contagem.get("connections", 0),
            "combosCount": contagem.get("combos", 0),
            "message": (
                f"{NOME_DO_GATEWAY}: OK"
                if (gateway_ok and db_exists)
                else f"{NOME_DO_GATEWAY}: FAIL"
            ),
        }
        self.respond_json(json.dumps(resultado, ensure_ascii=False, indent=2).encode("utf-8"))

    def handle_change_password(self, corpo: bytes) -> None:
        """Troca a credencial por JSON, com a MESMA política de força da tela."""
        lang = self.resolve_language()
        try:
            dados = json.loads(corpo.decode("utf-8")) if corpo else {}
        except Exception as erro:
            self.responde_erro_json(str(erro))
            return

        novo_usuario = str(dados.get("newUser") or "admin").strip()
        nova_senha = str(dados.get("newPassword") or "").strip()

        problemas = self.settings.check_password_strength(nova_senha) if self.settings else []
        if problemas:
            detalhe = " ".join(translate(chave, lang) for chave in problemas)
            self.responde_erro_json(detalhe, HTTPStatus.BAD_REQUEST)
            return
        if self.settings and getattr(self.settings, "dashboard_auth_from_env", False):
            self.responde_erro_json(
                strip_markup(translate("auth.env_managed", lang)), HTTPStatus.CONFLICT
            )
            return
        if self.settings and self.settings.update_auth_credentials(novo_usuario, nova_senha):
            self.respond_json(
                json.dumps(
                    {"success": True, "newUser": novo_usuario}, ensure_ascii=False
                ).encode("utf-8")
            )
            return
        self.responde_erro_json(translate("auth.save_failed", lang))

    def responde_erro_json(
        self, detalhe: str, status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    ) -> None:
        """Erro em JSON. O detalhe nunca carrega credencial: quem o monta é quem falhou."""
        corpo = json.dumps({"success": False, "error": detalhe}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.write_body(corpo)


def start_web_server(
    host: str,
    port: int,
    db_path: str,
    omniroute_url: str = "",
    sync_callback: Optional[Callable[[], Dict[str, Any]]] = None,
    settings: Optional[Settings] = None,
    cron_scheduler: Optional[Any] = None,
) -> ThreadingHTTPServer:
    """Sobe o painel numa thread própria e devolve o servidor."""
    DashboardHandler.db_path = db_path
    DashboardHandler.router_url = omniroute_url
    DashboardHandler.sync_callback = sync_callback
    DashboardHandler.settings = settings
    DashboardHandler.cron_scheduler = cron_scheduler

    server = QuietThreadingHTTPServer((host, port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

