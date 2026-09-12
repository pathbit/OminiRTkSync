"""O armazenamento do painel precisa nascer no startup, dentro do volume de dados.

Antes disto, quando o diretorio do DB_PATH ainda nao existia -- que e o estado
do primeiro boot, porque quem cria esse diretorio e o gateway -- a resolucao do
caminho caia silenciosamente para $HOME. O banco de preferencias, onde mora a
senha do painel, era gravado fora do volume: a senha sumia ao recriar o
container e o painel voltava a exigir a credencial de recuperacao.
"""

import os
import tempfile
import unittest
from unittest import mock

from omini_rtksync.auth import read_db_credentials, write_db_credentials
from omini_rtksync.config import Settings


class TestArmazenamentoNoStartup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Caminho que ainda NAO existe, de proposito.
        self.data_dir = os.path.join(self.tmp.name, "app", "data", "db")
        self.db_path = os.path.join(self.data_dir, "data.sqlite")
        self.assertFalse(os.path.exists(self.data_dir))

    def settings(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATA_DIR", None)
            return Settings(db_path=self.db_path, validate_credentials=False)

    def test_the_data_directory_is_created_not_bypassed(self):
        caminho = self.settings().get_auth_file_path()
        self.assertTrue(os.path.isdir(self.data_dir), "o diretorio de dados tem de ser criado")
        self.assertEqual(os.path.dirname(caminho), self.data_dir)

    def test_the_preferences_database_lives_beside_the_gateway_database(self):
        prefs = self.settings().get_prefs_path()
        self.assertEqual(os.path.dirname(prefs), self.data_dir)
        self.assertNotEqual(
            os.path.dirname(prefs),
            os.path.expanduser("~"),
            "a senha do painel nao pode ser gravada fora do volume de dados",
        )

    def test_the_password_survives_a_second_resolution(self):
        """Simula recriar o container: resolver de novo tem de achar a mesma senha."""
        prefs = self.settings().get_prefs_path()
        self.assertTrue(write_db_credentials(prefs, "admin", "Sample1!"))

        # Segunda resolucao, com o diretorio agora existindo.
        prefs_de_novo = self.settings().get_prefs_path()
        self.assertEqual(prefs, prefs_de_novo)
        self.assertIsNotNone(read_db_credentials(prefs_de_novo))

    def test_a_read_only_path_still_falls_back_instead_of_crashing(self):
        settings = Settings(db_path="/proc/nao-pode-criar/data.sqlite", validate_credentials=False)
        caminho = settings.get_auth_file_path()
        # Nao levanta; cai para o home, que e o unico lugar gravavel que resta.
        self.assertTrue(caminho.endswith(".dashboard_auth.json"))


if __name__ == "__main__":
    unittest.main()
