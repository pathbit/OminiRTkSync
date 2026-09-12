"""Testes unitários para motor de descoberta e múltiplos provedores do OminiRTKSync."""

import json
import os
import shutil
import tempfile
import unittest

from omini_rtksync.discovery import HostDiscoveryEngine
from omini_rtksync.providers import ApiKeyProvider, GenericOAuthProvider, GoogleProvider, LocalProvider


class TestOminiDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.engine = HostDiscoveryEngine(host_home=self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_discover_google(self):
        gemini_dir = os.path.join(self.tmp_dir, ".gemini")
        os.makedirs(gemini_dir, exist_ok=True)
        with open(os.path.join(gemini_dir, "oauth_creds.json"), "w") as f:
            json.dump({
                "access_token": "ya29.omini_google",
                "refresh_token": "1//omini_ref",
                "client_id": "omini_cid",
                "client_secret": "omini_sec",
                "expiry_date": 1800000000000,
            }, f)

        res = self.engine.discover_google()
        self.assertIsNotNone(res)
        self.assertEqual(res["accessToken"], "ya29.omini_google")

    def test_discover_claude_settings(self):
        claude_dir = os.path.join(self.tmp_dir, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        with open(os.path.join(claude_dir, "settings.json"), "w") as f:
            json.dump({
                "env": {
                    "ANTHROPIC_API_KEY": "sk-ant-test-omini",
                }
            }, f)

        res = self.engine.discover_claude()
        self.assertIsNotNone(res)
        self.assertEqual(res["apiKey"], "sk-ant-test-omini")

    def test_multi_providers(self):
        # 1. Google Provider
        gemini_dir = os.path.join(self.tmp_dir, ".gemini")
        os.makedirs(gemini_dir, exist_ok=True)
        with open(os.path.join(gemini_dir, "oauth_creds.json"), "w") as f:
            json.dump({"access_token": "ya29.synced", "refresh_token": "ref_sync"}, f)

        gp = GoogleProvider(discovery=self.engine)
        creds = gp.read_local_credential()
        self.assertIsNotNone(creds)
        self.assertEqual(creds["access_token"], "ya29.synced")

        # 2. Generic OAuth
        op = GenericOAuthProvider(discovery=self.engine)
        conn_claude = {
            "provider": "claude",
            "isOAuth": True,
            "accessToken": "old",
            "refreshToken": "ref",
            "expiresAt": 1000,
        }
        self.assertTrue(op.can_handle(conn_claude))

        # 3. ApiKey Provider
        claude_dir = os.path.join(self.tmp_dir, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        with open(os.path.join(claude_dir, "settings.json"), "w") as f:
            json.dump({"env": {"ANTHROPIC_API_KEY": "sk-ant-synced"}}, f)

        ap = ApiKeyProvider(discovery=self.engine)
        conn_api = {"provider": "claude", "hasApiKey": True, "apiKey": "old_api"}
        self.assertTrue(ap.can_handle(conn_api))
        mod, data, notes = ap.check_and_refresh(conn_api)
        self.assertTrue(mod)
        self.assertEqual(data["apiKey"], "sk-ant-synced")

        # 4. Local Provider
        lp = LocalProvider()
        conn_local = {"provider": "openai-compatible-chat-ollama-local"}
        self.assertTrue(lp.can_handle(conn_local))


if __name__ == "__main__":
    unittest.main()
