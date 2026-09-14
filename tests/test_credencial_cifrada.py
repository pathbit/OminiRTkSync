"""Credencial que não conseguimos ler não é credencial inválida.

O gateway pode guardar a credencial cifrada em repouso (`enc:v1:<iv>:<cifra>:<tag>`).
Lendo o campo cru do banco, o sincronizador mandava o texto cifrado para o
provedor, levava a recusa esperada e concluía "inválida" -- gravando isso de
volta no banco do gateway, que passava a exibir em vermelho uma conta que
ninguém chegou a testar. Não saber ler é um estado diferente de saber que está
ruim, e só um dos dois justifica mandar o usuário reautenticar.
"""

import unittest

from omini_rtksync.credential_check import (
    STATE_INVALID,
    STATE_UNSUPPORTED,
    check_connection,
    looks_encrypted,
)

CIFRADO = "enc:v1:00112233445566778899aabbccddeeff:00:00112233445566778899aabbccddeeff"


class ConexaoFalsa:
    def __init__(self, **campos):
        self.provider = "gemini"
        self.is_local = False
        self.is_oauth = False
        self.has_api_key = False
        self.access_token = None
        self.api_key = None
        self.base_url = None
        for nome, valor in campos.items():
            setattr(self, nome, valor)


class CredencialCifrada(unittest.TestCase):
    def test_reconhece_o_texto_cifrado(self):
        self.assertTrue(looks_encrypted(CIFRADO))
        self.assertFalse(looks_encrypted("gsk_uma_chave_em_claro"))
        self.assertFalse(looks_encrypted(None))

    def test_token_cifrado_nao_vira_invalido(self):
        r = check_connection(ConexaoFalsa(is_oauth=True, access_token=CIFRADO))
        self.assertEqual(r.state, STATE_UNSUPPORTED)
        self.assertNotEqual(r.state, STATE_INVALID)
        self.assertIn("encrypted at rest", r.detail)

    def test_chave_de_api_cifrada_nao_vira_invalida(self):
        r = check_connection(ConexaoFalsa(has_api_key=True, api_key=CIFRADO))
        self.assertEqual(r.state, STATE_UNSUPPORTED)
        self.assertIn("encrypted at rest", r.detail)

    def test_nao_faz_requisicao_nenhuma_com_valor_cifrado(self):
        """A sonda não pode sequer sair: mandar o cifrado é o que sujava o painel."""
        chamou = []

        def abridor(*a, **k):
            chamou.append(a)
            raise AssertionError("não deveria ter feito requisição")

        check_connection(ConexaoFalsa(is_oauth=True, access_token=CIFRADO), opener=abridor)
        check_connection(ConexaoFalsa(has_api_key=True, api_key=CIFRADO), opener=abridor)
        self.assertEqual(chamou, [])

    def test_credencial_em_claro_continua_sendo_sondada(self):
        """A guarda não pode calar a validação de quem está legível."""
        saiu = []

        def abridor(req, timeout=None):
            saiu.append(getattr(req, "full_url", str(req)))
            raise RuntimeError("corta aqui: o que importa é que a sonda saiu")

        check_connection(ConexaoFalsa(has_api_key=True, api_key="gsk_em_claro"), opener=abridor)
        self.assertEqual(len(saiu), 1)


if __name__ == "__main__":
    unittest.main()


class SaudeNaoSeInventa(unittest.TestCase):
    """O espelho do mesmo defeito: afirmar saúde sem ter medido.

    A leitura preenchia `test_status` vazio com "active". Como o fim do ciclo
    grava de volta o que leu, o sincronizador passava a afirmar ao gateway uma
    saúde que nunca mediu -- e uma chave ilegível aparecia verde na tela.
    """

    def _banco_com_uma_conexao_sem_estado(self):
        import sqlite3
        import tempfile
        import os

        caminho = os.path.join(tempfile.mkdtemp(), "storage.sqlite")
        con = sqlite3.connect(caminho)
        con.execute(
            "CREATE TABLE provider_connections ("
            "id TEXT PRIMARY KEY, provider TEXT, name TEXT, auth_type TEXT,"
            " api_key TEXT, access_token TEXT, refresh_token TEXT,"
            " expires_at TEXT, test_status TEXT, last_error TEXT,"
            " is_active INTEGER, provider_specific_data TEXT)"
        )
        con.execute(
            "INSERT INTO provider_connections VALUES"
            " ('1','groq','Groq','apikey',?,NULL,NULL,NULL,NULL,NULL,1,'{}')",
            (CIFRADO,),
        )
        con.commit()
        con.close()
        return caminho

    def test_coluna_vazia_nao_vira_active(self):
        from omini_rtksync.gateway import get_all_connections

        conexoes = get_all_connections(self._banco_com_uma_conexao_sem_estado())
        self.assertEqual(len(conexoes), 1)
        self.assertNotEqual(
            conexoes[0].get("testStatus"),
            "active",
            "coluna vazia significa 'ninguém testou', não 'está saudável'",
        )
