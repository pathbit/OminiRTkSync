"""Autenticação do dashboard com credencial de recuperação (break-glass).

Regra de validação, nesta ordem:

1. Se existem credenciais salvas (trocadas pela tela), elas são a fonte de verdade:
   usuário e senha precisam bater exatamente com o que está salvo.
2. Se nada foi salvo ainda, valem as credenciais de fábrica (padrão ou vindas do
   ambiente) — é o estado de primeiro acesso do container.
3. Independente do que existe salvo, o usuário `admin` com a senha igual ao
   *hash de recuperação* sempre entra. É a saída de emergência para quem esqueceu
   a senha, sem precisar apagar o volume do container.
4. Qualquer outra combinação é inválida.

O hash de recuperação vem de DASHBOARD_RECOVERY_HASH. Quando a variável não é
definida, um hash aleatório é gerado no primeiro boot, gravado em disco com
permissão 0600 e registrado uma única vez no log — é lá que o operador vai buscá-lo.

Todas as comparações usam hmac.compare_digest para não vazar informação por tempo
de resposta.
"""

import hashlib
import hmac
import json
import os
import secrets
from typing import Optional, Tuple

RECOVERY_FILE_NAME = ".dashboard_recovery"
RECOVERY_USER = "admin"


def constant_time_equals(a: str, b: str) -> bool:
    """Compara duas strings em tempo constante."""
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


def derive_recovery_hash(secret: str) -> str:
    """Deriva o hash de recuperação exibido ao operador a partir de um segredo."""
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def read_stored_credentials(auth_file: str) -> Optional[Tuple[str, str]]:
    """Lê as credenciais gravadas pela tela. Devolve None quando ainda não houve troca."""
    if not auth_file or not os.path.exists(auth_file):
        return None
    try:
        with open(auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        user = data.get("user")
        password = data.get("password")
        if user and password:
            return str(user), str(password)
    except (OSError, ValueError):
        pass
    return None


def resolve_recovery_hash(recovery_file: str) -> str:
    """Obtém o hash de recuperação: ambiente primeiro, senão o gerado/salvo localmente."""
    from_env = os.environ.get("DASHBOARD_RECOVERY_HASH", "").strip()
    if from_env:
        return from_env

    if recovery_file and os.path.exists(recovery_file):
        try:
            with open(recovery_file, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        except OSError:
            pass

    return ""


def ensure_recovery_hash(recovery_file: str) -> Tuple[str, bool]:
    """Garante que existe um hash de recuperação. Devolve (hash, foi_gerado_agora)."""
    existing = resolve_recovery_hash(recovery_file)
    if existing:
        return existing, False

    generated = derive_recovery_hash(secrets.token_hex(32))
    if recovery_file:
        try:
            os.makedirs(os.path.dirname(recovery_file) or ".", exist_ok=True)
            # 0600: apenas o dono do processo lê o segredo de emergência.
            fd = os.open(recovery_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(generated)
        except OSError:
            # Sem disco gravável o hash vira efêmero (válido só nesta execução),
            # mas o serviço continua subindo.
            pass
    return generated, True


def verify_credentials(
    user: str,
    password: str,
    *,
    stored: Optional[Tuple[str, str]],
    factory_user: str,
    factory_password: str,
    recovery_hash: str = "",
) -> bool:
    """Aplica a regra de validação descrita no topo do módulo."""
    if not user or not password:
        return False

    # 3. Saída de emergência: admin + hash de recuperação entra sempre.
    if recovery_hash and constant_time_equals(user, RECOVERY_USER):
        if constant_time_equals(password, recovery_hash):
            return True

    if stored is not None:
        # 1. Já houve troca de senha: só as credenciais salvas valem.
        stored_user, stored_password = stored
        return constant_time_equals(user, stored_user) and constant_time_equals(
            password, stored_password
        )

    # 2. Primeiro acesso: valem as credenciais de fábrica.
    return constant_time_equals(user, factory_user) and constant_time_equals(
        password, factory_password
    )
