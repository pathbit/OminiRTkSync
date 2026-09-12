"""Configurações globais e carregamento de variáveis de ambiente para o OminiRTKSync."""

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .auth import (
    RECOVERY_FILE_NAME,
    ensure_recovery_hash,
    read_stored_credentials,
    resolve_recovery_hash,
    verify_credentials,
)


def load_dotenv(dotenv_path: str = ".env") -> None:
    """Carrega variaveis de um arquivo .env para os.environ se nao estiverem definidas."""
    if not os.path.isfile(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


@dataclass
class Settings:
    """Configurações de execução do OminiRTKSync para OmniRoute."""
    db_path: str
    host_home: str = ""
    omniroute_url: str = "http://127.0.0.1:20128"
    sync_interval: int = 300
    refresh_margin: int = 900
    enable_web: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 9090
    credential_paths: List[str] = None
    dashboard_user: str = "admin"
    dashboard_password: str = "pathbit"
    cron_interval: int = 300
    cron_enabled: bool = True
    # Quando DASHBOARD_USER/DASHBOARD_PASSWORD vêm explicitamente do ambiente, elas
    # passam a ser a fonte de verdade e o arquivo salvo pela tela é ignorado. É o
    # que permite operar 100% headless (Docker, Kubernetes, CI) sem nunca abrir o
    # dashboard para configurar nada.
    dashboard_auth_from_env: bool = False

    def get_recovery_file_path(self) -> str:
        """Caminho do arquivo que guarda o hash de recuperação gerado localmente."""
        return os.path.join(os.path.dirname(self.get_auth_file_path()), RECOVERY_FILE_NAME)

    def get_recovery_hash(self) -> str:
        """Hash de recuperação em vigor (ambiente ou gerado no primeiro boot)."""
        return resolve_recovery_hash(self.get_recovery_file_path())

    def ensure_recovery_hash(self) -> Tuple[str, bool]:
        """Garante a existência do hash de recuperação. Devolve (hash, foi_gerado_agora)."""
        return ensure_recovery_hash(self.get_recovery_file_path())

    def get_stored_credentials(self) -> Optional[Tuple[str, str]]:
        """Credenciais gravadas pela tela, ou None quando o ambiente é autoritativo."""
        if self.dashboard_auth_from_env:
            return None
        return read_stored_credentials(self.get_auth_file_path())

    def verify_credentials(self, user: str, password: str) -> bool:
        """Valida um par usuário/senha, incluindo a credencial de recuperação."""
        return verify_credentials(
            user,
            password,
            stored=self.get_stored_credentials(),
            factory_user=self.dashboard_user,
            factory_password=self.dashboard_password,
            recovery_hash=self.get_recovery_hash(),
        )

    def get_auth_file_path(self) -> str:
        base_dir = os.environ.get("DATA_DIR", "")
        if not base_dir and self.db_path:
            base_dir = os.path.dirname(self.db_path)
        if not base_dir or not os.path.exists(base_dir):
            base_dir = os.path.expanduser("~")
        return os.path.join(base_dir, ".dashboard_auth.json")

    def get_auth_credentials(self) -> tuple[str, str]:
        # Ambiente explícito vence o arquivo: sem isso, uma única troca de senha
        # pela tela deixaria DASHBOARD_USER/DASHBOARD_PASSWORD inertes para sempre.
        if self.dashboard_auth_from_env:
            return self.dashboard_user, self.dashboard_password

        auth_file = self.get_auth_file_path()
        if os.path.exists(auth_file):
            try:
                import json
                with open(auth_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                u = data.get("user") or self.dashboard_user
                p = data.get("password") or self.dashboard_password
                if u and p:
                    return str(u), str(p)
            except Exception:
                pass
        return self.dashboard_user, self.dashboard_password

    def is_default_password(self) -> bool:
        _, p = self.get_auth_credentials()
        return p == "pathbit"

    def update_auth_credentials(self, user: str, new_pass: str) -> bool:
        # Em modo headless o ambiente é imutável pela tela — gravar o arquivo aqui
        # criaria um estado fantasma que get_auth_credentials nunca leria.
        if self.dashboard_auth_from_env:
            return False

        auth_file = self.get_auth_file_path()
        try:
            import json
            payload = {"user": user.strip() or "admin", "password": new_pass.strip()}
            with open(auth_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            self.dashboard_user = payload["user"]
            self.dashboard_password = payload["password"]
            return True
        except Exception:
            return False

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "Settings":
        load_dotenv(env_file)
        host_home = os.environ.get("HOST_HOME", "")
        if not host_home:
            if os.path.exists("/root/host") and os.path.isdir("/root/host"):
                host_home = "/root/host"
            elif os.path.exists("/host") and os.path.isdir("/host"):
                host_home = "/host"
            else:
                host_home = os.path.expanduser("~")

        default_paths = [
            os.environ.get("ANTIGRAVITY_TOKEN_PATH", ""),
            os.path.join(host_home, ".gemini", "oauth_creds.json"),
            os.path.join(host_home, ".gemini", "jetski-standalone-oauth-token"),
            os.path.join(host_home, ".config", "antigravity", "jetski-standalone-oauth-token"),
            "/root/.gemini/jetski-standalone-oauth-token",
        ]
        valid_paths = [p for p in default_paths if p]

        # Descoberta de banco SQLite do OmniRoute
        db_path = os.environ.get("DB_PATH", "")
        if not db_path:
            candidate_dbs = [
                "/app/data/storage.sqlite",
                "/app/data/data.sqlite",
                os.path.join(host_home, ".omniroute", "data", "storage.sqlite"),
                os.path.join(host_home, ".omniroute", "storage.sqlite"),
                os.path.join(host_home, ".omniroute", "data.sqlite"),
            ]
            for candidate in candidate_dbs:
                if os.path.exists(candidate):
                    db_path = candidate
                    break
            if not db_path:
                db_path = candidate_dbs[0]

        # Só considera "vindo do ambiente" quando a variável foi realmente definida,
        # para não transformar o padrão de fábrica em configuração autoritativa.
        env_user = os.environ.get("DASHBOARD_USER")
        env_pass = os.environ.get("DASHBOARD_PASSWORD")
        d_user = env_user or "admin"
        d_pass = env_pass or "pathbit"
        auth_from_env = bool(env_user or env_pass)

        sync_int = int(os.environ.get("SYNC_INTERVAL", "300"))
        cron_int = int(os.environ.get("CRON_INTERVAL", str(sync_int)))
        cron_on = os.environ.get("CRON_ENABLED", "1") not in ("0", "false", "no")

        return cls(
            db_path=db_path,
            host_home=host_home,
            omniroute_url=os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128"),
            sync_interval=sync_int,
            refresh_margin=int(os.environ.get("REFRESH_MARGIN", "900")),
            enable_web=os.environ.get("ENABLE_WEB_DASHBOARD", "1") not in ("0", "false", "no"),
            web_host=os.environ.get("WEB_HOST", "0.0.0.0"),
            web_port=int(os.environ.get("WEB_PORT", "9090")),
            credential_paths=valid_paths,
            dashboard_user=d_user,
            dashboard_password=d_pass,
            cron_interval=cron_int,
            cron_enabled=cron_on,
            dashboard_auth_from_env=auth_from_env,
        )
