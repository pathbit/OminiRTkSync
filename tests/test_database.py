"""Unit tests for OmniRoute SQLite database operations."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from omini_rtksync.database import (
    detect_connection_table,
    get_all_combos,
    get_all_connections,
    get_db_connection,
    update_connection,
)


class TestOminiDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "storage.sqlite")
        self._init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _init_db(self):
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
            (id, provider, name, access_token, refresh_token, expires_at, test_status, created_at, updated_at)
            VALUES
            ('conn-ag-1', 'antigravity', 'Google Antigravity Pro', 'old-tok-123', 'ref-tok-456', '1789000000000', 'ok', '2026-09-12T00:00:00Z', '2026-09-12T00:00:00Z'),
            ('conn-groq-1', 'groq', 'Groq Fast', NULL, NULL, NULL, 'ok', '2026-09-12T00:00:00Z', '2026-09-12T00:00:00Z')
        """)
        conn.commit()
        conn.close()

    def test_detect_connection_table(self):
        conn = get_db_connection(self.db_path)
        tbl = detect_connection_table(conn)
        conn.close()
        self.assertEqual(tbl, "provider_connections")

    def test_get_all_connections(self):
        conns = get_all_connections(self.db_path)
        self.assertEqual(len(conns), 2)
        ag = next(c for c in conns if c["id"] == "conn-ag-1")
        self.assertEqual(ag["provider"], "antigravity")
        self.assertTrue(ag["isOAuth"])
        self.assertEqual(ag["accessToken"], "old-tok-123")

    def test_update_connection(self):
        ok = update_connection(
            self.db_path,
            "conn-ag-1",
            access_token="new-tok-789",
            refresh_token="ref-tok-456",
            expires_at_ms=1789999999000,
        )
        self.assertTrue(ok)
        conns = get_all_connections(self.db_path)
        ag = next(c for c in conns if c["id"] == "conn-ag-1")
        self.assertEqual(ag["accessToken"], "new-tok-789")
        self.assertEqual(ag["expiresAt"], "1789999999000")


if __name__ == "__main__":
    unittest.main()
