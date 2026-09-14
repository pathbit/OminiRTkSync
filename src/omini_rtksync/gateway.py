"""Domínio: tudo o que este sincronizador sabe sobre o gateway a que se liga.

Este é o ÚNICO módulo do pacote onde a divergência entre os três irmãos é
legítima e fica. Painel, render, sessão, SSO e i18n são o mesmo texto nos três;
o que muda é o que existe atrás deste arquivo — aqui um SQLite no disco do
gateway, num irmão um proxy com API HTTP e Postgres.

Ele reúne o que antes morava em três módulos separados (`database.py`,
`discovery.py` e `providers.py`). Separados, eles eram três nomes de arquivo que
os irmãos não tinham, e enquanto os nomes fossem diferentes nenhum teste
conseguia afirmar que o resto era igual.

Por que banco e não API: este gateway guarda conexões, chaves e catálogo num
SQLite próprio, e não publica API administrativa para escrevê-los. O painel lê
o arquivo diretamente e só escreve em coluna que o schema instalado tem.
"""

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .credential_check import (
    DEFAULT_TIMEOUT_SECONDS,
    STATE_INVALID,
    STATE_RATE_LIMITED,
    STATE_UNREACHABLE,
    STATE_VALID,
    check_connection,
)
from .identidade import NOME_DO_PRODUTO
from .models import ConnectionRecord

# ---------------------------------------------------------------------------
# Banco do gateway: leitura e mutação segura do SQLite
# ---------------------------------------------------------------------------

def get_db_connection(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Banco SQLite do gateway não encontrado em: {db_path}")
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def detect_connection_table(conn: sqlite3.Connection) -> str:
    """Detecta se o gateway utiliza a tabela provider_connections ou providerConnections."""
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
    """Resolve, por conexao, qual saida de rede o gateway usaria.

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
    """Carrega todas as conexões cadastradas no gateway."""
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

            # Se houver campo JSON 'data' (o formato do gateway irmao), funde os campos
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

            # provider_specific_data e onde o gateway guarda baseUrl e afins.
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
                # Saida de rede: interruptores por conexao do proprio gateway.
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
    """Converte epoch em milissegundos para o ISO-8601 em UTC que o gateway grava nativamente."""
    return (
        datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def normalize_expiry_format(db_path: str, connection_id: str, expires_at_ms: int) -> bool:
    """Regrava expires_at em ISO-8601 sem tocar nos tokens.

    A cura de formato acontecia so junto de uma renovacao bem-sucedida. Quando a
    renovacao falha -- refresh token revogado, client_id ausente -- o epoch
    numerico gravado como texto permanecia, e e justamente ele que o gateway
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
            # Tabela relacional do gateway (provider_connections).
            #
            # expires_at é uma coluna TEXT e o gateway a lê com `new Date(...)`
            # (src/lib/tokenHealthCheck.ts). Um epoch numérico gravado como texto
            # vira Invalid Date -> NaN -> o health check conclui que a conexão não
            # tem expiração conhecida e nunca renova o token preventivamente.
            # Por isso gravamos ISO-8601, o mesmo formato nativo do gateway.
            #
            # test_status precisa ser 'active': é o único valor que o gateway
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
            # o gateway irmao (resetHealthStateOnActivation em connectionsRepo.js);
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

    Só escreve em coluna que existe no banco — o schema do gateway evolui entre
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

        # Schema de coluna JSON única (o formato que o gateway irmao usa e que
        # este aceita em instalações migradas). Sem este ramo a sondagem
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


# Colunas de ``api_keys`` que o painel tem permissao de ler.
#
# A lista existe para ser uma lista: ``SELECT *`` nesta tabela traria ``key``
# (o segredo em claro), ``key_hash`` e ``key_prefix``. O prefixo tambem e
# segredo -- e um pedaco do proprio token -- e por isso NAO esta aqui. Quem
# identifica a chave na tela e o ``name``; sem nome, o ``id`` (um UUID, que nao
# abre porta nenhuma).
COLUNAS_SEGURAS_DE_CHAVE = (
    "id",
    "name",
    "created_at",
    "expires_at",
    "revoked_at",
    "last_used_at",
    "is_active",
    "is_banned",
    "model_access_mode",
    "allowed_models",
    "allowed_combos",
    "scopes",
    "max_requests_per_day",
    "max_requests_per_minute",
)

# Namespace do ``key_value`` onde o gateway guarda o catalogo sincronizado.
NAMESPACE_MODELOS = "syncedAvailableModels"


def _tabela_existe(conn: sqlite3.Connection, nome: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (nome,)
    )
    return cursor.fetchone() is not None


def _lista_json(valor: Any) -> List[Any]:
    """Le uma coluna JSON que deveria ser um array, tolerando lixo e NULL."""
    if isinstance(valor, list):
        return valor
    if not valor:
        return []
    try:
        decodificado = json.loads(valor)
    except (TypeError, ValueError):
        return []
    return decodificado if isinstance(decodificado, list) else []


def get_all_api_keys(db_path: str) -> List[Dict[str, Any]]:
    """Chaves virtuais emitidas pelo gateway (tabela ``api_keys``).

    Todo gateway da familia emite chave virtual -- e o token que o cliente
    apresenta no lugar da credencial do provedor -- e o painel precisa mostrar
    quais existem, ate quando valem e se ainda estao aceitas.

    Somente leitura, e somente das colunas de COLUNAS_SEGURAS_DE_CHAVE: o
    material do token nunca sai do banco.

    Instalacao antiga sem a tabela devolve lista vazia, do mesmo jeito que
    ``get_all_combos`` faz com ``combos``: o cartao aparece com o estado vazio
    em vez de derrubar a pagina inteira.
    """
    conn = get_db_connection(db_path)
    try:
        if not _tabela_existe(conn, "api_keys"):
            return []

        # So pede o que a instalacao realmente tem: o schema do gateway cresce
        # entre versoes, e uma coluna ausente faria a consulta inteira falhar.
        presentes = {linha[1] for linha in conn.execute("PRAGMA table_info(api_keys)")}
        colunas = [c for c in COLUNAS_SEGURAS_DE_CHAVE if c in presentes]
        if "id" not in colunas:
            return []

        resultado: List[Dict[str, Any]] = []
        for linha in conn.execute(f"SELECT {', '.join(colunas)} FROM api_keys"):
            item = dict(linha)
            resultado.append({
                "id": str(item.get("id") or ""),
                "name": item.get("name") or "",
                "createdAt": item.get("created_at"),
                "expiresAt": item.get("expires_at"),
                "revokedAt": item.get("revoked_at"),
                "lastUsedAt": item.get("last_used_at"),
                # Colunas ausentes viram o padrao do proprio gateway: chave
                # ativa e nao banida. Assumir o contrario pintaria de vermelho
                # toda chave de uma instalacao antiga.
                "isActive": bool(item["is_active"]) if item.get("is_active") is not None else True,
                "isBanned": bool(item.get("is_banned")),
                "modelAccessMode": item.get("model_access_mode") or "all",
                "allowedModels": _lista_json(item.get("allowed_models")),
                "allowedCombos": _lista_json(item.get("allowed_combos")),
                "scopes": _lista_json(item.get("scopes")),
                "maxRequestsPerDay": item.get("max_requests_per_day"),
                "maxRequestsPerMinute": item.get("max_requests_per_minute"),
            })
        resultado.sort(key=lambda k: (k["name"] or k["id"]).lower())
        return resultado
    finally:
        conn.close()


def get_all_registered_models(db_path: str) -> List[Dict[str, Any]]:
    """Modelos que o gateway conhece, um por linha, com a conexao que os serve.

    Este gateway NAO tem tabela de modelos. O catalogo que ele publica em
    ``/v1/models`` e montado em tempo de requisicao a partir de um registro
    estatico somado ao que cada conexao sincronizou -- e essa segunda metade, a
    unica que descreve esta instalacao, mora em ``key_value``, no namespace
    ``syncedAvailableModels``, com a chave no formato ``<provedor>:<id da
    conexao>`` e um array JSON por valor.

    E dai que se le, e nao do ``/v1/models``: a rota HTTP exige uma chave de API
    do proprio gateway, que o sincronizador nao tem e nao deveria passar a ter
    so para desenhar uma tabela. O banco ja esta aberto aqui.
    """
    conn = get_db_connection(db_path)
    try:
        if not _tabela_existe(conn, "key_value"):
            return []

        resultado: List[Dict[str, Any]] = []
        for chave, valor in conn.execute(
            "SELECT key, value FROM key_value WHERE namespace = ?", (NAMESPACE_MODELOS,)
        ):
            # ``<provedor>:<id da conexao>`` -- o id e um UUID com hifens, nunca
            # com dois-pontos, entao o primeiro separador e o unico.
            provider, _, connection_id = str(chave).partition(":")
            for entrada in _lista_json(valor):
                if not isinstance(entrada, dict):
                    continue
                identificador = entrada.get("id")
                if not identificador:
                    continue
                resultado.append({
                    "id": str(identificador),
                    "name": entrada.get("name") or str(identificador),
                    "provider": provider,
                    "connectionId": connection_id,
                    "source": entrada.get("source") or "",
                    "description": entrada.get("description") or "",
                    "inputTokenLimit": entrada.get("inputTokenLimit"),
                    "outputTokenLimit": entrada.get("outputTokenLimit"),
                    "supportedEndpoints": [
                        str(e) for e in _lista_json(entrada.get("supportedEndpoints"))
                    ],
                })

        resultado.sort(key=lambda m: (m["provider"].lower(), m["id"].lower()))
        return resultado
    except sqlite3.Error:
        # ``key_value`` existe mas esta em uso ou em formato inesperado: o cartao
        # cai para o estado vazio em vez de levar o painel junto.
        return []
    finally:
        conn.close()


def get_all_combos(db_path: str) -> List[Dict[str, Any]]:
    """Carrega combos cadastrados no gateway se a tabela existir."""
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


# ---------------------------------------------------------------------------
# Descoberta de credenciais locais no host
# ---------------------------------------------------------------------------

class HostDiscoveryEngine:
    """
    Localiza e extrai credenciais de ferramentas e CLIs instaladas no host.
    Funciona tanto executando nativamente no host quanto dentro do container
    com o diretório montado em HOST_HOME (ex: /root/host).
    """

    def __init__(self, host_home: Optional[str] = None, extra_paths: Optional[List[str]] = None):
        self.host_home = self._resolve_host_home(host_home)
        self.extra_paths = extra_paths or []

    @staticmethod
    def _resolve_host_home(override: Optional[str] = None) -> str:
        if override and os.path.exists(override):
            return override
        env_host = os.environ.get("HOST_HOME")
        if env_host and os.path.exists(env_host):
            return env_host
        if os.path.exists("/root/host") and os.path.isdir("/root/host"):
            return "/root/host"
        if os.path.exists("/host") and os.path.isdir("/host"):
            return "/host"
        return os.path.expanduser("~")

    def _read_json(self, path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(path) or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def discover_google(self) -> Optional[Dict[str, Any]]:
        """Descobre tokens do Google Antigravity / Gemini CLI."""
        # 1. jetski-standalone-oauth-token (token standalone do Antigravity)
        jetski_candidates = [
            os.path.join(self.host_home, ".gemini", "jetski-standalone-oauth-token"),
            os.path.join(self.host_home, ".config", "antigravity", "jetski-standalone-oauth-token"),
            "/root/.gemini/jetski-standalone-oauth-token",
        ] + self.extra_paths

        for p in jetski_candidates:
            data = self._read_json(p)
            if data:
                tok_dict = data.get("token") if isinstance(data.get("token"), dict) else data
                acc = tok_dict.get("access_token") or tok_dict.get("accessToken")
                ref = tok_dict.get("refresh_token") or tok_dict.get("refreshToken")
                if acc or ref:
                    return {
                        "source_path": p,
                        "accessToken": acc,
                        "refreshToken": ref,
                        "clientId": tok_dict.get("client_id") or data.get("client_id"),
                        "clientSecret": tok_dict.get("client_secret") or data.get("client_secret"),
                        "expiry": tok_dict.get("expiry"),
                    }

        # 2. oauth_creds.json
        creds_candidates = [
            os.path.join(self.host_home, ".gemini", "oauth_creds.json"),
            os.path.join(self.host_home, ".config", "antigravity", "oauth_creds.json"),
            "/root/.gemini/oauth_creds.json",
        ]
        for p in creds_candidates:
            data = self._read_json(p)
            if data and (data.get("access_token") or data.get("refresh_token")):
                return {
                    "source_path": p,
                    "accessToken": data.get("access_token"),
                    "refreshToken": data.get("refresh_token"),
                    "clientId": data.get("client_id") or data.get("clientId"),
                    "clientSecret": data.get("client_secret") or data.get("clientSecret"),
                    "expiry": data.get("expiry_date"),
                }

        return None

    def discover_claude(self) -> Optional[Dict[str, Any]]:
        """Descobre configurações e contas do Claude Code CLI e Anthropic."""
        settings_path = os.path.join(self.host_home, ".claude", "settings.json")
        data = self._read_json(settings_path)
        if data and isinstance(data.get("env"), dict):
            env = data["env"]
            api_key = env.get("ANTHROPIC_API_KEY")
            if api_key:
                return {
                    "source_path": settings_path,
                    "apiKey": api_key,
                    "baseUrl": env.get("ANTHROPIC_BASE_URL"),
                }

        claude_json_path = os.path.join(self.host_home, ".claude.json")
        data = self._read_json(claude_json_path)
        if data:
            oauth_acc = data.get("oauthAccount") if isinstance(data.get("oauthAccount"), dict) else None
            return {
                "source_path": claude_json_path,
                "oauthAccount": oauth_acc,
                "has_oauth": bool(oauth_acc),
                "email": oauth_acc.get("emailAddress") if oauth_acc else None,
            }

        cred_paths = [
            os.path.join(self.host_home, ".claude", "credentials.json"),
            os.path.join(self.host_home, ".config", "claude", "credentials.json"),
        ]
        for p in cred_paths:
            data = self._read_json(p)
            if data and (data.get("apiKey") or data.get("token")):
                return {
                    "source_path": p,
                    "apiKey": data.get("apiKey") or data.get("token"),
                }

        return None

    def discover_github(self) -> Optional[Dict[str, Any]]:
        """Descobre credenciais do GitHub CLI e Copilot."""
        copilot_hosts = os.path.join(self.host_home, ".config", "github-copilot", "hosts.json")
        copilot_data = self._read_json(copilot_hosts)
        if copilot_data:
            for host, info in copilot_data.items():
                if isinstance(info, dict) and info.get("oauth_token"):
                    return {
                        "source_path": copilot_hosts,
                        "provider": "github",
                        "accessToken": info["oauth_token"],
                        "user": info.get("user"),
                    }

        gh_hosts = os.path.join(self.host_home, ".config", "gh", "hosts.yml")
        if os.path.exists(gh_hosts):
            try:
                with open(gh_hosts, "r", encoding="utf-8") as f:
                    content = f.read()
                m_token = re.search(r"oauth_token:\s*([^\s]+)", content)
                m_user = re.search(r"user:\s*([^\s]+)", content)
                if m_token:
                    return {
                        "source_path": gh_hosts,
                        "provider": "github",
                        "accessToken": m_token.group(1),
                        "user": m_user.group(1) if m_user else None,
                    }
            except Exception:
                pass

        return None

    def discover_codex_openai(self) -> Optional[Dict[str, Any]]:
        """Descobre credenciais OpenAI e Codex."""
        codex_auth = os.path.join(self.host_home, ".codex", "auth.json")
        data = self._read_json(codex_auth)
        if data:
            toks = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            api_key = data.get("OPENAI_API_KEY")
            acc_tok = toks.get("access_token")
            ref_tok = toks.get("refresh_token")
            if api_key or acc_tok:
                return {
                    "source_path": codex_auth,
                    "apiKey": api_key,
                    "accessToken": acc_tok,
                    "refreshToken": ref_tok,
                    "auth_mode": data.get("auth_mode"),
                }

        candidates = [
            os.path.join(self.host_home, ".codex", "config.json"),
            os.path.join(self.host_home, ".openai", "credentials"),
            os.path.join(self.host_home, ".config", "openai", "credentials"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "apiKey": data.get("api_key") or data.get("apiKey") or data.get("token"),
                    "accessToken": data.get("access_token"),
                    "refreshToken": data.get("refresh_token"),
                }
        return None

    def discover_kiro(self) -> Optional[Dict[str, Any]]:
        """Descobre credenciais AWS Kiro."""
        candidates = [
            os.path.join(self.host_home, ".kiro", "credentials"),
            os.path.join(self.host_home, ".kiro", "settings", "auth.json"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "accessToken": data.get("accessToken") or data.get("token"),
                    "refreshToken": data.get("refreshToken"),
                }
        return None

    def discover_codeium(self) -> Optional[Dict[str, Any]]:
        """Descobre configurações e chaves Codeium / Windsurf."""
        candidates = [
            os.path.join(self.host_home, ".codeium", "config.json"),
            os.path.join(self.host_home, ".windsurf", "auth.json"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "apiKey": data.get("apiKey") or data.get("token"),
                }
        return None

    def discover_all(self) -> Dict[str, Any]:
        """Varre todos os provedores suportados no host."""
        return {
            "google": self.discover_google(),
            "claude": self.discover_claude(),
            "github": self.discover_github(),
            "codex": self.discover_codex_openai(),
            "kiro": self.discover_kiro(),
            "codeium": self.discover_codeium(),
        }

    def get_credential_for_provider(self, provider: str) -> Optional[Dict[str, Any]]:
        """Busca credencial correspondente a um provedor do gateway."""
        p_lower = provider.lower()
        if p_lower in ("antigravity", "gemini-cli", "google"):
            return self.discover_google()
        if p_lower in ("claude", "anthropic"):
            return self.discover_claude()
        if p_lower in ("github", "copilot"):
            return self.discover_github()
        if p_lower in ("codex", "openai"):
            return self.discover_codex_openai()
        if p_lower in ("kiro", "aws-kiro"):
            return self.discover_kiro()
        if p_lower in ("codeium", "windsurf"):
            return self.discover_codeium()
        return None


# ---------------------------------------------------------------------------
# Provedores: renovação, sondagem e saneamento por tipo de conexão
# ---------------------------------------------------------------------------

class GoogleProvider:
    """Renovador OAuth para contas Google (Antigravity / Gemini CLI) no gateway."""

    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, credential_paths: Optional[List[str]] = None, discovery: Optional[Any] = None):
        self.credential_paths = credential_paths or []
        self.discovery = discovery

    def find_local_credential_file(self) -> Optional[str]:
        if self.discovery:
            disc = self.discovery.discover_google()
            if disc and disc.get("source_path"):
                return disc["source_path"]
        for p in self.credential_paths:
            if p and os.path.exists(p) and os.path.isfile(p):
                return p
        return None

    def read_local_credential(self) -> Optional[Dict[str, Any]]:
        if self.discovery:
            disc = self.discovery.discover_google()
            if disc and disc.get("accessToken"):
                return {
                    "access_token": disc.get("accessToken"),
                    "refresh_token": disc.get("refreshToken"),
                    "client_id": disc.get("clientId"),
                    "client_secret": disc.get("clientSecret"),
                    "expiry": disc.get("expiry"),
                }
        path = self.find_local_credential_file()
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            tok = data.get("access_token") or data.get("accessToken") or data.get("token")
            if tok and "access_token" not in data:
                data["access_token"] = tok
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def refresh(
        self, refresh_token: str, client_id: str, client_secret: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not client_id or not client_secret:
            return False, None, "client_id ou client_secret não configurado no ambiente nem encontrado em shared.js"
        payload = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.OAUTH_TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": f"{NOME_DO_PRODUTO}/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data, "OK"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
            return False, None, f"HTTP {e.code}: {err_body}"
        except Exception as e:
            return False, None, str(e)


class GenericOAuthProvider:
    """Monitor e sincronizador OAuth genérico do gateway (Claude, GitHub, Codex, Kiro)."""

    KNOWN_TOKEN_URLS = {
        "claude": "https://api.anthropic.com/v1/oauth/token",
        "github": "https://github.com/login/oauth/access_token",
        "kiro": "https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken",
        "codex": "https://auth.openai.com/oauth/token",
        "kimi": "https://api.moonshot.cn/v1/oauth/token",
    }

    def __init__(self, discovery: Optional[Any] = None):
        self.discovery = discovery

    def can_handle(self, conn: Dict[str, Any]) -> bool:
        provider = conn.get("provider", "").lower()
        return bool(conn.get("isOAuth")) and provider not in ("antigravity", "gemini-cli")

    def check_and_refresh(
        self, conn: Dict[str, Any], margin_seconds: int = 900
    ) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
        messages = []
        now_ms = int(time.time() * 1000)
        provider = conn.get("provider", "")

        # 1. Verifica se há credencial local descoberta no host
        if self.discovery:
            local = self.discovery.get_credential_for_provider(provider)
            if local and local.get("accessToken") and local.get("accessToken") != conn.get("accessToken"):
                exp_ms = now_ms + (3599 * 1000)
                res = {
                    "accessToken": local["accessToken"],
                    "refreshToken": local.get("refreshToken") or conn.get("refreshToken"),
                    "expiresAt": exp_ms,
                }
                src = local.get("source_path", "host")
                messages.append(f"Token sincronizado a partir do host ({src})")
                return True, res, messages

        # 2. Avaliação de expiração
        from .normalizer import parse_expiry_to_ms
        exp_ms = parse_expiry_to_ms(conn.get("expiresAt"))
        if not exp_ms:
            messages.append("Conexão OAuth sem registro temporal de expiração")
            return False, None, messages

        rem = int((exp_ms - now_ms) / 1000)
        if rem > margin_seconds:
            messages.append(f"Token válido por mais {rem // 60} min ({rem}s)")
            return False, None, messages

        # 3. Tentativa de refresh
        refresh_token = conn.get("refreshToken")
        token_url = self.KNOWN_TOKEN_URLS.get(provider.lower())
        client_id = os.environ.get(f"{provider.upper()}_CLIENT_ID")
        client_secret = os.environ.get(f"{provider.upper()}_CLIENT_SECRET")

        if token_url and refresh_token and client_id:
            try:
                body = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                }
                if client_secret:
                    body["client_secret"] = client_secret
                payload = urllib.parse.urlencode(body).encode("utf-8")
                req = urllib.request.Request(
                    token_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                        "User-Agent": f"{NOME_DO_PRODUTO}/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    new_tok = data.get("access_token") or data.get("accessToken")
                    if new_tok:
                        exp_in = int(data.get("expires_in", 3600))
                        res = {
                            "accessToken": new_tok,
                            "refreshToken": data.get("refresh_token", refresh_token),
                            "expiresAt": now_ms + (exp_in * 1000),
                        }
                        messages.append(f"Token OAuth renovado com sucesso ({exp_in}s)")
                        return True, res, messages
            except Exception as e:
                messages.append(f"Refresh remoto retornou: {e}")

        messages.append(f"Token próximo da expiração ({rem}s restantes)")
        return False, None, messages


class ApiKeyProvider:
    """Gerenciador e sanitizador para conexões de API Key no gateway."""

    def __init__(
        self,
        discovery: Optional[Any] = None,
        validate_credentials: bool = False,
        validation_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Optional[Any] = None,
    ):
        self.discovery = discovery
        # Desligado por padrão: montar o provider não pode gerar tráfego de saída.
        self.validate_credentials = validate_credentials
        self.validation_timeout = validation_timeout
        self.opener = opener

    def can_handle(self, conn: Dict[str, Any]) -> bool:
        # Uma instancia local carrega uma chave de fachada, entao `hasApiKey`
        # sozinho tambem casaria com ela. Como o motor consulta este provider
        # antes do LocalProvider, o catalogo local nunca seria descoberto -- e
        # por isso que o Ollama local aparecia sem modelo nenhum no painel.
        return bool(conn.get("hasApiKey")) and not LocalProvider.is_local_connection(conn)

    def check_and_refresh(self, conn: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
        messages = []
        # `modified` decide se vale gravar; `renewed` decide se conta como
        # renovacao no resumo do ciclo. Carimbar o horario de uma verificacao
        # muda a linha, mas nao renovou credencial nenhuma -- e contar isso
        # inflava o "N renovadas" do cron.
        modified = False
        renewed = False
        res = dict(conn)
        provider = conn.get("provider", "")

        # 1. Verifica se há chave de API mais recente no host
        if self.discovery:
            local = self.discovery.get_credential_for_provider(provider)
            if local and local.get("apiKey") and local.get("apiKey") != conn.get("apiKey"):
                res["apiKey"] = local["apiKey"]
                modified = True
                renewed = True
                src = local.get("source_path", "host")
                messages.append(f"Chave de API sincronizada a partir do host ({src})")

        # 2. Pergunta ao provedor se a chave ainda é aceita. Antes daqui a conexão
        # era declarada "operacional e ativa" sem nenhuma verificação.
        if self.validate_credentials:
            record = ConnectionRecord.from_row(res)
            result = check_connection(
                record, timeout=self.validation_timeout, opener=self.opener
            )
            res.update(result.to_dict())
            modified = True

            if result.state == STATE_VALID:
                res["testStatus"] = "active"
                # Um 4xx que nao seja 401/403 continua provando que a
                # autenticacao passou -- a sonda manda corpo vazio de
                # proposito, e o provedor so chega a reclamar do corpo depois
                # de aceitar a chave. Dizer apenas "aceita (HTTP 400)" fazia a
                # tela parecer errada; a frase agora explica o que o numero
                # significa.
                messages.append(
                    f"Autenticação aceita pelo provedor ({result.detail})"
                    if result.detail and "200" in str(result.detail)
                    else f"Autenticação aceita pelo provedor; a sondagem em si foi recusada ({result.detail})"
                )
            elif result.state == STATE_INVALID:
                res["testStatus"] = "invalid"
                messages.append(f"Chave RECUSADA pelo provedor ({result.detail})")
            elif result.state == STATE_RATE_LIMITED:
                messages.append(f"Provedor aplicou rate limit na validação ({result.detail})")
            elif result.state == STATE_UNREACHABLE:
                messages.append(f"Chave não verificada: {result.detail}")
            else:
                messages.append(result.detail or "Credencial não verificável")

        if not messages:
            messages.append("Chave de API inalterada")

        return renewed, res if modified else None, messages


# Catalog endpoints, in attempt order: Ollama-native and the OpenAI standard.
MODEL_CATALOG_PATHS = ("/api/tags", "/v1/models", "/models")
PROBE_TIMEOUT_SECONDS = 3.0

LOCAL_PROVIDER_MARKERS = ("ollama", "vllm", "lmstudio", "llamacpp", "localai", "openai-compatible")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


class LocalProvider:
    """Health monitor for local OpenAI-compatible instances (Ollama, vLLM, LM Studio)."""

    @staticmethod
    def _base_url(conn: Dict[str, Any]) -> str:
        especifico = conn.get("providerSpecificData") or {}
        return str(
            conn.get("baseUrl")
            or especifico.get("baseUrl")
            or especifico.get("baseURL")
            or ""
        )

    @classmethod
    def is_local_connection(cls, conn: Dict[str, Any]) -> bool:
        """Classificacao de "local" compartilhada, para os providers nao brigarem."""
        provider = str(conn.get("provider", "")).lower()
        endereco = cls._base_url(conn)
        if endereco:
            # Endereco declarado decide sozinho. O marcador "ollama" tambem casa
            # com a conta hospedada em https://ollama.com/v1, e trata-la como
            # local mandaria o sincronizador sondar um catalogo que nao existe
            # ali, alem de tirar a conexao do caminho de validacao de chave.
            return any(host in endereco for host in LOCAL_HOSTS)
        if any(marker in provider for marker in LOCAL_PROVIDER_MARKERS):
            return True
        return False

    def can_handle(self, conn: Dict[str, Any]) -> bool:
        if self.is_local_connection(conn):
            return True
        # Sem token e sem chave nao ha o que outro provider faca com a conexao.
        return not (conn.get("isOAuth") or conn.get("hasApiKey"))

    def discover_models(self, base_url: str, api_key: str = "") -> Tuple[List[str], str]:
        """Query the local instance catalog. Returns (models, error)."""
        if not base_url:
            return [], "baseUrl not declared on the connection"

        root = base_url.rstrip("/")
        # An OpenAI-shaped baseUrl already ends in /v1; the root serves /api/tags.
        origin = root[: -len("/v1")] if root.endswith("/v1") else root
        last_error = ""
        answered = False

        for path in MODEL_CATALOG_PATHS:
            target = f"{origin}{path}" if path.startswith("/api") else f"{root}{path}"
            try:
                req = urllib.request.Request(
                    target, headers={"User-Agent": f"{NOME_DO_PRODUTO}-LocalProbe/1.0"}
                )
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # The host answered, this path just is not the right one — keep trying.
                last_error = str(e)
                continue
            except (urllib.error.URLError, OSError) as e:
                # Nothing is listening: trying the remaining paths only multiplies the
                # timeout (3 endpoints x 3s) on every sweep. Give up now.
                return [], str(e)
            except ValueError as e:
                last_error = str(e)
                continue

            # Chegar aqui significa resposta HTTP valida e JSON parseavel: o
            # servico esta de pe, tendo modelo ou nao.
            answered = True
            models = self._extract_model_names(payload)
            if models:
                return models, ""

        if answered:
            return [], ""
        return [], last_error or "no model returned by the local instance"

    @staticmethod
    def _extract_model_names(payload: Any) -> List[str]:
        """Extract model names from the Ollama (/api/tags) and OpenAI (/v1/models) shapes."""
        if not isinstance(payload, dict):
            return []
        entries = payload.get("models") or payload.get("data") or []
        names = []
        for entry in entries:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("id") or entry.get("model")
                if name:
                    names.append(str(name))
        return names

    def check_and_refresh(self, conn: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
        """Sonda a instancia local.

        O primeiro elemento e a contagem de renovacao do ciclo: uma sondagem
        nunca renova credencial, entao e sempre False. O dicionario devolvido e
        o que deve ser gravado.
        """
        models, probe_error = self.discover_models(self._base_url(conn), conn.get("apiKey") or "")

        if models:
            return (
                False,
                {"discoveredModels": models, "testStatus": "active"},
                [f"Local instance answered with {len(models)} model(s): {', '.join(models[:5])}"],
            )

        # Erro vazio significa que a instancia respondeu com catalogo vazio --
        # instalacao nova, sem modelo baixado. Esta no ar.
        if not probe_error:
            return (
                False,
                {"discoveredModels": [], "testStatus": "active"},
                ["Local instance answered with an empty model catalog"],
            )

        # With no catalog response the connection is not assumed healthy: this is
        # exactly the "the local Ollama went down and nobody noticed" case.
        return (
            False,
            {"testStatus": "unreachable", "lastError": probe_error},
            [f"Local instance did not answer the model catalog: {probe_error}"],
        )
