"""Testes da renderização server-side do dashboard."""

import base64
import os
import re
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
import urllib.error
import urllib.request

from omini_rtksync import i18n
from omini_rtksync.config import Settings
from omini_rtksync.models import ConnectionRecord
from omini_rtksync import render
from omini_rtksync import web as web_server

# Faixas de emoji que não podem aparecer na interface (o padrão é fonte de ícones).
EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]"
)


def make_conn(provider: str, name: str, data: dict) -> ConnectionRecord:
    return ConnectionRecord(id=f"id-{provider}", provider=provider, name=name, data=data)


class TestRenderHelpers(unittest.TestCase):
    def test_duration_formatting(self):
        self.assertEqual(render.format_duration(None), "Unlimited / N/A")
        self.assertEqual(render.format_duration(None, "pt"), "Ilimitado / N/A")
        self.assertEqual(render.format_duration(0), "Expired")
        self.assertEqual(render.format_duration(-5, "es"), "Expirado")
        self.assertEqual(render.format_duration(45), "45s")
        self.assertEqual(render.format_duration(1440), "24 min")
        self.assertEqual(render.format_duration(3660), "1h 01min")
        self.assertEqual(render.format_duration(90000), "1d 1h")

    def test_html_is_escaped(self):
        self.assertEqual(render.esc("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_refresh_reason_explains_why_nothing_was_renewed(self):
        """O caso real: 24 min restantes com margem de 15 min não renova — e isso precisa ficar visível."""
        now_ms = int(time.time() * 1000)
        conn = make_conn("antigravity", "Google Antigravity Pro", {
            "accessToken": "tok",
            "refreshToken": "ref",
            "expiresAt": now_ms + (24 * 60 * 1000),
        })
        self.assertIn("Outside the 15 min margin", render.render_refresh_reason(conn, 900))
        reason_pt = render.render_refresh_reason(conn, refresh_margin=900, lang="pt")
        self.assertIn("Fora da margem de 15 min", reason_pt)
        self.assertIn("renovação prevista", reason_pt)

    def test_refresh_reason_inside_margin(self):
        now_ms = int(time.time() * 1000)
        conn = make_conn("antigravity", "AG", {
            "accessToken": "tok", "refreshToken": "ref",
            "expiresAt": now_ms + (5 * 60 * 1000),
        })
        self.assertIn("Within the 15 min margin", render.render_refresh_reason(conn, 900))
        self.assertIn("Dentro da margem", render.render_refresh_reason(conn, 900, "pt"))

    def test_refresh_reason_for_expired_and_apikey(self):
        now_ms = int(time.time() * 1000)
        expired = make_conn("antigravity", "AG", {
            "accessToken": "t", "refreshToken": "r", "expiresAt": now_ms - 1000,
        })
        self.assertIn("expired", render.render_refresh_reason(expired, 900).lower())

        apikey = make_conn("groq", "Groq", {"apiKey": "gsk-xxx"})
        self.assertIn("never expires", render.render_refresh_reason(apikey, 900))
        self.assertIn("não expira", render.render_refresh_reason(apikey, 900, "pt"))

    def test_local_instance_reason_reports_models(self):
        """O Ollama local precisa aparecer como local, com os modelos que serve."""
        local = make_conn("openai-compatible-chat-ollama-local", "Ollama Local Host", {
            "apiKey": "fachada",
            "baseUrl": "http://localhost:11434/v1",
            "discoveredModels": ["llama3.2:3b", "qwen2.5-coder:7b"],
        })
        self.assertTrue(local.is_local)
        self.assertIn("2 model(s)", render.render_refresh_reason(local, 900))

    def test_unreachable_local_instance_is_reported(self):
        # Quem diz que a instancia nao respondeu e a sonda, gravada em
        # testStatus. Um catalogo vazio sozinho nao prova queda nenhuma:
        # instalacao nova, de pe e sem modelo baixado responde 200 com lista
        # vazia, e anuncia-la como inalcancavel contradizia o proprio ciclo.
        local = make_conn("ollama-local", "Ollama Local", {
            "apiKey": "k", "baseUrl": "http://localhost:11434/v1",
            "testStatus": "unreachable",
        })
        self.assertIn("did not answer", render.render_refresh_reason(local, 900))

    def test_an_empty_catalog_is_not_called_unreachable(self):
        local = make_conn("ollama-local", "Ollama Local", {
            "apiKey": "k", "baseUrl": "http://localhost:11434/v1",
            "testStatus": "active", "discoveredModels": [],
        })
        frase = render.render_refresh_reason(local, 900)
        self.assertNotIn("did not answer", frase)
        self.assertIn("no model installed", frase)


class TestDashboardMarkup(unittest.TestCase):
    def _page(self, **overrides):
        now_ms = int(time.time() * 1000)
        base = dict(
            connections=[
                make_conn("antigravity", "Google Antigravity Pro", {
                    "accessToken": "tok", "refreshToken": "ref",
                    "expiresAt": now_ms + (24 * 60 * 1000),
                }),
                make_conn("groq", "Groq Cloud PathBit", {"apiKey": "gsk-xxx"}),
            ],
            combos=[{"name": "arsenal-supremo", "kind": "fallback", "models": ["a", "b", "c"]}],
            cron={"active": True, "intervalSeconds": 300, "totalRuns": 4, "totalRenewals": 0,
                  "lastRunAt": "2026-09-12T13:46:53Z", "nextRunAt": "2026-09-12T13:51:53Z",
                  "lastResult": {"totalInspected": 7, "refreshedCount": 0, "durationMs": 5}},
            gateway={"url": "http://ominirtk-router:20128", "online": True, "statusCode": 200,
                     "latencyMs": 9, "dbSummary": "Operacional (7 conexoes, 5 combos)"},
            db_path="/app/data/db/data.sqlite",
            router_url="http://ominirtk-router:20128",
            current_user="admin",
            is_default_password=True,
            refresh_margin=900,
        )
        base.update(overrides)
        return render.render_dashboard(**base)

    def test_page_uses_icon_fonts_and_no_emoji(self):
        page = self._page()
        self.assertIn("bootstrap-icons", page)
        self.assertIn('class="bi bi-', page)
        found = EMOJI_PATTERN.findall(page)
        self.assertEqual(found, [], f"emojis encontrados na interface: {found}")

    def test_page_loads_bootstrap_and_jquery(self):
        page = self._page()
        self.assertIn("bootstrap@5", page)
        self.assertIn("jquery@3", page)

    def test_data_is_embedded_server_side(self):
        """A página chega pronta: nada de buscar dados do banco pelo navegador."""
        page = self._page()
        self.assertIn("Google Antigravity Pro", page)
        self.assertIn("Groq Cloud PathBit", page)
        self.assertIn("arsenal-supremo", page)
        self.assertNotIn("fetch(", page)
        self.assertNotIn("/api/status", page)

    def test_secrets_are_never_rendered(self):
        page = self._page()
        for secret in ("tok", "ref", "gsk-xxx"):
            self.assertNotIn(f">{secret}<", page)
        self.assertNotIn("gsk-xxx", page)

    def test_refresh_controls_are_present(self):
        page = self._page()
        self.assertIn('action="/acoes/atualizar"', page)     # botão Atualizar
        # Sincronizar dispara pelo agendador, para que a execucao manual
        # apareca no historico junto com as automaticas.
        self.assertIn('action="/acoes/cron"', page)   # Sincronizar agora
        self.assertIn('action="/acoes/cron"', page)          # Executar ciclo
        self.assertIn('action="/acoes/testar-gateway"', page)

    def test_security_banner_appears_only_with_default_password(self):
        self.assertIn("Security warning", self._page(is_default_password=True))
        self.assertNotIn("Security warning", self._page(is_default_password=False))

    def test_default_language_is_english_with_pt_and_es_available(self):
        page = self._page()
        self.assertIn('lang="en"', page)
        self.assertIn("Monitored connections", page)
        self.assertIn("flag-icons", page)
        for flag in ("fi-us", "fi-br", "fi-es"):
            self.assertIn(flag, page)

    def test_page_renders_in_portuguese_and_spanish(self):
        self.assertIn("Conexões monitoradas", self._page(lang="pt"))
        self.assertIn("Conexiones monitoreadas", self._page(lang="es"))

    def test_local_connection_shows_base_url_and_models(self):
        local = make_conn("openai-compatible-chat-ollama-local", "Ollama Local Host", {
            "apiKey": "fachada", "baseUrl": "http://localhost:11434/v1",
            "discoveredModels": ["llama3.2:3b", "qwen2.5-coder:7b"],
        })
        page = self._page(connections=[local])
        self.assertIn("http://localhost:11434/v1", page)
        self.assertIn("llama3.2:3b", page)
        self.assertIn("bi-hdd-network me-1", page)  # tipo renderizado como Local

    def test_cron_history_details_are_available(self):
        page = self._page(cron={
            "active": True, "intervalSeconds": 300, "totalRenewals": 0,
            "nextRunAt": "2026-09-12T13:51:53Z",
            "lastResult": {"totalInspected": 7, "refreshedCount": 0,
                           "durationMs": 5, "success": False, "error": "gateway offline"},
            "history": [
                {"timestamp": "2026-09-12T13:46:53Z", "totalInspected": 7, "refreshedCount": 0,
                 "durationMs": 5, "success": False, "error": "gateway offline",
                 "log": ["antigravity · AG: Validade proxima do fim"]},
            ],
        })
        self.assertIn("modalHistorico", page)
        self.assertIn("gateway offline", page)
        self.assertIn("Validade proxima do fim", page)
        self.assertIn("accordion", page)

    def test_cron_failure_is_flagged_on_the_card(self):
        page = self._page(cron={
            "active": True, "intervalSeconds": 300, "totalRenewals": 0,
            "lastResult": {"totalInspected": 1, "refreshedCount": 0, "durationMs": 3,
                           "success": False, "error": "boom"},
            "history": [],
        })
        self.assertIn("text-bg-danger", page)
        self.assertIn("boom", page)

    def test_env_mode_hides_the_password_form(self):
        page = self._page(auth_from_env=True)
        self.assertNotIn('action="/acoes/credenciais"', page)
        self.assertIn("DASHBOARD_USER", page)

    def test_injection_in_connection_name_is_escaped(self):
        evil = make_conn("evil", "<script>alert('xss')</script>", {"apiKey": "k"})
        page = self._page(connections=[evil])
        self.assertNotIn("<script>alert('xss')</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_empty_state_renders(self):
        page = self._page(connections=[], combos=[])
        self.assertIn("No connection registered", page)
        self.assertIn("No fallback combo", page)
        page_pt = self._page(connections=[], combos=[], lang="pt")
        self.assertIn("Nenhuma conexão registrada", page_pt)


class TestDashboardOverHttp(unittest.TestCase):
    """Verifica o SSR de ponta a ponta, com o servidor real no ar."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "data.sqlite")
        now_ms = int(time.time() * 1000)
        expiry_iso = datetime.fromtimestamp(
            (now_ms + 24 * 60 * 1000) / 1000, tz=timezone.utc
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute(
                "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT, "
                "access_token TEXT, refresh_token TEXT, api_key TEXT, expires_at TEXT, "
                "test_status TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE model_combos (id TEXT PRIMARY KEY, name TEXT, models TEXT, "
                "created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("c1", "antigravity", "Google Antigravity Pro", "tok", "ref", None,
                 expiry_iso, "active", "2026-09-12T00:00:00Z", "2026-09-12T00:00:00Z"),
            )

        cls.settings = Settings(
            db_path=cls.db_path, web_host="127.0.0.1", web_port=19393,
            dashboard_user="admin", dashboard_password="senha-forte",
            dashboard_auth_from_env=True,
        )
        cls.server = web_server.start_omini_web(
            "127.0.0.1", 19393, cls.db_path, omniroute_url="", settings=cls.settings
        )
        time.sleep(0.3)
        cls.auth = base64.b64encode(b"admin:senha-forte").decode()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    def _get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:19393{path}")
        req.add_header("Authorization", f"Basic {self.auth}")
        return urllib.request.urlopen(req, timeout=5)

    def test_dashboard_is_rendered_with_live_data(self):
        with self._get("/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers["Cache-Control"], "no-store, must-revalidate")
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
            body = resp.read().decode("utf-8")

        self.assertIn("Google Antigravity Pro", body)
        self.assertIn("bootstrap-icons", body)
        self.assertNotIn("tok", body.split("<script")[0].replace("token", ""))

    def test_unauthenticated_access_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen("http://127.0.0.1:19393/", timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_flash_message_is_rendered_from_the_query_string(self):
        with self._get("/?aviso=Ciclo+executado&tom=success") as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("Ciclo executado", body)
        self.assertIn("alert-success", body)

    def test_api_status_no_longer_allows_any_origin(self):
        with self._get("/api/status") as resp:
            self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()


class TestRemainingValidity(unittest.TestCase):
    """Um token OAuth nunca e ilimitado: sem validade legivel, o dado esta faltando."""

    def build(self, payload):
        row = {"id": "c1", "provider": "antigravity", "name": "Antigravity"}
        row.update(payload)
        return ConnectionRecord.from_row(row)

    def test_oauth_without_expiry_is_flagged_not_called_unlimited(self):
        page = render.render_remaining(self.build({"accessToken": "a", "refreshToken": "r"}), "en")
        self.assertIn("Expiry unknown", page)
        self.assertNotIn("Unlimited", page)

    def test_static_key_may_be_shown_without_expiry(self):
        page = render.render_remaining(self.build({"apiKey": "k"}), "en")
        self.assertIn("No expiry", page)

    def test_a_readable_expiry_is_shown_as_a_duration(self):
        conn = self.build({
            "accessToken": "a", "refreshToken": "r",
            "expiresAt": int(time.time() * 1000) + 3_600_000,
        })
        self.assertIn("min", render.render_remaining(conn, "en"))

    def test_every_language_has_both_labels(self):
        for lang in ("en", "pt", "es"):
            for key in ("duration.unknown_expiry", "duration.no_expiry"):
                self.assertTrue(i18n.translate(key, lang))
