"""Testes de superficie de ataque do painel: CSRF nas acoes e vazamento de segredo na API."""

import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from omini_rtksync.config import Settings
from omini_rtksync import web as web_server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Nao segue o 303: o teste precisa ver a resposta, nao o destino."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


PORT = 19394
BASE = f"http://127.0.0.1:{PORT}"

ACCESS_TOKEN = "ya29.SEGREDO-DE-ACESSO-NAO-PODE-VAZAR"
REFRESH_TOKEN = "1//SEGREDO-DE-REFRESH-NAO-PODE-VAZAR"
API_KEY = "sk-SEGREDO-DE-CHAVE-NAO-PODE-VAZAR"


class TestWebSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "storage.sqlite")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute(
                "CREATE TABLE provider_connections ("
                "id TEXT PRIMARY KEY, provider TEXT, name TEXT, access_token TEXT, "
                "refresh_token TEXT, api_key TEXT, expires_at TEXT, test_status TEXT)"
            )
            conn.execute("CREATE TABLE combos (id TEXT PRIMARY KEY, name TEXT, kind TEXT, models TEXT)")
            conn.execute(
                "INSERT INTO provider_connections VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "conn-1",
                    "google-antigravity",
                    "Antigravity",
                    ACCESS_TOKEN,
                    REFRESH_TOKEN,
                    API_KEY,
                    "2026-12-31T00:00:00.000Z",
                    "active",
                ),
            )

        cls.settings = Settings(
            db_path=cls.db_path,
            web_host="127.0.0.1",
            web_port=PORT,
            dashboard_user="admin",
            dashboard_password="senha-de-teste",
        )
        cls.server = web_server.start_web_server(
            "127.0.0.1", PORT, cls.db_path, omniroute_url="", settings=cls.settings
        )
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    def auth_header(self):
        raw = base64.b64encode(b"admin:senha-de-teste").decode()
        return {"Authorization": f"Basic {raw}"}

    def post(self, path, headers=None, body=b"idioma=pt"):
        """Dispara o POST sem seguir o redirecionamento, para inspecionar a resposta."""
        req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST")
        for key, value in self.auth_header().items():
            req.add_header(key, value)
        for key, value in (headers or {}).items():
            req.add_header(key, value)

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, resp.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", "")

    def assertRejected(self, result):
        """A recusa devolve o usuario ao painel com o aviso, nao uma pagina de erro crua."""
        status, location = result
        self.assertEqual(status, 303)
        self.assertIn("tom=danger", location)

    def assertAccepted(self, result):
        status, _ = result
        self.assertEqual(status, 303)

    # --- CSRF ---------------------------------------------------------------

    def test_cross_site_post_is_rejected(self):
        """O Basic Auth vai junto num POST de outro site; sem esta barreira daria para
        trocar a senha do painel a partir de uma pagina maliciosa."""
        self.assertRejected(self.post("/acoes/idioma", {"Sec-Fetch-Site": "cross-site"}))

    def test_same_site_post_is_rejected(self):
        """Subdominio tambem e outra origem."""
        self.assertRejected(self.post("/acoes/idioma", {"Sec-Fetch-Site": "same-site"}))

    def test_cross_origin_by_origin_header_is_rejected(self):
        """Navegador antigo, sem Sec-Fetch-Site: cai na comparacao de Origin com Host."""
        self.assertRejected(self.post("/acoes/idioma", {"Origin": "http://site-malicioso.example"}))

    def test_same_origin_post_is_accepted(self):
        self.assertAccepted(self.post("/acoes/idioma", {"Sec-Fetch-Site": "same-origin"}))

    def test_direct_navigation_is_accepted(self):
        """Sec-Fetch-Site: none e a navegacao digitada na barra de enderecos."""
        self.assertAccepted(self.post("/acoes/idioma", {"Sec-Fetch-Site": "none"}))

    def test_matching_origin_is_accepted(self):
        self.assertAccepted(self.post("/acoes/idioma", {"Origin": BASE}))

    def test_non_browser_client_is_accepted(self):
        """curl e scripts nao mandam nenhum dos dois cabecalhos e nao tem sessao a sequestrar."""
        self.assertAccepted(self.post("/acoes/idioma"))

    def test_csrf_guard_also_covers_the_json_endpoints(self):
        self.assertRejected(
            self.post("/api/change-password", {"Sec-Fetch-Site": "cross-site"}, b"{}")
        )

    def test_a_mismatched_origin_wins_over_a_friendly_fetch_site(self):
        """O Origin e a evidencia forte: checar Sec-Fetch-Site antes dele fazia um
        valor inesperado do navegador recusar um POST legitimo do proprio painel."""
        self.assertRejected(
            self.post(
                "/acoes/idioma",
                {"Origin": "http://site-malicioso.example", "Sec-Fetch-Site": "same-origin"},
            )
        )

    def test_a_matching_origin_wins_over_an_unexpected_fetch_site(self):
        self.assertAccepted(
            self.post("/acoes/idioma", {"Origin": BASE, "Sec-Fetch-Site": "valor-inesperado"})
        )

    # --- Vazamento de segredo ----------------------------------------------

    def test_api_status_never_returns_credentials(self):
        req = urllib.request.Request(f"{BASE}/api/status")
        for key, value in self.auth_header().items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")

        for secret in (ACCESS_TOKEN, REFRESH_TOKEN, API_KEY):
            self.assertNotIn(secret, body)

        payload = json.loads(body)
        connection = payload["connections"][0]
        self.assertEqual(connection["id"], "conn-1")
        self.assertTrue(connection["isOAuth"])
        # A linha bruta do banco carrega os tokens e nao pode ser serializada.
        for forbidden in ("accessToken", "refreshToken", "apiKey", "raw", "data"):
            self.assertNotIn(forbidden, connection)

    def test_dashboard_html_never_returns_credentials(self):
        req = urllib.request.Request(BASE + "/")
        for key, value in self.auth_header().items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=5) as resp:
            page = resp.read().decode("utf-8")

        for secret in (ACCESS_TOKEN, REFRESH_TOKEN, API_KEY):
            self.assertNotIn(secret, page)

    def test_the_401_body_never_teaches_the_credentials(self):
        """A resposta de autenticacao nao pode ensinar a senha a quem ainda nao entrou."""
        try:
            urllib.request.urlopen(f"{BASE}/", timeout=5)
            self.fail("deveria exigir autenticacao")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)
            body = e.read().decode("utf-8", errors="replace").lower()

        for leak in ("pathbit", "admin /", "password", "senha"):
            self.assertNotIn(leak, body)

    def test_no_page_prints_a_live_credential(self):
        req = urllib.request.Request(BASE + "/")
        for key, value in self.auth_header().items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        # O banner explica que falta senha sem exibir qual e a de fabrica.
        self.assertNotIn("admin / pathbit", page)


class TestSecurityHeadersOnEveryResponse(unittest.TestCase):
    """Sobe o proprio servidor: o unittest ordena as classes pelo nome e esta
    roda antes de TestWebSecurity, que e quem monta o outro fixture."""

    PORTA = PORT + 1
    ORIGEM = f"http://127.0.0.1:{PORT + 1}"

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "data.sqlite")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute(
                "CREATE TABLE provider_connections ("
                "id TEXT PRIMARY KEY, provider TEXT, name TEXT, access_token TEXT, "
                "refresh_token TEXT, api_key TEXT, expires_at TEXT, test_status TEXT, "
                "created_at TEXT, updated_at TEXT)"
            )
            conn.execute("CREATE TABLE combos (id TEXT PRIMARY KEY, name TEXT, models TEXT)")
        cls.settings = Settings(
            db_path=cls.db_path,
            web_host="127.0.0.1",
            web_port=cls.PORTA,
            dashboard_user="admin",
            dashboard_password="senha-de-teste",
            validate_credentials=False,
        )
        cls.server = web_server.start_web_server(
            "127.0.0.1", cls.PORTA, cls.db_path, omniroute_url="", settings=cls.settings
        )
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    """Os cabecalhos de seguranca nao podem valer so para a pagina principal.

    Antes desta cobertura eles eram emitidos em um unico ponto, o da pagina do
    painel. O corpo do 401 e a pagina de credenciais atualizadas tambem sao
    HTML que o navegador renderiza, e saiam sem nenhum deles; nao havia
    Content-Security-Policy em resposta nenhuma.
    """

    OBRIGATORIOS = (
        "Referrer-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Content-Security-Policy",
    )

    def headers_de(self, path, autenticado=True):
        req = urllib.request.Request(f"{self.ORIGEM}{path}")
        if autenticado:
            raw = base64.b64encode(b"admin:senha-de-teste").decode()
            req.add_header("Authorization", f"Basic {raw}")
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.headers
        except urllib.error.HTTPError as e:
            return e.headers

    def test_the_dashboard_carries_every_header(self):
        headers = self.headers_de("/")
        for nome in self.OBRIGATORIOS:
            self.assertIsNotNone(headers.get(nome), f"{nome} ausente em /")

    def test_the_401_body_carries_every_header(self):
        """E o corpo que o navegador exibe quando se aperta ESC: tem de estar protegido."""
        headers = self.headers_de("/", autenticado=False)
        for nome in self.OBRIGATORIOS:
            self.assertIsNotNone(headers.get(nome), f"{nome} ausente no 401")

    def test_the_credentials_updated_page_carries_every_header(self):
        headers = self.headers_de("/credenciais-atualizadas", autenticado=False)
        for nome in self.OBRIGATORIOS:
            self.assertIsNotNone(headers.get(nome), f"{nome} ausente no aviso")

    def test_the_json_endpoint_carries_every_header(self):
        headers = self.headers_de("/api/status")
        for nome in self.OBRIGATORIOS:
            self.assertIsNotNone(headers.get(nome), f"{nome} ausente em /api/status")

    def test_the_policy_allows_exactly_the_origins_the_page_uses(self):
        csp = self.headers_de("/").get("Content-Security-Policy")
        # Bootstrap e os icones vem do jsDelivr; as fontes, do Google.
        self.assertIn("https://cdn.jsdelivr.net", csp)
        self.assertIn("https://fonts.gstatic.com", csp)
        # E nada alem disso: sem destino para exfiltrar e sem enquadramento.
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertIn("base-uri 'none'", csp)

    def test_no_header_is_emitted_twice(self):
        """O helper nao pode duplicar um cabecalho que a rota ja tenha enviado."""
        headers = self.headers_de("/")
        for nome in self.OBRIGATORIOS:
            self.assertEqual(len(headers.get_all(nome) or []), 1, f"{nome} duplicado")


if __name__ == "__main__":
    unittest.main()
