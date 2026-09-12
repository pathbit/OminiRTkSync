"""Testes da politica de senha e da credencial gravada em SQLite."""

import os
import tempfile
import unittest

from omini_rtksync.auth import (
    hash_password,
    password_matches,
    read_db_credentials,
    validate_password_strength,
    write_db_credentials,
)
from omini_rtksync.config import Settings
from omini_rtksync.prefs import resolve_prefs_path


class TestPasswordStrength(unittest.TestCase):
    def test_accepts_a_password_meeting_every_rule(self):
        self.assertEqual(validate_password_strength("Sample1!"), [])

    def test_reports_every_broken_rule_at_once(self):
        """Uma regra por tentativa faria o usuario adivinhar a politica aos poucos."""
        problems = validate_password_strength("abc")
        self.assertIn("password.too_short", problems)
        self.assertIn("password.needs_upper", problems)
        self.assertIn("password.needs_digit", problems)
        self.assertIn("password.needs_special", problems)

    def test_six_characters_is_the_floor(self):
        self.assertIn("password.too_short", validate_password_strength("Ab1!c"))
        self.assertEqual(validate_password_strength("Ab1!cd"), [])

    def test_each_class_is_required(self):
        self.assertEqual(validate_password_strength("ABC123!@"), ["password.needs_lower"])
        self.assertEqual(validate_password_strength("abc123!@"), ["password.needs_upper"])
        self.assertEqual(validate_password_strength("Abcdef!@"), ["password.needs_digit"])
        self.assertEqual(validate_password_strength("Abcdef12"), ["password.needs_special"])

    def test_the_factory_password_would_be_refused_today(self):
        self.assertTrue(validate_password_strength("pathbit"))


class TestPasswordHashing(unittest.TestCase):
    def test_hash_is_salted_so_two_hashes_never_match(self):
        self.assertNotEqual(hash_password("Sample1!"), hash_password("Sample1!"))

    def test_the_password_never_appears_in_the_stored_value(self):
        self.assertNotIn("Sample1!", hash_password("Sample1!"))

    def test_matching_and_non_matching(self):
        stored = hash_password("Sample1!")
        self.assertTrue(password_matches(stored, "Sample1!"))
        self.assertFalse(password_matches(stored, "Pathbit1"))
        self.assertFalse(password_matches(stored, ""))

    def test_legacy_plaintext_still_authenticates(self):
        """Quem ja tinha senha no arquivo antigo nao pode ficar trancado do lado de fora."""
        self.assertTrue(password_matches("senha-antiga", "senha-antiga"))
        self.assertFalse(password_matches("senha-antiga", "outra"))

    def test_corrupted_stored_value_denies_access(self):
        self.assertFalse(password_matches("pbkdf2_sha256$quebrado", "qualquer"))


class TestCredentialsInSqlite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "data.sqlite")
        self.settings = Settings(db_path=self.db_path)

    def test_no_password_stored_means_the_banner_shows(self):
        self.assertTrue(self.settings.is_default_password())
        self.assertFalse(self.settings.has_stored_password())

    def test_the_banner_disappears_once_a_password_is_stored(self):
        self.assertTrue(self.settings.update_auth_credentials("admin", "Sample1!"))
        self.assertTrue(self.settings.has_stored_password())
        self.assertFalse(self.settings.is_default_password())

    def test_a_weak_password_is_refused_and_changes_nothing(self):
        self.assertFalse(self.settings.update_auth_credentials("admin", "fraca"))
        self.assertFalse(self.settings.has_stored_password())
        self.assertTrue(self.settings.is_default_password())

    def test_the_stored_password_authenticates_and_the_old_one_stops(self):
        self.settings.update_auth_credentials("admin", "Sample1!")
        self.assertTrue(self.settings.verify_credentials("admin", "Sample1!"))
        self.assertFalse(self.settings.verify_credentials("admin", "pathbit"))

    def test_the_password_is_never_written_in_clear_text(self):
        self.settings.update_auth_credentials("admin", "Sample1!")
        prefs = resolve_prefs_path(os.path.dirname(self.db_path))
        with open(prefs, "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"Sample1!", raw)

    def test_headless_mode_refuses_to_write(self):
        """Com a senha vindo do ambiente, gravar aqui criaria estado que ninguem le."""
        headless = Settings(db_path=self.db_path, dashboard_auth_from_env=True)
        self.assertFalse(headless.update_auth_credentials("admin", "Sample1!"))
        # E o banner nao aparece: quem opera o ambiente ja controla o segredo.
        self.assertFalse(headless.is_default_password())

    def test_credentials_round_trip_through_the_database(self):
        prefs = resolve_prefs_path(self.tmp.name)
        self.assertIsNone(read_db_credentials(prefs))
        self.assertTrue(write_db_credentials(prefs, "operador", "Sample1!"))
        user, stored = read_db_credentials(prefs)
        self.assertEqual(user, "operador")
        self.assertTrue(password_matches(stored, "Sample1!"))


class TestNoFactoryPassword(unittest.TestCase):
    """Uma senha padrao estatica e, por definicao, uma credencial publica."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "data.sqlite")

    def test_the_settings_default_carries_no_password(self):
        self.assertEqual(Settings(db_path=self.db_path).dashboard_password, "")

    def test_no_guessable_password_opens_the_panel(self):
        s = Settings(db_path=self.db_path)
        s.ensure_recovery_hash()
        for guess in ("pathbit", "admin", "", "password", "123456", "9rtksync"):
            self.assertFalse(s.verify_credentials("admin", guess), guess)

    def test_the_recovery_credential_is_the_only_way_in_before_a_password_is_set(self):
        s = Settings(db_path=self.db_path)
        recovery, _ = s.ensure_recovery_hash()
        self.assertTrue(s.verify_credentials("admin", recovery))

    def test_the_recovery_credential_is_long_enough_to_resist_guessing(self):
        s = Settings(db_path=self.db_path)
        recovery, _ = s.ensure_recovery_hash()
        self.assertGreaterEqual(len(recovery), 32)


if __name__ == "__main__":
    unittest.main()
