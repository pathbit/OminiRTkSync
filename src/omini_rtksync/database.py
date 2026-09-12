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


def _decode_json(value: Any) -> Optional[Dict[str, Any]]:
    """Le uma coluna JSON tolerando texto vazio, NULL e dicionario ja decodificado."""
    if isinstance(value, dict):
        return value
    if not value:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _anexar_saida_de_rede(conn: sqlite3.Connection, conexoes: List[Dict[str, Any]]) -> None:
    """Resolve, por conexao, qual saida de rede o OmniRoute usaria.

    O vinculo vive em ``proxy_assignments`` com ``scope='account'`` e
    ``scope_id`` igual ao id da conexao; o proxy em si esta em
    ``proxy_registry``. Tudo em uma consulta so, e em silencio quando a
    instalacao e antiga demais para ter essas tabelas.

    Somente leitura: nada aqui escreve no banco.
    """
    try:
        existentes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('proxy_assignments','proxy_registry')"
            )
        }
        if {"proxy_assignments", "proxy_registry"} - existentes:
            return

        vinculos: Dict[str, str] = {}
        for linha in conn.execute(
            "SELECT a.scope_id, COALESCE(NULLIF(r.name,''), r.host || ':' || r.port) AS saida "
            "FROM proxy_assignments a JOIN proxy_registry r ON r.id = a.proxy_id "
            "WHERE a.scope = 'account' AND a.scope_id IS NOT NULL "
            "ORDER BY a.position ASC"
        ):
            # position ASC e o primeiro vence: e a saida que a rotacao entrega
            # quando o escopo tem um unico proxy vinculado.
            vinculos.setdefault(str(linha[0]), str(linha[1]))

        for c in conexoes:
            saida = vinculos.get(c["id"])
            if saida:
                c["egressProxy"] = saida
    except sqlite3.Error:
        # Schema mais antigo ou banco em uso por outro processo: a coluna de
        # saida simplesmente nao aparece, sem derrubar a listagem inteira.
        return


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
            # Sem default: uma coluna vazia significa "ninguém testou", não
            # "está saudável". Inventar "active" na leitura fazia o valor voltar
            # ao banco no fim do ciclo, e o sincronizador passava a afirmar ao
            # gateway uma saúde que nunca mediu — o mesmo defeito de marcar
            # "invalid" o que não conseguiu ler, só que na direção oposta.
            test_status = item.get("test_status") or item.get("testStatus")

            # Se houver campo JSON 'data' (formato 9Router), funde os campos
            extra: Dict[str, Any] = {}
            if "data" in keys and isinstance(item["data"], str):
                try:
                    d = json.loads(item["data"])
                    access_token = access_token or d.get("accessToken")
                    refresh_token = refresh_token or d.get("refreshToken")
                    api_key = api_key or d.get("apiKey")
                    expires_at = expires_at or d.get("expiresAt")
                    test_status = test_status or d.get("testStatus")
                    if isinstance(d, dict):
                        extra = d
                except Exception:
                    pass

            # provider_specific_data e onde o OmniRoute guarda baseUrl e afins.
            specific = _decode_json(item.get("provider_specific_data")) or _decode_json(
                extra.get("providerSpecificData")
            )

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
                # Campos de saude, projetados um a um. A linha crua do banco NAO
                # e devolvida: ela carrega access_token, refresh_token e api_key,
                # e qualquer consumidor que a serializasse por engano publicaria
                # as tres coisas de uma vez.
                "providerSpecificData": specific,
                "baseUrl": (specific or {}).get("baseUrl") or (specific or {}).get("baseURL"),
                "discoveredModels": (specific or {}).get("discoveredModels")
                or extra.get("discoveredModels")
                or [],
                "credentialState": (specific or {}).get("credentialState")
                or extra.get("credentialState"),
                "lastTested": item.get("last_tested") or extra.get("lastTested"),
                # Renovar e verificar são eventos diferentes. Sem projetar este
                # campo, "última renovação" no painel caía para o horário da
                # última verificação e um token parado há dias parecia recém
                # renovado a cada ciclo.
                "lastRefreshAt": (specific or {}).get("lastRefreshAt") or extra.get("lastRefreshAt"),
                "lastHealthCheckAt": item.get("last_health_check_at"),
                "rateLimitedUntil": item.get("rate_limited_until") or extra.get("rateLimitedUntil"),
                "lastError": item.get("last_error"),
                "updatedAt": item.get("updated_at") or item.get("updatedAt"),
                # Saida de rede: interruptores por conexao do proprio OmniRoute.
                # O vinculo em si vem de proxy_assignments, resolvido abaixo.
                "proxyEnabled": bool(item.get("proxy_enabled")),
                "perKeyProxyEnabled": bool(item.get("per_key_proxy_enabled")),
                "egressProxy": None,
            })

        _anexar_saida_de_rede(conn, result)
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


def normalize_expiry_format(db_path: str, connection_id: str, expires_at_ms: int) -> bool:
    """Regrava expires_at em ISO-8601 sem tocar nos tokens.

    A cura de formato acontecia so junto de uma renovacao bem-sucedida. Quando a
    renovacao falha -- refresh token revogado, client_id ausente -- o epoch
    numerico gravado como texto permanecia, e e justamente ele que o OmniRoute
    le com `new Date(...)` e obtem Invalid Date, desligando a propria renovacao
    preventiva. O formato e curado de qualquer jeito.
    """
    conn = get_db_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        tbl = detect_connection_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {tbl} SET expires_at = ?, updated_at = ? WHERE id = ?",
            (to_iso_utc(expires_at_ms), now_iso, connection_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()

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
            # O horário da renovação vive no JSON de provider_specific_data:
            # o schema relacional não tem coluna para ele, e sem esse carimbo o
            # painel não distingue "renovado agora" de "apenas verificado".
            especifico = {}
            if "provider_specific_data" in cols:
                cursor.execute(
                    f"SELECT provider_specific_data FROM {tbl} WHERE id = ?", (connection_id,)
                )
                linha = cursor.fetchone()
                if linha:
                    especifico = _decode_json(linha["provider_specific_data"]) or {}
                especifico["lastRefreshAt"] = now_iso
                cursor.execute(
                    f"""
                    UPDATE {tbl}
                    SET access_token = ?, refresh_token = ?, expires_at = ?, test_status = 'active',
                        provider_specific_data = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        access_token,
                        refresh_token,
                        to_iso_utc(expires_at_ms),
                        json.dumps(especifico),
                        now_iso,
                        connection_id,
                    ),
                )
            else:
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
            d["lastRefreshAt"] = now_iso
            cursor.execute(
                f"UPDATE {tbl} SET data = ?, updatedAt = ? WHERE id = ?",
                (json.dumps(d), now_iso, connection_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_connection_health(
    db_path: str,
    connection_id: str,
    *,
    test_status: Optional[str] = None,
    credential_state: Optional[str] = None,
    discovered_models: Optional[List[str]] = None,
    last_error: Optional[str] = None,
    clear_rate_limit: bool = False,
) -> bool:
    """Grava o resultado de uma sondagem, sem tocar em token nenhum.

    Antes disto o provider devolvia `testStatus`, `credentialState` e o catalogo
    descoberto, e o motor jogava tudo fora: o painel recarregava a linha antiga e
    nunca mostrava que uma chave havia sido recusada nem que a instancia local
    estava fora do ar.

    Só escreve em coluna que existe no banco — o schema do OmniRoute evolui entre
    versões, e uma instalação mais antiga não pode quebrar por causa disso.
    """
    conn = get_db_connection(db_path)
    try:
        tbl = detect_connection_table(conn)
        cursor = conn.cursor()
        cols = {c[1] for c in cursor.execute(f"PRAGMA table_info({tbl})")}
        now_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        campos: List[str] = []
        valores: List[Any] = []

        if test_status and "test_status" in cols:
            campos.append("test_status = ?")
            valores.append(test_status)
        if "last_tested" in cols:
            campos.append("last_tested = ?")
            valores.append(now_iso)
        if "last_health_check_at" in cols:
            campos.append("last_health_check_at = ?")
            valores.append(now_iso)
        if last_error is not None and "last_error" in cols:
            campos.append("last_error = ?")
            valores.append(last_error or None)
        if clear_rate_limit and "rate_limited_until" in cols:
            campos.append("rate_limited_until = ?")
            valores.append(None)

        # credentialState e o catalogo local nao tem coluna propria: vao para o
        # JSON de provider_specific_data, preservando o que ja estava la.
        if (credential_state or discovered_models is not None) and "provider_specific_data" in cols:
            cursor.execute(f"SELECT provider_specific_data FROM {tbl} WHERE id = ?", (connection_id,))
            linha = cursor.fetchone()
            atual = _decode_json(linha["provider_specific_data"]) if linha else None
            atual = dict(atual or {})
            if credential_state:
                atual["credentialState"] = credential_state
                atual["credentialCheckedAt"] = now_iso
            if discovered_models is not None:
                atual["discoveredModels"] = discovered_models
            campos.append("provider_specific_data = ?")
            valores.append(json.dumps(atual))

        # Schema de coluna JSON única (o formato que o 9Router usa e que o
        # OmniRoute aceita em instalações migradas). Sem este ramo a sondagem
        # era descartada inteira nessas bases: não há `test_status` nem
        # `provider_specific_data` para receber os campos acima, e a função
        # saía por `if not campos` como se não houvesse nada a gravar.
        if "data" in cols and "test_status" not in cols:
            cursor.execute(f"SELECT data FROM {tbl} WHERE id = ?", (connection_id,))
            linha = cursor.fetchone()
            d: Dict[str, Any] = {}
            if linha and linha["data"]:
                try:
                    carregado = json.loads(linha["data"])
                    if isinstance(carregado, dict):
                        d = carregado
                except Exception:
                    # Coluna corrompida ou em formato inesperado: seguimos com o
                    # dicionário vazio e regravamos a linha com os campos de
                    # saúde. Abortar aqui faria uma linha ilegível bloquear para
                    # sempre a gravação da sondagem — justamente na conexão que
                    # mais precisa ser diagnosticada.
                    pass
            if test_status:
                d["testStatus"] = test_status
            if credential_state:
                d["credentialState"] = credential_state
                d["credentialCheckedAt"] = now_iso
            if discovered_models is not None:
                d["discoveredModels"] = discovered_models
            if last_error is not None:
                d["lastError"] = last_error or None
            if clear_rate_limit:
                d.pop("rateLimitedUntil", None)
            d["lastTested"] = now_iso
            campos.append("data = ?")
            valores.append(json.dumps(d))

        if "updated_at" in cols:
            campos.append("updated_at = ?")
            valores.append(now_iso)
        elif "updatedAt" in cols:
            campos.append("updatedAt = ?")
            valores.append(now_iso)

        if not campos:
            return False

        valores.append(connection_id)
        cursor.execute(f"UPDATE {tbl} SET {', '.join(campos)} WHERE id = ?", valores)
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
