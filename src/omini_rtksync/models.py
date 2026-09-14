"""Gateway connection model exposing the same interface the dashboard consumes.

database.py returns dictionaries (this gateway schema is relational). This layer
wraps them in an object carrying the derived properties the screen needs, keeping
the renderer identical to the sibling projects.
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

        Um dos gateways aninha em providerSpecificData; lendo só a raiz, instância
        local nenhuma exibia seus modelos.
        """
        specific = self.data.get("providerSpecificData")
        if isinstance(specific, dict):
            nested = specific.get("baseUrl") or specific.get("baseURL")
            if nested:
                return nested
        return (
            self.data.get("baseUrl")
            or self.data.get("base_url")
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
    def egress_status(self) -> str:
        """Como esta conexao sai para a internet: ``bound``, ``shared`` ou ``unknown``.

        Somente leitura: quem manda no vinculo e o gateway, que guarda
        os interruptores em ``provider_connections`` (``proxy_enabled`` e
        ``per_key_proxy_enabled``) e o vinculo em si em ``proxy_assignments``,
        com ``scope='account'`` e ``scope_id`` igual ao id da conexao. E exibido
        aqui porque uma conta que compartilha o mesmo endereco de saida com
        todas as outras e justamente o estado que o operador quer perceber, e
        nada no painel mostrava isso.
        """
        if self.data.get("proxyEnabled") is True or self.data.get("perKeyProxyEnabled") is True:
            return "bound" if self.data.get("egressProxy") else "shared"
        return "shared" if "proxyEnabled" in self.data else "unknown"

    @property
    def egress_binding(self) -> Optional[str]:
        """Nome ou identificador da saida vinculada, quando ha uma."""
        vinculo = self.data.get("egressProxy")
        return str(vinculo) if vinculo else None

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
    def last_refresh_at(self) -> Optional[str]:
        """Quando a credencial foi renovada/verificada pela ultima vez.

        lastRefreshAt e gravado na renovacao de OAuth; lastTested, na validacao
        da credencial. Sem expor isto, o painel diz "0 renovadas" e nao ha como
        saber se a ultima renovacao foi ha um minuto ou ha uma semana.
        """
        return (
            self.data.get("lastRefreshAt")
            or self.data.get("credentialCheckedAt")
            or self.data.get("lastTested")
            or None
        )

    @property
    def credential_state(self) -> Optional[str]:
        """Resultado da última validação viva da credencial, quando houve uma."""
        state = self.data.get("credentialState")
        return str(state) if state else None

    @property
    def rate_limit_active(self) -> bool:
        """Se a trava de rate limit ainda vale neste instante.

        `rateLimitedUntil` é um prazo, não uma bandeira: ele guarda o momento em
        que a janela do provedor se reabre. Tratar a simples presença do campo
        como "limitada" deixava a conexão amarela para sempre depois do primeiro
        429, porque nada apaga a marca quando o prazo vence.
        """
        until = parse_expiry_to_ms(self.data.get("rateLimitedUntil"))
        if until is None:
            return False
        return until > int(time.time() * 1000)

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
            estado = self.data.get("testStatus")
            if estado == "unreachable":
                return "unknown"
            # Uma conexão recém-criada ainda não foi sondada por ninguém. Com
            # CRON_ENABLED=0 ela pode nunca ser, e dizer "ativa" é alegar uma
            # saúde que nenhuma sonda confirmou.
            return "active" if estado in ("active", "ok", "success") else "not_checked"

        if self.is_oauth:
            # O gateway já pode ter carimbado a conexão como recusada. **Sem uma
            # sonda viva que diga o contrário**, o carimbo dele é a melhor
            # informação que existe — ignorá-lo mostrava como saudável uma
            # credencial que o próprio gateway sabe estar quebrada. Mas uma
            # validação viva vence tudo: o carimbo é do último erro do gateway
            # e não caduca sozinho, então honrá-lo mesmo depois de a sonda
            # aprovar a credencial repetia, ao contrário, a própria contradição
            # entre tela e banco que este arquivo existe para evitar.
            if probed != "valid" and self.data.get("testStatus") in ("invalid", "error", "failed"):
                return "invalid"
            remaining = self.remaining_seconds
            if remaining is None:
                return "no_expiration"
            if remaining <= 0:
                return "expired"
            if remaining < EXPIRING_SOON_SECONDS:
                return "expiring_soon"
            return "active"

        if self.has_api_key:
            if self.rate_limit_active:
                return "rate_limited"
            # Nunca sondada: dizer isso, em vez de alegar saúde que ninguém verificou.
            return "active" if probed == "valid" else "not_checked"

        # One gateway writes "active", another writes "ok"; both mean healthy.
        return "active" if self.data.get("testStatus") in ("active", "ok") else "unknown"


@dataclass
class VirtualKeyRecord:
    """Uma linha de ``api_keys`` vista pela lente do painel.

    Chave virtual e o token que o cliente apresenta ao gateway no lugar da
    credencial do provedor. Ela NAO se renova: nasce com prazo (ou sem nenhum) e
    vence. Por isso a coluna "ultima renovacao" da tabela carrega aqui a data de
    EMISSAO -- e o unico carimbo de tempo que a chave tem, e a coluna existe
    para casar com a dos irmaos.

    O material do token nunca chega a este objeto: ``get_all_api_keys`` sequer
    le a coluna.
    """

    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "VirtualKeyRecord":
        return cls(data=dict(row))

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def name(self) -> str:
        """Como a chave se identifica na tela: o nome dado a ela, senao o id.

        O id e um UUID -- identifica sem revelar nada. O prefixo do token seria
        mais reconhecivel e esta fora de questao: e um pedaco do segredo.
        """
        return str(self.data.get("name") or self.id)

    @property
    def issued_at(self) -> Optional[str]:
        return self.data.get("createdAt")

    @property
    def last_used_at(self) -> Optional[str]:
        return self.data.get("lastUsedAt")

    @property
    def revoked(self) -> bool:
        """Se o gateway ja recusa esta chave, por qualquer um dos tres motivos."""
        return bool(
            self.data.get("revokedAt")
            or self.data.get("isBanned")
            or self.data.get("isActive") is False
        )

    @property
    def expires_at_ms(self) -> Optional[int]:
        return parse_expiry_to_ms(self.data.get("expiresAt"))

    @property
    def remaining_seconds(self) -> Optional[int]:
        exp = self.expires_at_ms
        if exp is None:
            return None
        return int((exp - int(time.time() * 1000)) / 1000)

    @property
    def scopes(self) -> List[str]:
        return [str(s) for s in (self.data.get("scopes") or [])]

    @property
    def allowed_models(self) -> List[str]:
        return [str(m) for m in (self.data.get("allowedModels") or [])]

    @property
    def model_access_mode(self) -> str:
        return str(self.data.get("modelAccessMode") or "all")

    @property
    def health_status(self) -> str:
        """Estado da chave, nos mesmos termos que as conexoes usam.

        Revogada, banida e desativada sao caminhos diferentes para o mesmo fato
        observavel: o gateway recusa a chave. A tela diz "recusada" nos tres, e o
        modal detalha qual foi o caminho.
        """
        if self.revoked:
            return "invalid"
        remaining = self.remaining_seconds
        if remaining is None:
            return "no_expiration"
        if remaining <= 0:
            return "expired"
        if remaining < EXPIRING_SOON_SECONDS:
            return "expiring_soon"
        return "active"


@dataclass
class RegisteredModelRecord:
    """Um modelo do catalogo sincronizado, com a conexao que o serve.

    Modelo nao tem saude propria nem validade propria: ele responde enquanto a
    credencial da conexao que o publica for aceita. Por isso status, validade
    restante e ultima renovacao sao HERDADOS da conexao dona -- e o modal diz de
    qual conexao vieram, para que ninguem leia a linha como um veredito sobre o
    modelo em si.
    """

    data: Dict[str, Any] = field(default_factory=dict)
    connection: Optional[ConnectionRecord] = None

    @classmethod
    def from_row(
        cls, row: Dict[str, Any], connection: Optional[ConnectionRecord] = None
    ) -> "RegisteredModelRecord":
        return cls(data=dict(row), connection=connection)

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.id)

    @property
    def provider(self) -> str:
        return str(self.data.get("provider") or "")

    @property
    def source(self) -> str:
        return str(self.data.get("source") or "")

    @property
    def description(self) -> str:
        return str(self.data.get("description") or "")

    @property
    def input_token_limit(self) -> Optional[int]:
        return self.data.get("inputTokenLimit")

    @property
    def output_token_limit(self) -> Optional[int]:
        return self.data.get("outputTokenLimit")

    @property
    def supported_endpoints(self) -> List[str]:
        return [str(e) for e in (self.data.get("supportedEndpoints") or [])]

    @property
    def connection_name(self) -> Optional[str]:
        return self.connection.name if self.connection else None

    @property
    def health_status(self) -> str:
        # Sem conexao dona identificada (catalogo orfao de uma conexao removida)
        # nao ha o que afirmar: dizer "ativo" seria inventar a sondagem.
        return self.connection.health_status if self.connection else "not_checked"

    @property
    def remaining_seconds(self) -> Optional[int]:
        return self.connection.remaining_seconds if self.connection else None

    @property
    def last_refresh_at(self) -> Optional[str]:
        return self.connection.last_refresh_at if self.connection else None
