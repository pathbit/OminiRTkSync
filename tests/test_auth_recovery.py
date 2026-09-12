"""Testes da regra de autenticação do dashboard, incluindo a credencial de recuperação."""

import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from omini_rtksync.auth import (
    constant_time_equals,
    derive_recovery_hash,
    ensure_recovery_hash,
    read_stored_credentials,
    resolve_recovery_hash,
    verify_credentials,
)
from omini_rtksync.config import Settings

FACTORY = {"factory_user": "admin", "factory_password": "pathbit"}


class TestVerifyCredentials(unittest.TestCase):
    """A ordem de validação: salvas -> padrão de fábrica (se nada salvo) -> recuperação."""

    def test_factory_credentials_work_while_nothing_is_stored(self):
        self.assertTrue(verify_credentials("admin", "pathbit", stored=None, **FACTORY))

    def test_wrong_factory_password_is_rejected(self):
        self.assertFalse(verify_credentials("admin", "errada", stored=None, **FACTORY))

    def test_stored_credentials_replace_the_factory_ones(self):
        stored = ("operador", "senha-nova")
        self.assertTrue(verify_credentials("operador", "senha-nova", stored=stored, **FACTORY))
        # Depois da troca, a senha de fábrica nao vale mais.
        self.assertFalse(verify_credentials("admin", "pathbit", stored=stored, **FACTORY))

    def test_recovery_hash_always_works_for_admin(self):
        stored = ("operador", "senha-esquecida")
        recovery = derive_recovery_hash("segredo")
        self.assertTrue(
            verify_credentials("admin", recovery, stored=stored, recovery_hash=recovery, **FACTORY)
        )

    def test_recovery_hash_works_even_before_any_password_change(self):
        recovery = derive_recovery_hash("segredo")
        self.assertTrue(
            verify_credentials("admin", recovery, stored=None, recovery_hash=recovery, **FACTORY)
        )

    def test_recovery_hash_only_works_for_the_admin_user(self):
        recovery = derive_recovery_hash("segredo")
        self.assertFalse(
            verify_credentials(
                "operador", recovery, stored=None, recovery_hash=recovery, **FACTORY
            )
        )

    def test_everything_else_is_invalid(self):
        stored = ("operador", "senha-nova")
        recovery = derive_recovery_hash("segredo")
        for user, password in [
            ("admin", "pathbit"),
            ("admin", "chute"),
            ("operador", "chute"),
            ("outro", "senha-nova"),
            ("", ""),
            ("admin", ""),
            ("", "senha-nova"),
        ]:
            with self.subTest(user=user, password=password):
                self.assertFalse(
                    verify_credentials(
                        user, password, stored=stored, recovery_hash=recovery, **FACTORY
                    )
                )

    def test_empty_recovery_hash_never_grants_access(self):
        # Sem hash configurado, uma senha vazia nao pode virar chave mestra.
        self.assertFalse(
            verify_credentials("admin", "", stored=None, recovery_hash="", **FACTORY)
        )

    def test_constant_time_equals_handles_none(self):
        self.assertTrue(constant_time_equals("a", "a"))
        self.assertFalse(constant_time_equals("a", None))
        self.assertTrue(constant_time_equals(None, None))


class TestRecoveryHashStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.recovery_file = os.path.join(self.tmp_dir.name, ".dashboard_recovery")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_env_hash_wins(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_RECOVERY_HASH": "do-ambiente"}, clear=True):
            self.assertEqual(resolve_recovery_hash(self.recovery_file), "do-ambiente")

    def test_generated_hash_is_persisted_and_reused(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            first, generated = ensure_recovery_hash(self.recovery_file)
            self.assertTrue(generated)
            self.assertTrue(first)

            second, generated_again = ensure_recovery_hash(self.recovery_file)
            self.assertFalse(generated_again)
            self.assertEqual(second, first)

    def test_generated_hash_file_is_owner_only(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            ensure_recovery_hash(self.recovery_file)
        mode = stat.S_IMODE(os.stat(self.recovery_file).st_mode)
        self.assertEqual(mode, 0o600)

    def test_unwritable_path_still_returns_a_hash(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            value, generated = ensure_recovery_hash("/proc/nao-pode/recovery")
        self.assertTrue(value)
        self.assertTrue(generated)

    def test_read_stored_credentials_handles_missing_and_corrupt_files(self):
        self.assertIsNone(read_stored_credentials(""))
        self.assertIsNone(read_stored_credentials(os.path.join(self.tmp_dir.name, "nao-existe")))

        corrupt = os.path.join(self.tmp_dir.name, "corrupto.json")
        with open(corrupt, "w", encoding="utf-8") as f:
            f.write("{nao e json")
        self.assertIsNone(read_stored_credentials(corrupt))

        incomplete = os.path.join(self.tmp_dir.name, "incompleto.json")
        with open(incomplete, "w", encoding="utf-8") as f:
            json.dump({"user": "só-usuario"}, f)
        self.assertIsNone(read_stored_credentials(incomplete))


class TestSettingsAuthIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "data.sqlite")
        open(self.db_path, "w").close()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _settings(self, **env):
        base = {"DB_PATH": self.db_path, "DATA_DIR": self.tmp_dir.name}
        base.update(env)
        with mock.patch.dict(os.environ, base, clear=True):
            return Settings.from_env(env_file=""), dict(base)

    def test_full_lifecycle_from_factory_to_change_to_recovery(self):
        settings, base = self._settings()
        with mock.patch.dict(os.environ, base, clear=True):
            # 1. Primeiro acesso: credenciais de fábrica.
            self.assertTrue(settings.verify_credentials("admin", "pathbit"))

            # 2. Operador troca a senha pela tela.
            self.assertTrue(settings.update_auth_credentials("operador", "minha-senha"))
            self.assertTrue(settings.verify_credentials("operador", "minha-senha"))
            self.assertFalse(settings.verify_credentials("admin", "pathbit"))

            # 3. Esqueceu a senha: entra com admin + hash de recuperação.
            recovery, _ = settings.ensure_recovery_hash()
            self.assertTrue(settings.verify_credentials("admin", recovery))

            # 4. Qualquer outra combinação segue inválida.
            self.assertFalse(settings.verify_credentials("admin", "chute"))
            self.assertFalse(settings.verify_credentials("operador", recovery))

    def test_recovery_hash_can_be_pinned_by_environment(self):
        settings, base = self._settings(DASHBOARD_RECOVERY_HASH="hash-fixo-do-container")
        with mock.patch.dict(os.environ, base, clear=True):
            settings.update_auth_credentials("operador", "minha-senha")
            self.assertTrue(settings.verify_credentials("admin", "hash-fixo-do-container"))

    def test_env_authoritative_mode_ignores_the_saved_file(self):
        settings, base = self._settings(DASHBOARD_PASSWORD="do-ambiente")
        with mock.patch.dict(os.environ, base, clear=True):
            # A tela nao consegue sobrescrever o ambiente.
            self.assertFalse(settings.update_auth_credentials("da-tela", "da-tela"))
            self.assertTrue(settings.verify_credentials("admin", "do-ambiente"))
            self.assertFalse(settings.verify_credentials("da-tela", "da-tela"))


if __name__ == "__main__":
    unittest.main()
