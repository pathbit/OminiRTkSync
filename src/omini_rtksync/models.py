"""OmniRoute connection model exposing the same interface the dashboard consumes.

database.py returns dictionaries (the OmniRoute schema is relational). This layer
wraps them in an object carrying the derived properties the screen needs, keeping
the renderer identical to the sibling project 9RTKSync.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .normalizer import parse_expiry_to_ms

# Provider names that suggest a local instance. A marker alone is not proof:
# "ollama" is also the name of Ollama Cloud, a hosted service that must never
# be probed on /api/tags.
LOCAL_PROVIDER_MARKERS = ("ollama", "vllm", "lmstudio", "llamacpp", "localai", "openai-compatible")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", ".local")

# Threshold below which a token counts as "expiring soon" (15 min).
EXPIRING_SOON_SECONDS = 900


@dataclass
class ConnectionRecord:
    """A provider_connections row seen through the panel's lens."""

    id: str
    provider: str
    name: str
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ConnectionRecord":
        """Build the record from the dictionary returned by get_all_connections."""
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
    def access_token(self) -> Optional[str]:
        return self.data.get("accessToken")

    @property
    def refresh_token(self) -> Optional[str]:
        return self.data.get("refreshToken")

    @property
    def is_local(self) -> bool:
        """Whether the connection really points at an instance on this machine.

        Classification is driven by the address, not by the provider name. Only
        when no address is declared does a marker like "openai-compatible" --
        which has no hosted counterpart -- stand on its own.
        """
        base_url = str(self.base_url or "").lower()
        if base_url:
            return any(host in base_url for host in LOCAL_HOSTS)
        return "openai-compatible" in self.provider.lower()

    @property
    def base_url(self) -> Optional[str]:
        """Endereço do provedor, onde quer que o gateway o tenha guardado.

        O 9Router aninha em providerSpecificData; lendo só a raiz, instância
        local nenhuma exibia seus modelos.
        """
        specific = self.data.get("providerSpecificData")
        if isinstance(specific, dict):
            nested = specific.get("baseUrl") or specific.get("baseURL")
            if nested:
                return nested
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
        """Models discovered on the local instance during the last sweep."""
        models = self.data.get("discoveredModels") or self.data.get("models") or []
        if isinstance(models, str):
            return [models]
        return [str(m) for m in models if m]

    @property
    def expires_at_ms(self) -> Optional[int]:
        """Expiry normalized to epoch milliseconds, whether ISO or numeric."""
        return parse_expiry_to_ms(self.data.get("expiresAt"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        exp = self.expires_at_ms
        if exp is None:
            return None
        return int((exp - int(time.time() * 1000)) / 1000)

    @property
    def credential_state(self) -> Optional[str]:
        """Resultado da última validação viva da credencial, quando houve uma."""
        state = self.data.get("credentialState")
        return str(state) if state else None

    @property
    def health_status(self) -> str:
        """Semantic classification of the connection state.

        Uma validação viva vence tudo: chave que o provedor recusa está
        quebrada, não importa o que o gateway tenha carimbado por último.
        """
        probed = self.credential_state
        if probed in ("invalid", "rate_limited", "unreachable"):
            return probed

        if self.is_local:
            # unreachable is written when the model catalog does not answer.
            return "unknown" if self.data.get("testStatus") == "unreachable" else "active"

        if self.is_oauth:
            remaining = self.remaining_seconds
            if remaining is None:
                return "no_expiration"
            if remaining <= 0:
                return "expired"
            if remaining < EXPIRING_SOON_SECONDS:
                return "expiring_soon"
            return "active"

        if self.has_api_key:
            if self.data.get("rateLimitedUntil"):
                return "rate_limited"
            # Nunca sondada: dizer isso, em vez de alegar saúde que ninguém verificou.
            return "active" if probed == "valid" else "not_checked"

        # OmniRoute writes "active"; 9Router writes "ok". Both mean healthy.
        return "active" if self.data.get("testStatus") in ("active", "ok") else "unknown"
