"""Testes unitários da CLI do OminiRTKSync."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from omini_rtksync.cli import OmniSyncEngine, main, print_status
from omini_rtksync.config import Settings


class TestOminiCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "storage.sqlite")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE provider_connections (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                name TEXT,
                access_token TEXT,
                refresh_token TEXT,
                api_key TEXT,
                expires_at TEXT,
                test_status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        c.execute("""
            INSERT INTO provider_connections
            (id, provider, name, access_token, refresh_token, api_key, expires_at, test_status, created_at, updated_at)
            VALUES
            ('test-1', 'groq', 'Groq Account', NULL, NULL, 'test-key', '1789000000000', 'ok', '2026-09-12T00:00:00Z', '2026-09-12T00:00:00Z')
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_engine_once(self):
        settings = Settings(db_path=self.db_path, sync_interval=300, enable_web=False)
        engine = OmniSyncEngine(settings)
        res = engine.sync_all()
        self.assertTrue(res["success"])
        self.assertEqual(res["total"], 1)

    def test_print_status(self):
        settings = Settings(db_path=self.db_path, enable_web=False)
        # Deve executar sem levantar exceção
        print_status(settings)

    def test_cli_main_status(self):
        with patch("sys.argv", ["ominirtksync", "--db-path", self.db_path, "--status"]):
            main()

    def test_cli_main_once(self):
        with patch("sys.argv", ["OminiRTKSync", "--db-path", self.db_path, "--once"]):
            main()


if __name__ == "__main__":
    unittest.main()
