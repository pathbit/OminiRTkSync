"""Regressões do despacho de providers, da persistência e da contagem de renovações.

Cada teste aqui nasceu de um apontamento da revisão automática do PR:

- uma conexão local carrega chave de fachada, então o handler genérico de chave
  a engolia antes do handler local e o catálogo nunca era descoberto;
- o resultado da sondagem era devolvido pelo provider e **descartado** pelo
  motor: o painel recarregava a linha antiga e uma chave recusada continuava
  verde na tela;
- carimbar o horário de uma verificação virava "credencial renovada";
- um catálogo vazio era relatado como instância fora do ar;
- 5xx do provedor virava "credencial válida";
- um `baseUrl` próprio (Azure, proxy) perdia para o casamento por substring.
"""

import os
import sqlite3
import tempfile
import unittest.mock

from omini_rtksync.credential_check import (
    STATE_UNREACHABLE,
    STATE_VALID,
    CheckResult,
    _classify,
    check_api_key,
)
from omini_rtksync.gateway import get_all_connections, update_connection_health
from omini_rtksync.gateway import ApiKeyProvider, LocalProvider


LOCAL = {
    "id": "c-local",
    "provider": "openai-compatible-ollama",
    "name": "Ollama Local",
    "hasApiKey": True,
    "apiKey": "chave-de-fachada",
    "isOAuth": False,
    "baseUrl": "http://127.0.0.1:11434/v1",
    "providerSpecificData": {"baseUrl": "http://127.0.0.1:11434/v1"},
}
NUVEM = {
    "id": "c-groq",
    "provider": "groq",
    "name": "Groq",
    "hasApiKey": True,
    "apiKey": "gsk_algo",
    "isOAuth": False,
}


class TestLocalNaoEEngolidaPeloHandlerDeChave(unittest.TestCase):
    def test_the_api_key_handler_declines_a_local_connection(self):
        self.assertFalse(ApiKeyProvider().can_handle(LOCAL))

    def test_the_local_handler_takes_it(self):
        self.assertTrue(LocalProvider().can_handle(LOCAL))

    def test_a_cloud_key_still_goes_to_the_api_key_handler(self):
        self.assertTrue(ApiKeyProvider().can_handle(NUVEM))
        self.assertFalse(LocalProvider().can_handle(NUVEM))


class TestVerificacaoNaoERenovacao(unittest.TestCase):
    def test_a_plain_validation_does_not_count_as_a_renewal(self):
        provider = ApiKeyProvider(validate_credentials=True)
        with unittest.mock.patch(
            "omini_rtksync.gateway.check_connection",
            return_value=CheckResult(state=STATE_VALID, detail="ok", checked_at="2026-01-01T00:00:00Z"),
        ):
            renewed, data, _ = provider.check_and_refresh(dict(NUVEM))
        self.assertFalse(renewed, "verificar chave nao e renovar chave")
        self.assertIsNotNone(data, "mas o resultado tem de ser gravado")
        self.assertEqual(data["testStatus"], "active")


class TestCatalogoVazioNaoEQueda(unittest.TestCase):
    def test_an_instance_answering_with_no_model_is_still_up(self):
        with unittest.mock.patch.object(LocalProvider, "discover_models", return_value=([], "")):
            _, data, msgs = LocalProvider().check_and_refresh(dict(LOCAL))
        self.assertEqual(data["testStatus"], "active")
        self.assertTrue(any("empty model catalog" in m for m in msgs))

    def test_an_instance_that_does_not_answer_is_unreachable(self):
        with unittest.mock.patch.object(LocalProvider, "discover_models", return_value=([], "Connection refused")):
            _, data, _ = LocalProvider().check_and_refresh(dict(LOCAL))
        self.assertEqual(data["testStatus"], "unreachable")


class TestErroDoProvedorNaoValidaCredencial(unittest.TestCase):
    def test_5xx_is_unreachable_not_valid(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(_classify(status), STATE_UNREACHABLE)

    def test_2xx_is_still_valid(self):
        self.assertEqual(_classify(200), STATE_VALID)


class TestEnderecoDeclaradoVenceONome(unittest.TestCase):
    def urls_sondadas(self, provider, base_url):
        vistas = []

        def espiao(request, timeout, opener=None, spec_invalid=()):
            vistas.append(request.full_url)
            return CheckResult(state=STATE_VALID, detail="ok", checked_at="2026-01-01T00:00:00Z")

        with unittest.mock.patch("omini_rtksync.credential_check._execute", side_effect=espiao):
            check_api_key(provider, "k", base_url=base_url)
        return vistas

    def test_an_azure_openai_key_never_reaches_api_openai_com(self):
        urls = self.urls_sondadas("azure-openai", "https://minha-org.openai.azure.com/v1")
        self.assertNotIn("api.openai.com", urls[0])
        self.assertIn("minha-org.openai.azure.com", urls[0])

    def test_the_vendor_endpoint_is_still_used_when_no_address_is_declared(self):
        urls = self.urls_sondadas("anthropic", None)
        self.assertIn("api.anthropic.com", urls[0])


class TestResultadoDaSondagemEGravado(unittest.TestCase):
    """O motor descartava o retorno do provider; esta é a prova de que agora grava."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "storage.sqlite")
        con = sqlite3.connect(self.db)
        # Recorte do schema real do OmniRoute, com as colunas que importam aqui.
        con.execute(
            "CREATE TABLE provider_connections ("
            "id TEXT PRIMARY KEY, provider TEXT, name TEXT, access_token TEXT, "
            "refresh_token TEXT, api_key TEXT, expires_at TEXT, test_status TEXT, "
            "last_tested TEXT, last_health_check_at TEXT, last_error TEXT, "
            "rate_limited_until TEXT, provider_specific_data TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        con.execute(
            "INSERT INTO provider_connections (id, provider, name, api_key, test_status) "
            "VALUES ('c1','groq','Groq','gsk_x','active')"
        )
        con.commit()
        con.close()

    def linha(self):
        return get_all_connections(self.db)[0]

    def test_a_rejected_key_stops_being_green(self):
        self.assertEqual(self.linha()["testStatus"], "active")
        update_connection_health(
            self.db, "c1", test_status="invalid", credential_state="invalid", last_error="HTTP 401"
        )
        atual = self.linha()
        self.assertEqual(atual["testStatus"], "invalid")
        self.assertEqual(atual["credentialState"], "invalid")
        self.assertEqual(atual["lastError"], "HTTP 401")

    def test_the_check_timestamp_feeds_the_last_renewal_column(self):
        self.assertIsNone(self.linha()["lastTested"])
        update_connection_health(self.db, "c1", test_status="active")
        self.assertIsNotNone(self.linha()["lastTested"])

    def test_the_local_catalog_is_persisted(self):
        update_connection_health(self.db, "c1", discovered_models=["llama3.2:3b", "qwen2.5:7b"])
        self.assertEqual(self.linha()["discoveredModels"], ["llama3.2:3b", "qwen2.5:7b"])

    def test_writing_health_never_touches_the_credential(self):
        update_connection_health(self.db, "c1", test_status="invalid", discovered_models=[])
        con = sqlite3.connect(self.db)
        chave = con.execute("SELECT api_key FROM provider_connections WHERE id='c1'").fetchone()[0]
        con.close()
        self.assertEqual(chave, "gsk_x")

    def test_an_unknown_connection_reports_nothing_written(self):
        self.assertFalse(update_connection_health(self.db, "nao-existe", test_status="invalid"))


class TestLinhaCruaNaoVaza(unittest.TestCase):
    """`raw` devolvia a linha inteira do banco, com os três segredos juntos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "storage.sqlite")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE provider_connections ("
            "id TEXT PRIMARY KEY, provider TEXT, name TEXT, access_token TEXT, "
            "refresh_token TEXT, api_key TEXT, expires_at TEXT, test_status TEXT, "
            "provider_specific_data TEXT, created_at TEXT, updated_at TEXT)"
        )
        con.execute(
            "INSERT INTO provider_connections (id, provider, name, access_token, refresh_token, api_key) "
            "VALUES ('c1','groq','Groq','ya29.SEGREDO','1//SEGREDO','gsk_SEGREDO')"
        )
        con.commit()
        con.close()

    def test_the_projection_has_no_raw_row(self):
        self.assertNotIn("raw", get_all_connections(self.db)[0])

    def test_the_base_url_still_resolves_from_provider_specific_data(self):
        con = sqlite3.connect(self.db)
        con.execute(
            "UPDATE provider_connections SET provider_specific_data = ? WHERE id='c1'",
            ('{"baseUrl": "http://127.0.0.1:11434/v1"}',),
        )
        con.commit()
        con.close()
        self.assertEqual(get_all_connections(self.db)[0]["baseUrl"], "http://127.0.0.1:11434/v1")


if __name__ == "__main__":
    unittest.main()


class TestSaidaDeRedePorConta(unittest.TestCase):
    """Qual endereco de saida cada conta usa — leitura, nunca escrita.

    Compartilhar um unico endereco de saida entre varias contas do mesmo
    fornecedor e o estado que mais preocupa o operador, e nada no painel
    mostrava isso. O OmniRoute ja modela tudo: os interruptores ficam em
    `provider_connections` (`proxy_enabled`, `per_key_proxy_enabled`) e o
    vinculo em `proxy_assignments` com `scope='account'`.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "storage.sqlite")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE provider_connections ("
            "id TEXT PRIMARY KEY, provider TEXT, name TEXT, api_key TEXT, "
            "test_status TEXT, proxy_enabled INTEGER, per_key_proxy_enabled INTEGER, "
            "provider_specific_data TEXT, created_at TEXT, updated_at TEXT)"
        )
        con.execute(
            "CREATE TABLE proxy_registry (id TEXT PRIMARY KEY, name TEXT, type TEXT, "
            "host TEXT, port INTEGER, status TEXT)"
        )
        con.execute(
            "CREATE TABLE proxy_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "proxy_id TEXT NOT NULL, scope TEXT NOT NULL, scope_id TEXT, "
            "position INTEGER NOT NULL DEFAULT 0)"
        )
        con.executemany(
            "INSERT INTO provider_connections (id, provider, name, api_key, test_status, "
            "proxy_enabled, per_key_proxy_enabled) VALUES (?,?,?,?,?,?,?)",
            [
                ("acc-1", "anthropic", "Conta A", "k1", "active", 1, 0),
                ("acc-2", "anthropic", "Conta B", "k2", "active", 0, 0),
            ],
        )
        con.execute(
            "INSERT INTO proxy_registry VALUES ('p-eu','Saida Frankfurt','http','10.8.0.21',8080,'active')"
        )
        con.execute(
            "INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position) "
            "VALUES ('p-eu','account','acc-1',0)"
        )
        con.commit()
        con.close()

    def por_id(self):
        return {c["id"]: c for c in get_all_connections(self.db)}

    def test_an_account_with_its_own_egress_is_reported_as_bound(self):
        conta = self.por_id()["acc-1"]
        self.assertEqual(conta["egressProxy"], "Saida Frankfurt")

    def test_an_account_without_a_binding_reports_a_shared_egress(self):
        conta = self.por_id()["acc-2"]
        self.assertIsNone(conta["egressProxy"])

    def test_an_assignment_of_another_scope_never_binds_the_account(self):
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO proxy_assignments (proxy_id, scope, scope_id, position) "
            "VALUES ('p-eu','provider','anthropic',0)"
        )
        con.commit()
        con.close()
        # Escopo de provedor nao e vinculo de conta: acc-2 continua compartilhada.
        self.assertIsNone(self.por_id()["acc-2"]["egressProxy"])

    def test_reading_the_binding_writes_nothing(self):
        antes = sqlite3.connect(self.db).execute(
            "SELECT count(*) FROM proxy_assignments"
        ).fetchone()[0]
        get_all_connections(self.db)
        depois = sqlite3.connect(self.db).execute(
            "SELECT count(*) FROM proxy_assignments"
        ).fetchone()[0]
        self.assertEqual(antes, depois)

    def test_an_older_schema_without_the_proxy_tables_still_lists_connections(self):
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE proxy_assignments")
        con.execute("DROP TABLE proxy_registry")
        con.commit()
        con.close()
        conexoes = get_all_connections(self.db)
        self.assertEqual(len(conexoes), 2)
        self.assertIsNone(conexoes[0]["egressProxy"])


class TestOllamaHospedadoNaoELocal(unittest.TestCase):
    """O marcador "ollama" também casa com a conta hospedada.

    Tratar `https://ollama.com/v1` como local mandaria o sincronizador sondar um
    catálogo que não existe ali, e tiraria a conexão do caminho de validação de
    chave — que é justamente onde ela precisa estar.
    """

    HOSPEDADA = {
        "id": "c-nuvem", "provider": "ollama", "name": "Ollama Cloud",
        "hasApiKey": True, "apiKey": "k", "isOAuth": False,
        "baseUrl": "https://ollama.com/v1",
        "providerSpecificData": {"baseUrl": "https://ollama.com/v1"},
    }
    LOCAL = {
        "id": "c-local", "provider": "ollama", "name": "Ollama Local",
        "hasApiKey": True, "apiKey": "k", "isOAuth": False,
        "baseUrl": "http://127.0.0.1:11434/v1",
        "providerSpecificData": {"baseUrl": "http://127.0.0.1:11434/v1"},
    }

    def test_the_hosted_account_goes_to_the_api_key_handler(self):
        self.assertFalse(LocalProvider.is_local_connection(self.HOSPEDADA))
        self.assertTrue(ApiKeyProvider().can_handle(self.HOSPEDADA))

    def test_the_local_instance_still_goes_to_the_local_handler(self):
        self.assertTrue(LocalProvider.is_local_connection(self.LOCAL))
        self.assertFalse(ApiKeyProvider().can_handle(self.LOCAL))

    def test_without_a_declared_address_the_name_still_decides(self):
        sem_endereco = {"id": "c", "provider": "ollama", "name": "x", "hasApiKey": True}
        self.assertTrue(LocalProvider.is_local_connection(sem_endereco))
