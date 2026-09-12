"""Tests for local instance discovery (Ollama, vLLM, LM Studio)."""

import unittest
import unittest.mock

from omini_rtksync.providers import LocalProvider


class TestLocalProviderDetection(unittest.TestCase):
    def setUp(self):
        self.provider = LocalProvider()

    def test_recognises_local_provider_names(self):
        for name in ("ollama", "openai-compatible-chat-ollama-local", "vllm-host",
                     "lmstudio", "localai", "llamacpp-server"):
            with self.subTest(provider=name):
                # A facade API key must not turn a local instance into a cloud provider.
                self.assertTrue(self.provider.can_handle({"provider": name, "hasApiKey": True}))

    def test_recognises_a_local_base_url(self):
        for url in ("http://localhost:11434/v1", "http://127.0.0.1:8000/v1",
                    "http://host.docker.internal:11434"):
            with self.subTest(url=url):
                self.assertTrue(
                    self.provider.can_handle({"provider": "custom", "hasApiKey": True, "baseUrl": url})
                )

    def test_does_not_claim_cloud_providers(self):
        self.assertFalse(
            self.provider.can_handle(
                {"provider": "groq", "hasApiKey": True, "baseUrl": "https://api.groq.com/openai/v1"}
            )
        )


class TestModelDiscovery(unittest.TestCase):
    def setUp(self):
        self.provider = LocalProvider()

    def test_parses_the_ollama_catalog_shape(self):
        payload = {"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5-coder:7b"}]}
        self.assertEqual(
            LocalProvider._extract_model_names(payload), ["llama3.2:3b", "qwen2.5-coder:7b"]
        )

    def test_parses_the_openai_catalog_shape(self):
        payload = {"data": [{"id": "gpt-oss:20b"}, {"id": "phi4"}]}
        self.assertEqual(LocalProvider._extract_model_names(payload), ["gpt-oss:20b", "phi4"])

    def test_ignores_unusable_payloads(self):
        for payload in ([], None, {"models": []}, {"other": [1, 2]}, "text"):
            with self.subTest(payload=payload):
                self.assertEqual(LocalProvider._extract_model_names(payload), [])

    def test_missing_base_url_is_reported(self):
        models, error = self.provider.discover_models("")
        self.assertEqual(models, [])
        self.assertIn("baseUrl", error)

    def test_unreachable_instance_stops_after_the_first_attempt(self):
        """Trying all three endpoints against a dead host just triples the timeout."""
        with unittest.mock.patch("omini_rtksync.providers.urllib.request.urlopen",
                        side_effect=OSError("Connection refused")) as urlopen:
            models, error = self.provider.discover_models("http://127.0.0.1:11434/v1")
        self.assertEqual(models, [])
        self.assertIn("Connection refused", error)
        self.assertEqual(urlopen.call_count, 1)


class TestCheckAndRefresh(unittest.TestCase):
    def setUp(self):
        self.provider = LocalProvider()
        self.conn = {"provider": "ollama-local", "baseUrl": "http://127.0.0.1:11434/v1"}

    def test_reachable_instance_records_its_models(self):
        with unittest.mock.patch.object(LocalProvider, "discover_models", return_value=(["llama3.2:3b"], "")):
            renewed, data, messages = self.provider.check_and_refresh(self.conn)
        # Sondagem local nao conta como renovacao de credencial; o que prova que
        # funcionou e o dicionario devolvido para gravacao.
        self.assertFalse(renewed)
        self.assertIsNotNone(data)
        self.assertEqual(data["discoveredModels"], ["llama3.2:3b"])
        # 'active' is the only value OmniRoute treats as healthy.
        self.assertEqual(data["testStatus"], "active")
        self.assertIn("1 model(s)", messages[0])

    def test_unreachable_instance_is_not_assumed_healthy(self):
        with unittest.mock.patch.object(LocalProvider, "discover_models", return_value=([], "Connection refused")):
            renewed, data, messages = self.provider.check_and_refresh(self.conn)
        self.assertFalse(renewed)
        self.assertIsNotNone(data)
        self.assertEqual(data["testStatus"], "unreachable")
        self.assertEqual(data["lastError"], "Connection refused")
        self.assertIn("did not answer", messages[0])


if __name__ == "__main__":
    unittest.main()
