"""Universal host credential discovery engine for OminiRTKSync."""

import json
import os
import re
from typing import Any, Dict, List, Optional


class HostDiscoveryEngine:
    """
    Locates and extracts local credentials from tools and CLIs installed on host.
    Operates seamlessly whether running natively on host or inside container
    with host directory mounted at HOST_HOME (e.g. /root/host).
    """

    def __init__(self, host_home: Optional[str] = None, extra_paths: Optional[List[str]] = None):
        self.host_home = self._resolve_host_home(host_home)
        self.extra_paths = extra_paths or []

    @staticmethod
    def _resolve_host_home(override: Optional[str] = None) -> str:
        if override and os.path.exists(override):
            return override
        env_host = os.environ.get("HOST_HOME")
        if env_host and os.path.exists(env_host):
            return env_host
        if os.path.exists("/root/host") and os.path.isdir("/root/host"):
            return "/root/host"
        if os.path.exists("/host") and os.path.isdir("/host"):
            return "/host"
        return os.path.expanduser("~")

    def _read_json(self, path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(path) or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def discover_google(self) -> Optional[Dict[str, Any]]:
        """Discover Google Antigravity / Gemini CLI tokens."""
        # 1. jetski-standalone-oauth-token (Antigravity standalone token)
        jetski_candidates = [
            os.path.join(self.host_home, ".gemini", "jetski-standalone-oauth-token"),
            os.path.join(self.host_home, ".config", "antigravity", "jetski-standalone-oauth-token"),
            "/root/.gemini/jetski-standalone-oauth-token",
        ] + self.extra_paths

        for p in jetski_candidates:
            data = self._read_json(p)
            if data:
                tok_dict = data.get("token") if isinstance(data.get("token"), dict) else data
                acc = tok_dict.get("access_token") or tok_dict.get("accessToken")
                ref = tok_dict.get("refresh_token") or tok_dict.get("refreshToken")
                if acc or ref:
                    return {
                        "source_path": p,
                        "accessToken": acc,
                        "refreshToken": ref,
                        "clientId": tok_dict.get("client_id") or data.get("client_id"),
                        "clientSecret": tok_dict.get("client_secret") or data.get("client_secret"),
                        "expiry": tok_dict.get("expiry"),
                    }

        # 2. oauth_creds.json
        creds_candidates = [
            os.path.join(self.host_home, ".gemini", "oauth_creds.json"),
            os.path.join(self.host_home, ".config", "antigravity", "oauth_creds.json"),
            "/root/.gemini/oauth_creds.json",
        ]
        for p in creds_candidates:
            data = self._read_json(p)
            if data and (data.get("access_token") or data.get("refresh_token")):
                return {
                    "source_path": p,
                    "accessToken": data.get("access_token"),
                    "refreshToken": data.get("refresh_token"),
                    "clientId": data.get("client_id") or data.get("clientId"),
                    "clientSecret": data.get("client_secret") or data.get("clientSecret"),
                    "expiry": data.get("expiry_date"),
                }

        return None

    def discover_claude(self) -> Optional[Dict[str, Any]]:
        """Discover Claude Code CLI and Anthropic configurations."""
        settings_path = os.path.join(self.host_home, ".claude", "settings.json")
        data = self._read_json(settings_path)
        if data and isinstance(data.get("env"), dict):
            env = data["env"]
            api_key = env.get("ANTHROPIC_API_KEY")
            if api_key:
                return {
                    "source_path": settings_path,
                    "apiKey": api_key,
                    "baseUrl": env.get("ANTHROPIC_BASE_URL"),
                }

        claude_json_path = os.path.join(self.host_home, ".claude.json")
        data = self._read_json(claude_json_path)
        if data:
            oauth_acc = data.get("oauthAccount") if isinstance(data.get("oauthAccount"), dict) else None
            return {
                "source_path": claude_json_path,
                "oauthAccount": oauth_acc,
                "has_oauth": bool(oauth_acc),
                "email": oauth_acc.get("emailAddress") if oauth_acc else None,
            }

        cred_paths = [
            os.path.join(self.host_home, ".claude", "credentials.json"),
            os.path.join(self.host_home, ".config", "claude", "credentials.json"),
        ]
        for p in cred_paths:
            data = self._read_json(p)
            if data and (data.get("apiKey") or data.get("token")):
                return {
                    "source_path": p,
                    "apiKey": data.get("apiKey") or data.get("token"),
                }

        return None

    def discover_github(self) -> Optional[Dict[str, Any]]:
        """Discover GitHub CLI and Copilot credentials."""
        copilot_hosts = os.path.join(self.host_home, ".config", "github-copilot", "hosts.json")
        copilot_data = self._read_json(copilot_hosts)
        if copilot_data:
            for host, info in copilot_data.items():
                if isinstance(info, dict) and info.get("oauth_token"):
                    return {
                        "source_path": copilot_hosts,
                        "provider": "github",
                        "accessToken": info["oauth_token"],
                        "user": info.get("user"),
                    }

        gh_hosts = os.path.join(self.host_home, ".config", "gh", "hosts.yml")
        if os.path.exists(gh_hosts):
            try:
                with open(gh_hosts, "r", encoding="utf-8") as f:
                    content = f.read()
                m_token = re.search(r"oauth_token:\s*([^\s]+)", content)
                m_user = re.search(r"user:\s*([^\s]+)", content)
                if m_token:
                    return {
                        "source_path": gh_hosts,
                        "provider": "github",
                        "accessToken": m_token.group(1),
                        "user": m_user.group(1) if m_user else None,
                    }
            except Exception:
                pass

        return None

    def discover_codex_openai(self) -> Optional[Dict[str, Any]]:
        """Discover OpenAI and Codex credentials."""
        codex_auth = os.path.join(self.host_home, ".codex", "auth.json")
        data = self._read_json(codex_auth)
        if data:
            toks = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            api_key = data.get("OPENAI_API_KEY")
            acc_tok = toks.get("access_token")
            ref_tok = toks.get("refresh_token")
            if api_key or acc_tok:
                return {
                    "source_path": codex_auth,
                    "apiKey": api_key,
                    "accessToken": acc_tok,
                    "refreshToken": ref_tok,
                    "auth_mode": data.get("auth_mode"),
                }

        candidates = [
            os.path.join(self.host_home, ".codex", "config.json"),
            os.path.join(self.host_home, ".openai", "credentials"),
            os.path.join(self.host_home, ".config", "openai", "credentials"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "apiKey": data.get("api_key") or data.get("apiKey") or data.get("token"),
                    "accessToken": data.get("access_token"),
                    "refreshToken": data.get("refresh_token"),
                }
        return None

    def discover_kiro(self) -> Optional[Dict[str, Any]]:
        """Discover AWS Kiro credentials."""
        candidates = [
            os.path.join(self.host_home, ".kiro", "credentials"),
            os.path.join(self.host_home, ".kiro", "settings", "auth.json"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "accessToken": data.get("accessToken") or data.get("token"),
                    "refreshToken": data.get("refreshToken"),
                }
        return None

    def discover_codeium(self) -> Optional[Dict[str, Any]]:
        """Discover Codeium / Windsurf configuration and API keys."""
        candidates = [
            os.path.join(self.host_home, ".codeium", "config.json"),
            os.path.join(self.host_home, ".windsurf", "auth.json"),
        ]
        for p in candidates:
            data = self._read_json(p)
            if data:
                return {
                    "source_path": p,
                    "apiKey": data.get("apiKey") or data.get("token"),
                }
        return None

    def discover_all(self) -> Dict[str, Any]:
        """Scan all supported local providers on host."""
        return {
            "google": self.discover_google(),
            "claude": self.discover_claude(),
            "github": self.discover_github(),
            "codex": self.discover_codex_openai(),
            "kiro": self.discover_kiro(),
            "codeium": self.discover_codeium(),
        }

    def get_credential_for_provider(self, provider: str) -> Optional[Dict[str, Any]]:
        """Find matching local credential for an OmniRoute provider."""
        p_lower = provider.lower()
        if p_lower in ("antigravity", "gemini-cli", "google"):
            return self.discover_google()
        if p_lower in ("claude", "anthropic"):
            return self.discover_claude()
        if p_lower in ("github", "copilot"):
            return self.discover_github()
        if p_lower in ("codex", "openai"):
            return self.discover_codex_openai()
        if p_lower in ("kiro", "aws-kiro"):
            return self.discover_kiro()
        if p_lower in ("codeium", "windsurf"):
            return self.discover_codeium()
        return None

