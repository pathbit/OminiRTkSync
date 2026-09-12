"""Configurações globais e carregamento de variáveis de ambiente para o OminiRTKSync."""

import os
from dataclasses import dataclass
from typing import List


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
    web_port: int = 9191
    credential_paths: List[str] = None
    dashboard_user: str = "admin"
    dashboard_password: str = "pathbit"
    cron_interval: int = 300

    def get_auth_file_path(self) -> str:
        base_dir = os.environ.get("DATA_DIR", "")
        if not base_dir and self.db_path:
            base_dir = os.path.dirname(self.db_path)
        if not base_dir or not os.path.exists(base_dir):
            base_dir = os.path.expanduser("~")
        return os.path.join(base_dir, ".dashboard_auth.json")

    def get_auth_credentials(self) -> tuple[str, str]:
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
    def from_env(cls) -> "Settings":
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

        d_user = os.environ.get("DASHBOARD_USER", "admin")
        d_pass = os.environ.get("DASHBOARD_PASSWORD", "pathbit")
        sync_int = int(os.environ.get("SYNC_INTERVAL", "300"))

        return cls(
            db_path=db_path,
            host_home=host_home,
            omniroute_url=os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128"),
            sync_interval=sync_int,
            refresh_margin=int(os.environ.get("REFRESH_MARGIN", "900")),
            enable_web=os.environ.get("ENABLE_WEB_DASHBOARD", "1") not in ("0", "false", "no"),
            web_host=os.environ.get("WEB_HOST", "0.0.0.0"),
            web_port=int(os.environ.get("WEB_PORT", "9191")),
            credential_paths=valid_paths,
            dashboard_user=d_user,
            dashboard_password=d_pass,
            cron_interval=sync_int,
        )
