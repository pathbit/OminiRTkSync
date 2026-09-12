"""Testes da validacao viva de credenciais.

Nenhum teste aqui toca a rede: todo probe recebe um opener falso. Foi um teste
que dependia de um servico real que derrubou a CI antes.
"""

import unittest
import urllib.error

from omini_rtksync import credential_check as cc
from omini_rtksync.models import ConnectionRecord
from omini_rtksync.providers import ApiKeyProvider


class FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def opener_returning(status: int):
    """Opener que responde com o status pedido e registra a requisicao."""
    captured = {}

    def _opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, None)
        return FakeResponse(status)

    _opener.captured = captured
    return _opener


def opener_raising(exc: Exception):
    def _opener(request, timeout=None):
        raise exc

    return _opener


class TestClassification(unittest.TestCase):
    def test_200_is_valid(self):
        r = cc.check_api_key("groq", "k", opener=opener_returning(200))
        self.assertEqual(r.state, cc.STATE_VALID)
        self.assertEqual(r.http_status, 200)

    def test_401_is_invalid(self):
        r = cc.check_api_key("groq", "k", opener=opener_returning(401))
        self.assertEqual(r.state, cc.STATE_INVALID)

    def test_403_is_invalid(self):
        r = cc.check_api_key("groq", "k", opener=opener_returning(403))
        self.assertEqual(r.state, cc.STATE_INVALID)

    def test_429_is_rate_limited(self):
        r = cc.check_api_key("groq", "k", opener=opener_returning(429))
        self.assertEqual(r.state, cc.STATE_RATE_LIMITED)

    def test_404_still_means_the_key_was_accepted(self):
        """Validamos a credencial, nao o modelo: 404 de modelo nao invalida a chave."""
        r = cc.check_api_key("groq", "k", opener=opener_returning(404))
        self.assertEqual(r.state, cc.STATE_VALID)

    def test_network_failure_is_unreachable_not_invalid(self):
        """Sem resposta a credencial fica nao comprovada, nunca comprovadamente ruim."""
        r = cc.check_api_key("groq", "k", opener=opener_raising(OSError("connection refused")))
        self.assertEqual(r.state, cc.STATE_UNREACHABLE)

    def test_missing_key_is_unsupported(self):
        r = cc.check_api_key("groq", "")
        self.assertEqual(r.state, cc.STATE_UNSUPPORTED)


class TestProbeSelection(unittest.TestCase):
    def test_openrouter_uses_the_key_endpoint_not_the_public_catalog(self):
        """/api/v1/models responde 200 sem credencial nenhuma: validaria qualquer lixo."""
        op = opener_returning(200)
        cc.check_api_key("openrouter", "k", opener=op)
        self.assertEqual(op.captured["url"], "https://openrouter.ai/api/v1/key")

    def test_gemini_authenticates_by_header_and_treats_400_as_invalid(self):
        op = opener_returning(400)
        r = cc.check_api_key("gemini", "k", opener=op)
        self.assertIn("x-goog-api-key", op.captured["headers"])
        self.assertEqual(r.state, cc.STATE_INVALID)

    def test_gemini_400_is_invalid_but_groq_400_is_not(self):
        r = cc.check_api_key("groq", "k", opener=opener_returning(400))
        self.assertEqual(r.state, cc.STATE_VALID)

    def test_ollama_cloud_posts_because_the_catalog_is_public(self):
        op = opener_returning(200)
        cc.check_api_key("ollama", "k", opener=op)
        self.assertEqual(op.captured["method"], "POST")
        self.assertIn("ollama.com", op.captured["url"])

    def test_self_hosted_name_never_resolves_to_a_vendor_url(self):
        """'openai-compatible-chat-ollama-local' casa com 'openai' e com 'ollama';
        sem barreira, a chave de fachada de um Ollama local iria para a api.openai.com."""
        self.assertIsNone(cc.select_probe("openai-compatible-chat-ollama-local"))
        self.assertIsNone(cc.select_probe("localai"))

    def test_marker_choice_is_deterministic(self):
        self.assertEqual(cc.select_probe("groq-cloud").url, "https://api.groq.com/openai/v1/models")
        self.assertEqual(cc.select_probe("ollama").url, "https://ollama.com/v1/chat/completions")

    def test_self_hosted_falls_back_to_its_own_address(self):
        op = opener_returning(200)
        r = cc.check_api_key(
            "openai-compatible-chat-ollama-local",
            "k",
            base_url="http://host.docker.internal:11434/v1",
            opener=op,
        )
        self.assertEqual(r.state, cc.STATE_VALID)
        self.assertEqual(op.captured["url"], "http://host.docker.internal:11434/v1/models")

    def test_unknown_provider_without_address_is_unsupported(self):
        r = cc.check_api_key("provedor-desconhecido", "k", opener=opener_returning(200))
        self.assertEqual(r.state, cc.STATE_UNSUPPORTED)

    def test_unknown_provider_with_address_uses_the_openai_convention(self):
        op = opener_returning(200)
        r = cc.check_api_key(
            "provedor-desconhecido", "k", base_url="https://api.exemplo.com/v1", opener=op
        )
        self.assertEqual(r.state, cc.STATE_VALID)
        self.assertEqual(op.captured["url"], "https://api.exemplo.com/v1/models")


class TestOAuthProbe(unittest.TestCase):
    def test_live_token_is_valid(self):
        r = cc.check_oauth_token("ya29.token", opener=opener_returning(200))
        self.assertEqual(r.state, cc.STATE_VALID)

    def test_dead_token_answers_400_and_is_invalid(self):
        """tokeninfo devolve 400, nao 401, para token morto."""
        r = cc.check_oauth_token("ya29.morto", opener=opener_returning(400))
        self.assertEqual(r.state, cc.STATE_INVALID)

    def test_token_travels_in_the_query(self):
        op = opener_returning(200)
        cc.check_oauth_token("ya29.abc", opener=op)
        self.assertIn("access_token=ya29.abc", op.captured["url"])


class TestConnectionDispatch(unittest.TestCase):
    def build(self, provider, payload):
        row = {"id": "c1", "provider": provider, "name": provider}
        row.update(payload)
        row["isOAuth"] = bool(payload.get("accessToken") or payload.get("refreshToken"))
        row["hasApiKey"] = bool(payload.get("apiKey"))
        return ConnectionRecord.from_row(row)

    def test_oauth_connection_checks_the_token(self):
        conn = self.build("antigravity", {"accessToken": "ya29.x", "refreshToken": "r"})
        op = opener_returning(200)
        r = cc.check_connection(conn, opener=op)
        self.assertEqual(r.state, cc.STATE_VALID)
        self.assertIn("tokeninfo", op.captured["url"])

    def test_api_key_connection_checks_the_key(self):
        conn = self.build("groq", {"apiKey": "gsk_x"})
        op = opener_returning(200)
        cc.check_connection(conn, opener=op)
        self.assertIn("groq.com", op.captured["url"])

    def test_local_instance_is_not_sent_to_a_cloud_api(self):
        conn = self.build(
            "openai-compatible-chat-ollama-local",
            {"apiKey": "x", "providerSpecificData": {"baseUrl": "http://host.docker.internal:11434/v1"}},
        )
        r = cc.check_connection(conn, opener=opener_returning(200))
        self.assertEqual(r.state, cc.STATE_UNSUPPORTED)
        self.assertIn("Local instance", r.detail)


class TestApiKeyProviderIntegration(unittest.TestCase):
    def build(self, provider, payload):
        row = {"id": "c1", "provider": provider, "name": provider}
        row.update(payload)
        row["isOAuth"] = bool(payload.get("accessToken") or payload.get("refreshToken"))
        row["hasApiKey"] = bool(payload.get("apiKey"))
        return ConnectionRecord.from_row(row)

    def test_validation_is_off_unless_asked(self):
        """Protege a CI: montar o provider nao pode gerar trafego de saida."""
        provider = ApiKeyProvider()
        self.assertFalse(provider.validate_credentials)

    def row(self, provider, payload):
        row = {"id": "c1", "provider": provider, "name": provider}
        row.update(payload)
        row["hasApiKey"] = bool(payload.get("apiKey"))
        return row

    def test_rejected_key_is_never_stamped_as_active(self):
        """O bug original: a conexao era declarada ativa sem perguntar a ninguem."""
        provider = ApiKeyProvider(validate_credentials=True, opener=opener_returning(401))
        _, data, msgs = provider.check_and_refresh(
            self.row("groq", {"apiKey": "revogada", "testStatus": "active"})
        )
        self.assertEqual(data["testStatus"], "invalid")
        self.assertEqual(data["credentialState"], cc.STATE_INVALID)
        self.assertTrue(any("RECUSADA" in m for m in msgs))

    def test_accepted_key_is_stamped_active_with_evidence(self):
        provider = ApiKeyProvider(validate_credentials=True, opener=opener_returning(200))
        _, data, _ = provider.check_and_refresh(self.row("groq", {"apiKey": "boa"}))
        self.assertEqual(data["testStatus"], "active")
        self.assertEqual(data["credentialState"], cc.STATE_VALID)
        self.assertTrue(data["credentialCheckedAt"])

    def test_unreachable_provider_does_not_mark_the_key_invalid(self):
        provider = ApiKeyProvider(
            validate_credentials=True, opener=opener_raising(OSError("timeout"))
        )
        _, data, _ = provider.check_and_refresh(
            self.row("groq", {"apiKey": "boa", "testStatus": "active"})
        )
        self.assertNotEqual(data.get("testStatus"), "invalid")
        self.assertEqual(data["credentialState"], cc.STATE_UNREACHABLE)


class TestHealthStatusUsesTheProbe(unittest.TestCase):
    def build(self, provider, payload):
        row = {"id": "c1", "provider": provider, "name": provider}
        row.update(payload)
        row["isOAuth"] = bool(payload.get("accessToken") or payload.get("refreshToken"))
        row["hasApiKey"] = bool(payload.get("apiKey"))
        return ConnectionRecord.from_row(row)

    def test_key_never_probed_is_not_claimed_healthy(self):
        conn = self.build("groq", {"apiKey": "x"})
        self.assertEqual(conn.health_status, "not_checked")

    def test_probed_valid_key_is_active(self):
        conn = self.build("groq", {"apiKey": "x", "credentialState": "valid"})
        self.assertEqual(conn.health_status, "active")

    def test_probed_invalid_key_overrides_everything(self):
        conn = self.build("groq", {"apiKey": "x", "testStatus": "ok", "credentialState": "invalid"})
        self.assertEqual(conn.health_status, "invalid")

    def test_invalid_probe_beats_a_healthy_oauth_expiry(self):
        conn = self.build(
            "antigravity",
            {"accessToken": "a", "refreshToken": "r", "expiresAt": 9_999_999_999_999,
             "credentialState": "invalid"},
        )
        self.assertEqual(conn.health_status, "invalid")


if __name__ == "__main__":
    unittest.main()
