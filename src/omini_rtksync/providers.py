"""Provedores universais de tokens e conexões para OmniRoute."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class GoogleProvider:
    """Renovador OAuth para contas Google (Antigravity / Gemini CLI) no OmniRoute."""

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
                "User-Agent": "OminiRTKSync/1.0",
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
    """Monitor e sincronizador OAuth genérico para OmniRoute (Claude, GitHub, Codex, Kiro)."""

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
                        "User-Agent": "OminiRTKSync/1.0",
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
    """Gerenciador e sanitizador para conexões de API Key no OmniRoute."""

    def __init__(self, discovery: Optional[Any] = None):
        self.discovery = discovery

    def can_handle(self, conn: Dict[str, Any]) -> bool:
        return bool(conn.get("hasApiKey"))

    def check_and_refresh(self, conn: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
        messages = []
        modified = False
        res = dict(conn)
        provider = conn.get("provider", "")

        # 1. Verifica se há chave de API mais recente no host
        if self.discovery:
            local = self.discovery.get_credential_for_provider(provider)
            if local and local.get("apiKey") and local.get("apiKey") != conn.get("apiKey"):
                res["apiKey"] = local["apiKey"]
                modified = True
                src = local.get("source_path", "host")
                messages.append(f"Chave de API sincronizada a partir do host ({src})")

        if not messages:
            messages.append("Chave de API operacional e ativa")

        return modified, res if modified else None, messages


# Catalog endpoints, in attempt order: Ollama-native and the OpenAI standard.
MODEL_CATALOG_PATHS = ("/api/tags", "/v1/models", "/models")
PROBE_TIMEOUT_SECONDS = 3.0

LOCAL_PROVIDER_MARKERS = ("ollama", "vllm", "lmstudio", "llamacpp", "localai", "openai-compatible")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


class LocalProvider:
    """Health monitor for local OpenAI-compatible instances (Ollama, vLLM, LM Studio)."""

    @staticmethod
    def _base_url(conn: Dict[str, Any]) -> str:
        raw = conn.get("raw") or {}
        return str(conn.get("baseUrl") or raw.get("base_url") or raw.get("baseUrl") or "")

    def can_handle(self, conn: Dict[str, Any]) -> bool:
        provider = str(conn.get("provider", "")).lower()
        if any(marker in provider for marker in LOCAL_PROVIDER_MARKERS):
            return True
        if any(host in self._base_url(conn) for host in LOCAL_HOSTS):
            return True
        return not (conn.get("isOAuth") or conn.get("hasApiKey"))

    def discover_models(self, base_url: str, api_key: str = "") -> Tuple[List[str], str]:
        """Query the local instance catalog. Returns (models, error)."""
        if not base_url:
            return [], "baseUrl not declared on the connection"

        root = base_url.rstrip("/")
        # An OpenAI-shaped baseUrl already ends in /v1; the root serves /api/tags.
        origin = root[: -len("/v1")] if root.endswith("/v1") else root
        last_error = ""

        for path in MODEL_CATALOG_PATHS:
            target = f"{origin}{path}" if path.startswith("/api") else f"{root}{path}"
            try:
                req = urllib.request.Request(
                    target, headers={"User-Agent": "OminiRTKSync-LocalProbe/1.0"}
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

            models = self._extract_model_names(payload)
            if models:
                return models, ""

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
        models, probe_error = self.discover_models(self._base_url(conn), conn.get("apiKey") or "")

        if models:
            return (
                True,
                {"discoveredModels": models, "testStatus": "active"},
                [f"Local instance answered with {len(models)} model(s): {', '.join(models[:5])}"],
            )

        # With no catalog response the connection is not assumed healthy: this is
        # exactly the "the local Ollama went down and nobody noticed" case.
        return (
            True,
            {"testStatus": "unreachable", "lastError": probe_error},
            [f"Local instance did not answer the model catalog: {probe_error}"],
        )
