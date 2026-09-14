"""Configurações globais e carregamento de variáveis de ambiente deste sincronizador."""

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .auth import (
    RECOVERY_FILE_NAME,
    ensure_recovery_hash,
    read_stored_credentials,
    read_db_credentials,
    write_db_credentials,
    validate_password_strength,
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
    """Configurações de execução deste sincronizador."""
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
    # Sem senha de fabrica: um valor estatico e, por definicao, uma credencial
    # publica. O primeiro acesso e feito com a credencial de recuperacao, que e
    # sorteada no primeiro boot e gravada com modo 0600.
    dashboard_password: str = ""
    cron_interval: int = 300
    cron_enabled: bool = True
    # Validação viva das credenciais: pergunta ao provedor se a chave ainda é
    # aceita, em vez de pintar a linha de verde só porque existe uma chave.
    validate_credentials: bool = True
    validation_timeout: float = 8.0
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
        # O SQLite do painel e a fonte de verdade; o .dashboard_auth.json so
        # existe para nao trancar quem ja tinha senha antes desta mudanca.
        return read_db_credentials(self.get_prefs_path()) or read_stored_credentials(
            self.get_auth_file_path()
        )

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

    def get_prefs_path(self) -> str:
        """Banco SQLite do painel, onde vivem preferencias e credenciais."""
        from .prefs import resolve_prefs_path

        return resolve_prefs_path(os.path.dirname(self.get_auth_file_path()))

    def has_stored_password(self) -> bool:
        """Se ja existe senha definida pelo usuario no banco do painel."""
        if self.dashboard_auth_from_env:
            return True
        return read_db_credentials(self.get_prefs_path()) is not None

    def get_auth_file_path(self) -> str:
        base_dir = os.environ.get("DATA_DIR", "")
        if not base_dir and self.db_path:
            base_dir = os.path.dirname(self.db_path)
        # O diretorio e CRIADO, nao contornado. No primeiro boot ele ainda nao
        # existe -- o gateway e quem o cria ao subir -- e cair para $HOME
        # gravava o banco de preferencias, com a senha do painel dentro, fora do
        # volume de dados: a senha sumia ao recriar o container, e o painel
        # voltava a pedir a credencial de recuperacao.
        if base_dir:
            try:
                os.makedirs(base_dir, exist_ok=True)
            except OSError:
                # Caminho somente leitura ou invalido: ai sim nao ha onde
                # gravar, e $HOME e o unico lugar que resta.
                base_dir = ""
        if not base_dir or not os.path.isdir(base_dir):
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
        """Se o painel ainda roda sem senha própria.

        O aviso de segurança depende disto: ele some assim que existe uma senha
        gravada no SQLite, e não pela comparação com um texto fixo qualquer.
        """
        return not self.has_stored_password()

    def check_password_strength(self, new_pass: str) -> list:
        """Chaves de tradução das regras de senha que o valor não cumpre."""
        return validate_password_strength(new_pass)

    def update_auth_credentials(self, user: str, new_pass: str) -> bool:
        """Grava as credenciais do painel no SQLite, como hash.

        Recusa senha fraca: a política de força é obrigatória. Em modo headless
        o ambiente é imutável pela tela, e gravar aqui criaria estado fantasma
        que get_auth_credentials nunca leria.
        """
        if self.dashboard_auth_from_env:
            return False

        new_pass = (new_pass or "").strip()
        if validate_password_strength(new_pass):
            return False

        final_user = (user or "").strip() or "admin"
        if not write_db_credentials(self.get_prefs_path(), final_user, new_pass):
            return False

        self.dashboard_user = final_user
        self.dashboard_password = new_pass
        # O arquivo em texto puro perde a razão de existir assim que a senha
        # passa a viver no banco.
        try:
            os.remove(self.get_auth_file_path())
        except OSError:
            # O arquivo ja pode nao existir -- e o caso comum, porque a senha
            # nasce direto no banco. Falhar aqui reverteria uma troca de senha
            # que ja deu certo.
            pass
        return True

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

        # Descoberta do banco SQLite do gateway
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

        # Quem manda no modo "credencial gerida pelo ambiente" e a SENHA, nunca o
        # nome de usuario: nome sozinho nao e credencial. Com a regra anterior,
        # o docker-compose de exemplo (DASHBOARD_USER=admin e
        # DASHBOARD_PASSWORD vazia) marcava a autenticacao como autoritativa do
        # ambiente, e o painel recusava para sempre definir a senha pela tela.
        # A instalacao ficava presa na credencial de recuperacao e o aviso de
        # seguranca nunca sumia, porque nunca havia senha gravada no SQLite.
        env_user = os.environ.get("DASHBOARD_USER")
        env_pass = os.environ.get("DASHBOARD_PASSWORD")
        d_user = env_user or "admin"
        d_pass = env_pass or ""
        auth_from_env = bool(d_pass)

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
            validate_credentials=os.environ.get("CREDENTIAL_CHECK_ENABLED", "1")
            not in ("0", "false", "no"),
            validation_timeout=float(os.environ.get("CREDENTIAL_CHECK_TIMEOUT", "8")),
            dashboard_auth_from_env=auth_from_env,
        )
