"""Safe access and relational mutation for OmniRoute SQLite database (storage.sqlite)."""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def get_db_connection(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"OmniRoute SQLite database not found at: {db_path}")
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def detect_connection_table(conn: sqlite3.Connection) -> str:
    """Detect whether OmniRoute uses provider_connections or providerConnections table."""
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('provider_connections', 'providerConnections')")
    row = c.fetchone()
    if row:
        return row[0]
    return "provider_connections"


def get_all_connections(db_path: str) -> List[Dict[str, Any]]:
    """Load all connections registered in OmniRoute."""
    conn = get_db_connection(db_path)
    try:
        tbl = detect_connection_table(conn)
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {tbl}")
        rows = cursor.fetchall()
        result = []
        for r in rows:
            keys = r.keys()
            item = dict(r)

            # Normalization of relational column names
            provider = item.get("provider", "")
            name = item.get("name") or item.get("display_name") or provider
            access_token = item.get("access_token") or item.get("accessToken")
            refresh_token = item.get("refresh_token") or item.get("refreshToken")
            api_key = item.get("api_key") or item.get("apiKey")
            expires_at = item.get("expires_at") or item.get("expiresAt")
            test_status = item.get("test_status") or item.get("testStatus") or "ok"

            # If JSON 'data' field is present (9Router style schema), merge fields
            if "data" in keys and isinstance(item["data"], str):
                try:
                    d = json.loads(item["data"])
                    access_token = access_token or d.get("accessToken")
                    refresh_token = refresh_token or d.get("refreshToken")
                    api_key = api_key or d.get("apiKey")
                    expires_at = expires_at or d.get("expiresAt")
                    test_status = test_status or d.get("testStatus")
                except Exception:
                    pass

            result.append({
                "id": str(item["id"]),
                "provider": provider,
                "name": name,
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "apiKey": api_key,
                "expiresAt": expires_at,
                "testStatus": test_status,
                "isOAuth": bool(access_token or refresh_token),
                "hasApiKey": bool(api_key),
                "raw": item,
            })
        return result
    finally:
        conn.close()


def update_connection(
    db_path: str, connection_id: str, access_token: str, refresh_token: str, expires_at_ms: int
) -> bool:
    """Update normalized credentials in the detected connection table."""
    conn = get_db_connection(db_path)
    tbl = detect_connection_table(conn)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({tbl})")
        cols = [c["name"] for c in cursor.fetchall()]

        if "access_token" in cols:
            # OmniRoute relational schema (provider_connections)
            cursor.execute(
                f"""
                UPDATE {tbl}
                SET access_token = ?, refresh_token = ?, expires_at = ?, test_status = 'ok', updated_at = ?
                WHERE id = ?
                """,
                (access_token, refresh_token, str(expires_at_ms), now_iso, connection_id),
            )
        elif "data" in cols:
            # Compatible JSON format
            cursor.execute(f"SELECT data FROM {tbl} WHERE id = ?", (connection_id,))
            row = cursor.fetchone()
            d = {}
            if row and row["data"]:
                try:
                    d = json.loads(row["data"])
                except Exception:
                    pass
            d["accessToken"] = access_token
            if refresh_token:
                d["refreshToken"] = refresh_token
            d["expiresAt"] = expires_at_ms
            d["testStatus"] = "ok"
            cursor.execute(
                f"UPDATE {tbl} SET data = ?, updatedAt = ? WHERE id = ?",
                (json.dumps(d), now_iso, connection_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_all_combos(db_path: str) -> List[Dict[str, Any]]:
    """Load combos registered in OmniRoute if table exists."""
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='combos'")
        if not cursor.fetchone():
            return []
        cursor.execute("SELECT * FROM combos")
        rows = cursor.fetchall()
        result = []
        for r in rows:
            item = dict(r)
            models_raw = item.get("models", "[]")
            try:
                models = json.loads(models_raw) if isinstance(models_raw, str) else models_raw
            except Exception:
                models = []
            result.append({
                "id": str(item.get("id")),
                "name": item.get("name"),
                "kind": item.get("kind", "llm"),
                "models": models,
            })
        return result
    finally:
        conn.close()

