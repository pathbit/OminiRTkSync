"""Regressões da terceira rodada de revisão automática do PR.

O que cada grupo protege, em uma frase:

- a trava de rate limit é um **prazo**, e uma trava vencida deixava a conexão
  amarela para sempre porque nada apagava a marca;
- uma instância local recém-criada dizia "ativa" sem nunca ter sido sondada;
- uma conexão OAuth que o próprio gateway carimbou como recusada aparecia
  saudável só porque o token ainda não tinha vencido;
- o painel anunciava "instância inalcançável" para um catálogo vazio que o
  ciclo acabara de gravar como ativo;
- a sondagem era descartada inteira em bases com schema de coluna JSON;
- `lastRefreshAt` era lido pelo painel e nunca escrito por ninguém.
"""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from omini_rtksync.database import update_connection, update_connection_health
from omini_rtksync.models import ConnectionRecord
from omini_rtksync.render import render_refresh_reason


def agora_mais(segundos: int) -> str:
    """Timestamp ISO deslocado a partir de agora."""
    return (datetime.now(timezone.utc) + timedelta(seconds=segundos)).isoformat().replace(
        "+00:00", "Z"
    )


def conexao(**dados) -> ConnectionRecord:
    base = {"id": "c1", "provider": "groq", "name": "Teste"}
    base.update(dados)
    return ConnectionRecord(id="c1", provider=base["provider"], name=base["name"], data=base)


class TestTravaDeRateLimitVencida(unittest.TestCase):
    def test_a_hold_still_in_the_future_keeps_the_connection_limited(self):
        c = conexao(apiKey="gsk_x", hasApiKey=True, rateLimitedUntil=agora_mais(600))
        self.assertTrue(c.rate_limit_active)
        self.assertEqual(c.health_status, "rate_limited")

    def test_an_expired_hold_releases_the_connection(self):
        c = conexao(
            apiKey="gsk_x",
            hasApiKey=True,
            rateLimitedUntil=agora_mais(-600),
            credentialState="valid",
        )
        self.assertFalse(c.rate_limit_active, "a janela do provedor ja reabriu")
        self.assertEqual(
            c.health_status,
            "active",
            "uma trava vencida mantinha a conexao amarela para sempre",
        )

    def test_an_epoch_in_milliseconds_is_understood_too(self):
        futuro = int((time.time() + 600) * 1000)
        self.assertTrue(conexao(apiKey="k", hasApiKey=True, rateLimitedUntil=futuro).rate_limit_active)
        passado = int((time.time() - 600) * 1000)
        self.assertFalse(conexao(apiKey="k", hasApiKey=True, rateLimitedUntil=passado).rate_limit_active)

    def test_no_hold_at_all_is_not_a_hold(self):
        self.assertFalse(conexao(apiKey="k", hasApiKey=True).rate_limit_active)


class TestLocalNaoSondadaNaoAlegaSaude(unittest.TestCase):
    def test_a_brand_new_local_connection_is_not_checked_yet(self):
        c = conexao(provider="ollama", baseUrl="http://127.0.0.1:11434/v1")
        self.assertEqual(
            c.health_status,
            "not_checked",
            "sem sonda nenhuma, dizer 'ativa' e alegar saude que ninguem verificou",
        )

    def test_a_probed_local_connection_is_active(self):
        c = conexao(provider="ollama", baseUrl="http://127.0.0.1:11434/v1", testStatus="active")
        self.assertEqual(c.health_status, "active")

    def test_a_local_instance_that_did_not_answer_is_unknown(self):
        c = conexao(provider="ollama", baseUrl="http://127.0.0.1:11434/v1", testStatus="unreachable")
        self.assertEqual(c.health_status, "unknown")


class TestCarimboDoGatewayVale(unittest.TestCase):
    def test_an_oauth_connection_the_gateway_rejected_is_invalid(self):
        c = conexao(
            provider="github",
            accessToken="tok",
            isOAuth=True,
            expiresAt=agora_mais(7200),
            testStatus="invalid",
        )
        self.assertEqual(
            c.health_status,
            "invalid",
            "o gateway ja sabe que a credencial esta quebrada; ignorar isso mostra saude falsa",
        )

    def test_a_live_probe_outranks_a_stale_gateway_stamp(self):
        # `test_status` guarda o ultimo erro do gateway e nao caduca sozinho.
        # Depois que a sonda viva aprova a credencial, insistir no carimbo
        # antigo repetia, ao contrario, a contradicao entre tela e banco.
        c = conexao(
            provider="github",
            accessToken="tok",
            isOAuth=True,
            expiresAt=agora_mais(7200),
            testStatus="invalid",
            credentialState="valid",
        )
        self.assertEqual(
            c.health_status,
            "active",
            "a validacao viva vence o carimbo velho do gateway",
        )

    def test_a_healthy_oauth_connection_is_still_active(self):
        c = conexao(
            provider="github",
            accessToken="tok",
            isOAuth=True,
            expiresAt=agora_mais(7200),
            testStatus="active",
        )
        self.assertEqual(c.health_status, "active")


class TestPainelNaoContradizOBanco(unittest.TestCase):
    def test_an_empty_catalog_is_not_reported_as_unreachable(self):
        c = conexao(
            provider="ollama",
            baseUrl="http://127.0.0.1:11434/v1",
            testStatus="active",
            discoveredModels=[],
        )
        frase = render_refresh_reason(c, refresh_margin=900, lang="pt")
        self.assertNotIn("não respondeu", frase)
        self.assertIn("nenhum modelo", frase)

    def test_an_instance_that_really_did_not_answer_still_says_so(self):
        c = conexao(
            provider="ollama",
            baseUrl="http://127.0.0.1:11434/v1",
            testStatus="unreachable",
            discoveredModels=[],
        )
        self.assertIn("não respondeu", render_refresh_reason(c, refresh_margin=900, lang="pt"))


class TestPersistenciaNoSchemaJson(unittest.TestCase):
    """Bases com coluna `data` única perdiam a sondagem inteira."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "storage.sqlite")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE providerConnections (id TEXT PRIMARY KEY, provider TEXT, name TEXT,"
            " data TEXT, createdAt TEXT, updatedAt TEXT)"
        )
        conn.execute(
            "INSERT INTO providerConnections VALUES (?,?,?,?,?,?)",
            (
                "c1",
                "groq",
                "Teste",
                json.dumps({"apiKey": "gsk_x", "rateLimitedUntil": agora_mais(-60)}),
                "2026-01-01",
                "2026-01-01",
            ),
        )
        conn.commit()
        conn.close()

    def ler(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        linha = conn.execute("SELECT data FROM providerConnections WHERE id='c1'").fetchone()
        conn.close()
        return json.loads(linha["data"])

    def test_the_probe_result_reaches_a_json_schema_database(self):
        ok = update_connection_health(
            self.db,
            "c1",
            test_status="invalid",
            credential_state="invalid",
            last_error="401 do provedor",
            clear_rate_limit=True,
        )
        self.assertTrue(ok, "a sondagem tem de ser gravada tambem neste schema")
        d = self.ler()
        self.assertEqual(d["testStatus"], "invalid")
        self.assertEqual(d["credentialState"], "invalid")
        self.assertEqual(d["lastError"], "401 do provedor")
        self.assertNotIn("rateLimitedUntil", d, "a trava vencida tinha de sair")
        self.assertIn("lastTested", d)

    def test_the_existing_content_is_preserved(self):
        update_connection_health(self.db, "c1", test_status="active")
        self.assertEqual(self.ler()["apiKey"], "gsk_x", "gravar saude nao pode apagar a credencial")

    def test_the_renewal_stamps_its_own_timestamp(self):
        update_connection(self.db, "c1", "novo-token", "novo-refresh", int(time.time() * 1000))
        d = self.ler()
        self.assertIn("lastRefreshAt", d, "renovar e verificar sao eventos diferentes")
        self.assertEqual(d["accessToken"], "novo-token")


class TestCarimboDeRenovacaoNoSchemaRelacional(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "storage.sqlite")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT,"
            " access_token TEXT, refresh_token TEXT, api_key TEXT, expires_at TEXT,"
            " test_status TEXT, provider_specific_data TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "c1",
                "google",
                "Conta",
                "velho",
                "refresh",
                None,
                "2026-01-01T00:00:00Z",
                "active",
                json.dumps({"baseUrl": "https://x.example"}),
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()
        conn.close()

    def test_the_renewal_timestamp_is_written_without_losing_the_rest(self):
        update_connection(self.db, "c1", "token-novo", "refresh-novo", int(time.time() * 1000))
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        linha = conn.execute("SELECT * FROM provider_connections WHERE id='c1'").fetchone()
        conn.close()
        especifico = json.loads(linha["provider_specific_data"])
        self.assertIn("lastRefreshAt", especifico)
        self.assertEqual(
            especifico["baseUrl"], "https://x.example", "o endereço declarado nao pode ser perdido"
        )
        self.assertEqual(linha["access_token"], "token-novo")


class TestTravaVencidaSaiNoCicloReal(unittest.TestCase):
    """A limpeza vale para qualquer conexão, não só para as de chave de API.

    O primeiro corte tratava a trava dentro do ramo de chave de API. Uma
    conexão OAuth — que é o caso comum no OmniRoute — passava longe dele e a
    marca vencida continuava gravada, deixando a conexão amarela para sempre.
    Este teste roda o ciclo inteiro contra um banco no schema relacional real.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "storage.sqlite")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT,"
            " access_token TEXT, refresh_token TEXT, api_key TEXT, expires_at TEXT,"
            " test_status TEXT, rate_limited_until TEXT, last_tested TEXT, last_error TEXT,"
            " provider_specific_data TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "c1",
                "github",
                "Conta OAuth",
                "tok",
                "ref",
                None,
                agora_mais(7200),
                "active",
                agora_mais(-7200),  # trava vencida há duas horas
                None,
                None,
                None,
                "2026-01-01",
                "2026-01-01",
            ),
        )
        conn.commit()
        conn.close()

    def trava_gravada(self):
        conn = sqlite3.connect(self.db)
        v = conn.execute("SELECT rate_limited_until FROM provider_connections WHERE id='c1'").fetchone()[0]
        conn.close()
        return v

    def test_an_expired_hold_is_cleared_for_an_oauth_connection(self):
        from omini_rtksync.cli import OmniSyncEngine
        from omini_rtksync.config import Settings

        self.assertIsNotNone(self.trava_gravada())
        motor = OmniSyncEngine(Settings(db_path=self.db, enable_web=False, validate_credentials=False))
        motor.sync_all()
        self.assertIsNone(
            self.trava_gravada(),
            "a janela do provedor reabriu ha duas horas; a marca tinha de sair",
        )


if __name__ == "__main__":
    unittest.main()


class TestFraseDaSondagem(unittest.TestCase):
    """Um 400 prova que a autenticação passou; a frase tem de dizer isso."""

    def mensagens(self, estado, detalhe):
        import unittest.mock
        from omini_rtksync.credential_check import CheckResult
        from omini_rtksync.providers import ApiKeyProvider

        provider = ApiKeyProvider(validate_credentials=True)
        with unittest.mock.patch(
            "omini_rtksync.providers.check_connection",
            return_value=CheckResult(state=estado, detail=detalhe, checked_at="2026-01-01T00:00:00Z"),
        ):
            _, _, msgs = provider.check_and_refresh(
                {"id": "c1", "provider": "groq", "name": "T", "apiKey": "gsk_a"}
            )
        return msgs

    def test_a_plain_200_reads_as_a_clean_acceptance(self):
        from omini_rtksync.credential_check import STATE_VALID

        m = " ".join(self.mensagens(STATE_VALID, "HTTP 200"))
        self.assertIn("Autenticação aceita", m)
        self.assertNotIn("recusada", m)

    def test_a_400_says_what_the_number_means(self):
        from omini_rtksync.credential_check import STATE_VALID

        m = " ".join(self.mensagens(STATE_VALID, "HTTP 400"))
        self.assertIn("Autenticação aceita", m)
        self.assertIn("sondagem em si foi recusada", m)
