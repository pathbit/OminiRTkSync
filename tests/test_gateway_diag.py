"""Diagnostic tests for OmniRoute gateway and test endpoint."""

import base64
import json
import os
import sqlite3
import tempfile
import urllib.request
import unittest
from unittest.mock import patch, MagicMock

from omini_rtksync.config import Settings
from omini_rtksync.web import start_omini_web


class TestOminiGatewayDiag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "storage.sqlite")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute("CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT, access_token TEXT, refresh_token TEXT, expires_at INTEGER, test_status TEXT, created_at TEXT, updated_at TEXT)")
            conn.execute("CREATE TABLE model_combos (id TEXT PRIMARY KEY, name TEXT, models TEXT, created_at TEXT, updated_at TEXT)")

        cls.settings = Settings(
            db_path=cls.db_path,
            web_host="127.0.0.1",
            web_port=19198,
            dashboard_user="admin",
            dashboard_password="testpassword",
            omniroute_url="http://mock-omniroute:20128",
        )
        cls.server = start_omini_web(
            host=cls.settings.web_host,
            port=cls.settings.web_port,
            db_path=cls.settings.db_path,
            omniroute_url=cls.settings.omniroute_url,
            settings=cls.settings,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    def test_test_gateway_endpoint(self):
        orig_req = urllib.request.urlopen

        def mock_urlopen(req, *args, **kwargs):
            if isinstance(req, urllib.request.Request) and "mock-omniroute" in req.full_url:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.__enter__.return_value = mock_resp
                return mock_resp
            return orig_req(req, *args, **kwargs)

        with patch("omini_rtksync.web.urllib.request.urlopen", side_effect=mock_urlopen):
            url = f"http://127.0.0.1:{self.settings.web_port}/api/test-gateway"
            auth = base64.b64encode(b"admin:testpassword").decode("utf-8")
            req = urllib.request.Request(url, data=b"{}", headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["success"])
                self.assertEqual(data["gatewayStatus"], "online")
                self.assertEqual(data["dbStatus"], "ok")
                self.assertIn("latencyMs", data)


if __name__ == "__main__":
    unittest.main()
