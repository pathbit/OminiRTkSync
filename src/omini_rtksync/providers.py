"""Provedores de renovação OAuth para OmniRoute."""

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class GoogleProvider:
    """Renovador OAuth para contas Google no OmniRoute."""

    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, credential_paths: Optional[List[str]] = None):
        self.credential_paths = credential_paths or []

    def find_local_credential_file(self) -> Optional[str]:
        for p in self.credential_paths:
            if p and os.path.exists(p) and os.path.isfile(p):
                return p
        return None

    def read_local_credential(self) -> Optional[Dict[str, Any]]:
        path = self.find_local_credential_file()
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def refresh(
        self, refresh_token: str, client_id: str, client_secret: str
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
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
