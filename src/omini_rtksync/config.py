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

        return cls(
            db_path=db_path,
            host_home=host_home,
            omniroute_url=os.environ.get("OMNIROUTE_URL", "http://127.0.0.1:20128"),
            sync_interval=int(os.environ.get("SYNC_INTERVAL", "300")),
            refresh_margin=int(os.environ.get("REFRESH_MARGIN", "900")),
            enable_web=os.environ.get("ENABLE_WEB_DASHBOARD", "1") not in ("0", "false", "no"),
            web_host=os.environ.get("WEB_HOST", "0.0.0.0"),
            web_port=int(os.environ.get("WEB_PORT", "9191")),
            credential_paths=valid_paths,
        )
