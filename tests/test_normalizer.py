"""Testes unitários de normalização de datas para OmniRoute."""

import unittest
from omini_rtksync.normalizer import parse_expiry_to_ms


class TestNormalizer(unittest.TestCase):
    def test_parse_expiry_iso_string(self):
        iso = "2026-09-12T12:00:00Z"
        ms = parse_expiry_to_ms(iso)
        self.assertIsNotNone(ms)
        self.assertGreater(ms, 1700000000000)

    def test_parse_expiry_numeric_string(self):
        ms_str = "1789000000000"
        ms = parse_expiry_to_ms(ms_str)
        self.assertEqual(ms, 1789000000000)

    def test_parse_expiry_seconds_int(self):
        sec = 1789000000
        ms = parse_expiry_to_ms(sec)
        self.assertEqual(ms, 1789000000000)

    def test_parse_expiry_invalid(self):
        self.assertIsNone(parse_expiry_to_ms(None))
        self.assertIsNone(parse_expiry_to_ms(""))
        self.assertIsNone(parse_expiry_to_ms("invalido"))
        self.assertIsNone(parse_expiry_to_ms(-10))


if __name__ == "__main__":
    unittest.main()
