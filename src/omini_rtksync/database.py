"""Acesso e mutação segura do banco SQLite do OmniRoute (storage.sqlite)."""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def get_db_connection(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Banco SQLite do OmniRoute não encontrado em: {db_path}")
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def detect_connection_table(conn: sqlite3.Connection) -> str:
    """Detecta se o OmniRoute utiliza a tabela provider_connections ou providerConnections."""
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('provider_connections', 'providerConnections')")
    row = c.fetchone()
    if row:
        return row[0]
    return "provider_connections"


def get_all_connections(db_path: str) -> List[Dict[str, Any]]:
    """Carrega todas as conexões cadastradas no OmniRoute."""
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

            # Normalização de nomes de colunas relacionais
            provider = item.get("provider", "")
            name = item.get("name") or item.get("display_name") or provider
            access_token = item.get("access_token") or item.get("accessToken")
            refresh_token = item.get("refresh_token") or item.get("refreshToken")
            api_key = item.get("api_key") or item.get("apiKey")
            expires_at = item.get("expires_at") or item.get("expiresAt")
            test_status = item.get("test_status") or item.get("testStatus") or "active"

            # Se houver campo JSON 'data' (formato 9Router), funde os campos
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


def to_iso_utc(epoch_ms: int) -> str:
    """Converte epoch em milissegundos para o ISO-8601 em UTC que o OmniRoute grava nativamente."""
    return (
        datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def update_connection(
    db_path: str, connection_id: str, access_token: str, refresh_token: str, expires_at_ms: int
) -> bool:
    """Atualiza as credenciais normalizadas na tabela detectada."""
    conn = get_db_connection(db_path)
    tbl = detect_connection_table(conn)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({tbl})")
        cols = [c["name"] for c in cursor.fetchall()]

        if "access_token" in cols:
            # Tabela relacional do OmniRoute (provider_connections).
            #
            # expires_at é uma coluna TEXT e o OmniRoute a lê com `new Date(...)`
            # (src/lib/tokenHealthCheck.ts). Um epoch numérico gravado como texto
            # vira Invalid Date -> NaN -> o health check conclui que a conexão não
            # tem expiração conhecida e nunca renova o token preventivamente.
            # Por isso gravamos ISO-8601, o mesmo formato nativo do gateway.
            #
            # test_status precisa ser 'active': é o único valor que o OmniRoute
            # trata como saudável (src/sse/services/auth.ts::clearAccountError e
            # tokenHealthCheck.ts). 'ok' não é reconhecido e faz a conexão parecer
            # estar em estado de erro.
            cursor.execute(
                f"""
                UPDATE {tbl}
                SET access_token = ?, refresh_token = ?, expires_at = ?, test_status = 'active', updated_at = ?
                WHERE id = ?
                """,
                (access_token, refresh_token, to_iso_utc(expires_at_ms), now_iso, connection_id),
            )
        elif "data" in cols:
            # Formato compatível com JSON
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
            # 'active' é o valor que dispara o reset de estado de saúde no
            # 9Router (resetHealthStateOnActivation em connectionsRepo.js);
            # 'ok' só é reconhecido pela UI e não limpa travas de erro.
            d["testStatus"] = "active"
            cursor.execute(
                f"UPDATE {tbl} SET data = ?, updatedAt = ? WHERE id = ?",
                (json.dumps(d), now_iso, connection_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_all_combos(db_path: str) -> List[Dict[str, Any]]:
    """Carrega combos cadastrados no OmniRoute se a tabela existir."""
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
