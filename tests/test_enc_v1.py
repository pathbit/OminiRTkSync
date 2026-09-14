"""enc:v1 — interop de cifra com o armazenamento do OmniRoute.

O vetor INTEROP foi gerado com o scryptSync + AES-256-GCM do Node.js (os
mesmos parâmetros do bundle do OmniRoute) contra a chave de teste — a prova
de que o Python lê exatamente o que o gateway escreve.
"""

from __future__ import annotations

import os
import unittest

from omini_rtksync.enc_v1 import decrypt, decrypt_if_needed, encrypt, is_encrypted

CHAVE = "teste-chave-de-campo-32bytes"
INTEROP_NODE = "enc:v1:b321265c13c4321569e5def33f2e5e46:0101a8fc1b2ae325092ffed20fdfcd0920abb6e9:573d1423166eee97e8eab80aa4851b5a"
SEGREDO = "sk-teste-segredo-123"


class EncV1InteropTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["STORAGE_ENCRYPTION_KEY"] = CHAVE

    def tearDown(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)

    def test_decifra_valor_produzido_pelo_node(self) -> None:
        self.assertEqual(decrypt(INTEROP_NODE), SEGREDO)

    def test_round_trip_cifra_e_decifra(self) -> None:
        cifrado = encrypt(SEGREDO)
        self.assertTrue(is_encrypted(cifrado))
        self.assertEqual(decrypt(cifrado), SEGREDO)

    def test_cifras_sao_unicas_por_iv_aleatorio(self) -> None:
        self.assertNotEqual(encrypt("mesmo"), encrypt("mesmo"))

    def test_sem_chave_passthrough_e_none(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)
        self.assertIsNone(decrypt(INTEROP_NODE))
        self.assertEqual(encrypt("claro"), "claro")

    def test_valor_em_claro_passa_intacto(self) -> None:
        self.assertEqual(decrypt("texto-claro"), "texto-claro")
        self.assertEqual(decrypt_if_needed("texto-claro"), "texto-claro")

    def test_malformado_nao_levanta(self) -> None:
        self.assertIsNone(decrypt("enc:v1:aa:bb"))
        self.assertEqual(decrypt_if_needed("enc:v1:aa:bb"), "enc:v1:aa:bb")

    def test_tag_adulterada_recusa(self) -> None:
        adulterado = INTEROP_NODE[:-1] + ("0" if INTEROP_NODE[-1] != "0" else "1")
        self.assertIsNone(decrypt(adulterado))


if __name__ == "__main__":
    unittest.main()
