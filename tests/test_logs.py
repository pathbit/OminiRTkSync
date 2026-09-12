"""Testes do log persistente em arquivo, rotação e expurgo por idade."""

import os
import tempfile
import time
import unittest
import unittest.mock

from omini_rtksync import logs


class TestLogRetention(unittest.TestCase):
    def setUp(self):
        logs.reset_logging()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.log_dir = os.path.join(self.tmp_dir.name, "logs")
        os.makedirs(self.log_dir, exist_ok=True)

    def tearDown(self):
        logs.reset_logging()
        self.tmp_dir.cleanup()

    def _touch(self, name: str, age_days: float):
        path = os.path.join(self.log_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("linha de log\n")
        past = time.time() - (age_days * 86400)
        os.utime(path, (past, past))
        return path

    def test_default_retention_is_thirty_days(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(logs.get_retention_days(), 30)

    def test_retention_is_configurable(self):
        with unittest.mock.patch.dict(os.environ, {"LOG_RETENTION_DAYS": "90"}, clear=True):
            self.assertEqual(logs.get_retention_days(), 90)

    def test_invalid_retention_falls_back_to_default(self):
        with unittest.mock.patch.dict(os.environ, {"LOG_RETENTION_DAYS": "nao-e-numero"}, clear=True):
            self.assertEqual(logs.get_retention_days(), 30)

    def test_retention_has_a_floor_of_one_day(self):
        with unittest.mock.patch.dict(os.environ, {"LOG_RETENTION_DAYS": "0"}, clear=True):
            self.assertEqual(logs.get_retention_days(), 1)

    def test_purge_removes_only_files_older_than_retention(self):
        recent = self._touch(f"{logs.LOG_FILE_NAME}.2026-09-10", age_days=2)
        old = self._touch(f"{logs.LOG_FILE_NAME}.2026-07-01", age_days=45)
        active = self._touch(logs.LOG_FILE_NAME, age_days=99)
        unrelated = self._touch("outro-servico.log.2026-01-01", age_days=99)

        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            removed = logs.purge_expired_logs(self.log_dir)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(recent))
        # O arquivo ativo nunca e apagado, mesmo que a data de modificacao seja antiga.
        self.assertTrue(os.path.exists(active))
        # Arquivos de outros servicos no mesmo diretorio ficam intactos.
        self.assertTrue(os.path.exists(unrelated))

    def test_purge_respects_a_longer_configured_retention(self):
        old = self._touch(f"{logs.LOG_FILE_NAME}.2026-07-01", age_days=45)

        with unittest.mock.patch.dict(os.environ, {"LOG_RETENTION_DAYS": "60"}, clear=True):
            removed = logs.purge_expired_logs(self.log_dir)

        self.assertEqual(removed, 0)
        self.assertTrue(os.path.exists(old))

    def test_purge_on_missing_directory_is_a_noop(self):
        self.assertEqual(logs.purge_expired_logs(os.path.join(self.tmp_dir.name, "nao-existe")), 0)


class TestLogSetup(unittest.TestCase):
    def setUp(self):
        logs.reset_logging()
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        logs.reset_logging()
        self.tmp_dir.cleanup()

    def test_events_are_written_to_the_log_file(self):
        log_dir = os.path.join(self.tmp_dir.name, "logs")
        with unittest.mock.patch.dict(os.environ, {"LOG_DIR": log_dir, "LOG_TO_STDOUT": "0"}, clear=True):
            logger = logs.setup_logging()
            logger.info("[SYNC] token renovado")
            for handler in logger.handlers:
                handler.flush()

            log_file = os.path.join(log_dir, logs.LOG_FILE_NAME)
            self.assertTrue(os.path.exists(log_file))
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()

        self.assertIn("[SYNC] token renovado", content)

    def test_stdout_mirror_can_be_disabled(self):
        log_dir = os.path.join(self.tmp_dir.name, "logs")
        with unittest.mock.patch.dict(os.environ, {"LOG_DIR": log_dir, "LOG_TO_STDOUT": "0"}, clear=True):
            logger = logs.setup_logging()
            stream_handlers = [
                h for h in logger.handlers if type(h).__name__ == "StreamHandler"
            ]
        self.assertEqual(stream_handlers, [])

    def test_stdout_mirror_is_on_by_default(self):
        log_dir = os.path.join(self.tmp_dir.name, "logs")
        with unittest.mock.patch.dict(os.environ, {"LOG_DIR": log_dir}, clear=True):
            logger = logs.setup_logging()
            stream_handlers = [
                h for h in logger.handlers if type(h).__name__ == "StreamHandler"
            ]
        self.assertEqual(len(stream_handlers), 1)

    def test_unwritable_directory_does_not_break_startup(self):
        # Um caminho impossivel de criar nao pode derrubar o sincronizador.
        impossible = "/proc/nao-pode-criar/logs"
        with unittest.mock.patch.dict(os.environ, {"LOG_DIR": impossible, "LOG_TO_STDOUT": "0"}, clear=True):
            logger = logs.setup_logging()
        self.assertIsNotNone(logger)

    def test_log_dir_defaults_next_to_the_database(self):
        db_path = os.path.join(self.tmp_dir.name, "data.sqlite")
        open(db_path, "w").close()
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                logs.resolve_log_dir(db_path), os.path.join(self.tmp_dir.name, "logs")
            )

    def test_log_dir_env_wins_over_the_database_path(self):
        with unittest.mock.patch.dict(os.environ, {"LOG_DIR": "/var/log/custom"}, clear=True):
            self.assertEqual(logs.resolve_log_dir("/app/data/db/data.sqlite"), "/var/log/custom")


if __name__ == "__main__":
    unittest.main()
