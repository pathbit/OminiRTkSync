"""Testes unitários de manipulação do banco SQLite do OmniRoute."""

import json
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
        self.assertEqual(
            ag["expiresAt"],
            datetime.fromtimestamp(1789999999, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        )

    def test_update_connection_writes_iso_expiry(self):
        """expires_at deve sair em ISO-8601 UTC, o formato que o OmniRoute lê com new Date()."""
        expires_at_ms = 1789999999000
        update_connection(
            self.db_path,
            "conn-ag-1",
            access_token="tok",
            refresh_token="ref",
            expires_at_ms=expires_at_ms,
        )

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT expires_at, test_status FROM provider_connections WHERE id = 'conn-ag-1'"
        ).fetchone()
        conn.close()
        stored_expiry, stored_status = row

        # Precisa ser texto ISO parseável, e não um epoch numérico em texto.
        self.assertFalse(
            stored_expiry.isdigit(),
            "epoch numerico em texto vira Invalid Date no OmniRoute e desliga a renovacao preventiva",
        )
        parsed = datetime.fromisoformat(stored_expiry.replace("Z", "+00:00"))
        self.assertEqual(parsed.tzinfo, timezone.utc)

        # E o instante tem que sobreviver ao round-trip sem perda.
        self.assertEqual(int(parsed.timestamp() * 1000), expires_at_ms)

        # 'active' e o unico test_status que o OmniRoute trata como saudavel.
        self.assertEqual(stored_status, "active")

    def test_update_connection_json_schema_variant(self):
        """No schema JSON (9Router), expiresAt continua numerico e testStatus vira 'active'."""
        json_db = os.path.join(self.temp_dir.name, "data.sqlite")
        conn = sqlite3.connect(json_db)
        conn.execute(
            "CREATE TABLE providerConnections (id TEXT PRIMARY KEY, data TEXT, updatedAt TEXT)"
        )
        conn.execute(
            "INSERT INTO providerConnections (id, data, updatedAt) VALUES (?, ?, ?)",
            ("conn-json-1", json.dumps({"provider": "antigravity", "accessToken": "old"}), "x"),
        )
        conn.commit()
        conn.close()

        ok = update_connection(
            json_db,
            "conn-json-1",
            access_token="new-tok",
            refresh_token="new-ref",
            expires_at_ms=1789999999000,
        )
        self.assertTrue(ok)

        conn = sqlite3.connect(json_db)
        raw = conn.execute(
            "SELECT data FROM providerConnections WHERE id = 'conn-json-1'"
        ).fetchone()[0]
        conn.close()
        stored = json.loads(raw)

        # O 9Router guarda expiresAt dentro de um blob JSON, entao o numero sobrevive.
        self.assertEqual(stored["expiresAt"], 1789999999000)
        self.assertEqual(stored["testStatus"], "active")
        self.assertEqual(stored["accessToken"], "new-tok")


if __name__ == "__main__":
    unittest.main()
