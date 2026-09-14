"""O cabeçalho Server não pode anunciar a pilha que serve o painel.

`BaseHTTPRequestHandler` responde, por padrão, `Server: BaseHTTP/0.6
Python/3.14.7` — a versão exata do interpretador, na primeira linha de TODA
resposta, inclusive no 401 que sai antes de qualquer autenticação. Ela viajava
logo acima da CSP, do `X-Frame-Options: DENY` e do `nosniff` que o resto do
cabeçalho instala: a mesma resposta que fecha as portas dizia qual é a
fechadura.

Versão exata é o que um scanner precisa para escolher o exploit certo, e nada
no produto depende de publicá-la.
"""

import pathlib
import sys
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from omini_rtksync.web import DashboardHandler  # noqa: E402

HANDLER = RAIZ / "src" / "omini_rtksync" / "web.py"

PROIBIDO = ("python", "basehttp", "simplehttp", "wsgi")


class CabecalhoServerNaoDenuncia(unittest.TestCase):
    def test_o_handler_declara_nome_proprio_e_versao_vazia(self):
        self.assertTrue(
            DashboardHandler.server_version,
            "sem server_version o padrão do BaseHTTP anuncia a versão do Python",
        )
        self.assertEqual(
            DashboardHandler.sys_version,
            "",
            "sys_version tem de ser vazio: é ele que carrega 'Python/3.x.y'",
        )

    def test_version_string_devolve_so_o_nome(self):
        """Sem sobrescrever, o valor sai com um espaço sobrando no fim."""
        fonte = HANDLER.read_text(encoding="utf-8")
        self.assertIn(
            "def version_string",
            fonte,
            "BaseHTTPRequestHandler concatena server_version + ' ' + sys_version",
        )

    def test_o_nome_anunciado_nao_cita_a_pilha(self):
        anunciado = DashboardHandler.server_version
        for proibido in PROIBIDO:
            self.assertNotIn(
                proibido,
                anunciado.lower(),
                f"o nome anunciado ({anunciado!r}) entrega a pilha",
            )


if __name__ == "__main__":
    unittest.main()
