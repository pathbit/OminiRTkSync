"""Testes de carregamento de .env e da configuração 100% por variável de ambiente."""

import os
import tempfile
import unittest
from unittest import mock

from omini_rtksync.config import load_dotenv, Settings


class TestConfigEnv(unittest.TestCase):
    def setUp(self):
        self._orig_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_load_dotenv_parses_key_values_and_quotes(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8") as f:
            f.write("# Comentario\n")
            f.write("TEST_ENV_VAR1=valor_um\n")
            f.write('TEST_ENV_VAR2="valor com aspas"\n')
            f.write("TEST_ENV_VAR3='valor com aspas simples'\n")
            f.write("TEST_EXISTING=novo_valor\n")
            temp_path = f.name

        try:
            os.environ["TEST_EXISTING"] = "valor_original"
            load_dotenv(temp_path)

            self.assertEqual(os.environ.get("TEST_ENV_VAR1"), "valor_um")
            self.assertEqual(os.environ.get("TEST_ENV_VAR2"), "valor com aspas")
            self.assertEqual(os.environ.get("TEST_ENV_VAR3"), "valor com aspas simples")
            # Nao deve sobrescrever variaveis ja existentes
            self.assertEqual(os.environ.get("TEST_EXISTING"), "valor_original")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_settings_from_env_loads_custom_env_file(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8") as f:
            f.write("DASHBOARD_USER=custom_admin\n")
            f.write("DASHBOARD_PASSWORD=custom_pass\n")
            f.write("SYNC_INTERVAL=120\n")
            temp_path = f.name

        try:
            os.environ.pop("DASHBOARD_USER", None)
            os.environ.pop("DASHBOARD_PASSWORD", None)
            os.environ.pop("SYNC_INTERVAL", None)

            settings = Settings.from_env(env_file=temp_path)
            self.assertEqual(settings.dashboard_user, "custom_admin")
            self.assertEqual(settings.dashboard_password, "custom_pass")
            self.assertEqual(settings.sync_interval, 120)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestSettingsFromEnv(unittest.TestCase):
    """Cobre o contrato: toda configuração é alcançável sem abrir o dashboard."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "storage.sqlite")
        open(self.db_path, "w").close()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _env(self, **overrides):
        """Ambiente limpo com apenas as variáveis informadas."""
        base = {"DB_PATH": self.db_path, "HOST_HOME": self.tmp_dir.name}
        base.update(overrides)
        return mock.patch.dict(os.environ, base, clear=True)

    @staticmethod
    def _settings():
        # env_file inexistente: isola o teste de qualquer .env presente no diretório.
        return Settings.from_env(env_file="")

    def test_defaults_without_any_variable(self):
        with self._env():
            s = self._settings()
        self.assertEqual(s.sync_interval, 300)
        self.assertEqual(s.cron_interval, 300)
        self.assertTrue(s.cron_enabled)
        self.assertEqual(s.web_port, 9090)
        self.assertTrue(s.enable_web)
        self.assertFalse(s.dashboard_auth_from_env)

    def test_every_knob_is_reachable_from_the_environment(self):
        with self._env(
            OMNIROUTE_URL="http://gateway:20128",
            SYNC_INTERVAL="60",
            REFRESH_MARGIN="120",
            ENABLE_WEB_DASHBOARD="0",
            WEB_HOST="127.0.0.1",
            WEB_PORT="9999",
            CRON_INTERVAL="45",
            CRON_ENABLED="0",
            DASHBOARD_USER="operador",
            DASHBOARD_PASSWORD="segredo-forte",
        ):
            s = self._settings()

        self.assertEqual(s.omniroute_url, "http://gateway:20128")
        self.assertEqual(s.sync_interval, 60)
        self.assertEqual(s.refresh_margin, 120)
        self.assertFalse(s.enable_web)
        self.assertEqual(s.web_host, "127.0.0.1")
        self.assertEqual(s.web_port, 9999)
        self.assertEqual(s.cron_interval, 45)
        self.assertFalse(s.cron_enabled)
        self.assertEqual(s.get_auth_credentials(), ("operador", "segredo-forte"))

    def test_cron_interval_defaults_to_sync_interval(self):
        with self._env(SYNC_INTERVAL="90"):
            s = self._settings()
        self.assertEqual(s.cron_interval, 90)

    def test_env_credentials_override_the_saved_file(self):
        """Sem isto, uma única troca de senha pela tela tornaria o ambiente inerte."""
        with self._env(DASHBOARD_USER="operador", DASHBOARD_PASSWORD="do-ambiente"):
            s = self._settings()
            # Simula um arquivo gravado anteriormente pela tela.
            with open(s.get_auth_file_path(), "w", encoding="utf-8") as f:
                f.write('{"user": "da-tela", "password": "da-tela"}')

            self.assertTrue(s.dashboard_auth_from_env)
            self.assertEqual(s.get_auth_credentials(), ("operador", "do-ambiente"))

    def test_screen_cannot_overwrite_env_credentials(self):
        with self._env(DASHBOARD_PASSWORD="do-ambiente"):
            s = self._settings()
            self.assertFalse(s.update_auth_credentials("novo", "nova-senha"))
            self.assertEqual(s.get_auth_credentials(), ("admin", "do-ambiente"))

    def test_saved_credentials_still_win_when_env_is_absent(self):
        """Sem variáveis definidas, a tela continua sendo a fonte de verdade."""
        with self._env():
            s = self._settings()
            self.assertTrue(s.update_auth_credentials("da-tela", "Senha-Tela1"))
            # A senha agora vive como hash no SQLite, então o que se verifica é
            # a autenticação, não a igualdade do texto.
            self.assertTrue(s.verify_credentials("da-tela", "Senha-Tela1"))
            self.assertFalse(s.verify_credentials("da-tela", "outra"))

    def test_default_password_detection(self):
        with self._env():
            s = self._settings()
            self.assertTrue(s.is_default_password())
        with self._env(DASHBOARD_PASSWORD="outra-coisa"):
            s = self._settings()
            self.assertFalse(s.is_default_password())


if __name__ == "__main__":
    unittest.main()
