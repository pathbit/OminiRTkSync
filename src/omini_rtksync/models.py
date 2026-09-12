"""Modelo de conexão do OmniRoute, com a mesma interface que o dashboard consome.

O database.py devolve dicionários (o schema do OmniRoute é relacional). Esta
camada os embrulha num objeto com as propriedades derivadas que a tela precisa,
mantendo o renderizador igual ao do projeto irmão 9RTKSync.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .normalizer import parse_expiry_to_ms

# Nomes de provedor que identificam uma instância local / compatível com OpenAI.
LOCAL_PROVIDER_MARKERS = ("ollama", "vllm", "lmstudio", "llamacpp", "localai", "openai-compatible")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")

# Margem abaixo da qual o token é considerado "expirando em breve" (15 min).
EXPIRING_SOON_SECONDS = 900


@dataclass
class ConnectionRecord:
    """Uma linha de provider_connections vista pela ótica do painel."""

    id: str
    provider: str
    name: str
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ConnectionRecord":
        """Constrói o registro a partir do dicionário devolvido por get_all_connections."""
        return cls(
            id=str(row.get("id", "")),
            provider=str(row.get("provider", "")),
            name=str(row.get("name") or row.get("provider") or ""),
            data=dict(row),
        )

    @property
    def is_oauth(self) -> bool:
        return bool(self.data.get("refreshToken") or self.data.get("accessToken"))

    @property
    def has_api_key(self) -> bool:
        return bool(self.data.get("apiKey"))

    @property
    def api_key(self) -> Optional[str]:
        return self.data.get("apiKey")

    @property
    def is_local(self) -> bool:
        """Indica se a conexão aponta para uma instância local.

        Uma instância local costuma exigir uma chave de API de fachada, então
        checar apenas has_api_key a classificaria como provedor de nuvem.
        """
        provider = self.provider.lower()
        if any(marker in provider for marker in LOCAL_PROVIDER_MARKERS):
            return True
        base_url = str(self.base_url or "")
        return any(host in base_url for host in LOCAL_HOSTS)

    @property
    def base_url(self) -> Optional[str]:
        raw = self.data.get("raw") or {}
        return (
            self.data.get("baseUrl")
            or self.data.get("base_url")
            or raw.get("base_url")
            or raw.get("baseUrl")
            or None
        )

    @property
    def local_models(self) -> List[str]:
        """Modelos descobertos na instância local na última varredura."""
        models = self.data.get("discoveredModels") or self.data.get("models") or []
        if isinstance(models, str):
            return [models]
        return [str(m) for m in models if m]

    @property
    def expires_at_ms(self) -> Optional[int]:
        """Expiração normalizada em epoch milissegundos, seja ISO ou numérica."""
        return parse_expiry_to_ms(self.data.get("expiresAt"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        exp = self.expires_at_ms
        if exp is None:
            return None
        return int((exp - int(time.time() * 1000)) / 1000)

    @property
    def health_status(self) -> str:
        """Classificação semântica do estado da conexão."""
        if self.is_local:
            # unreachable é gravado quando o catálogo de modelos não responde.
            return "desconhecido" if self.data.get("testStatus") == "unreachable" else "ativo"

        if self.is_oauth:
            remaining = self.remaining_seconds
            if remaining is None:
                return "sem_expiracao"
            if remaining <= 0:
                return "expirado"
            if remaining < EXPIRING_SOON_SECONDS:
                return "expirando_em_breve"
            return "ativo"

        if self.has_api_key:
            if self.data.get("rateLimitedUntil"):
                return "rate_limited"
            return "ativo"

        # O OmniRoute usa "active"; o 9Router usa "ok". Ambos significam saudável.
        return "ativo" if self.data.get("testStatus") in ("active", "ok") else "desconhecido"
