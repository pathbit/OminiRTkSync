"""Gateway recém-subido não é falha.

O gateway cria o banco quando é usado pela primeira vez. Entre subir a stack e
cadastrar a primeira conexão, o arquivo simplesmente não existe — e isso é o
estado normal de quem acabou de instalar, não um erro.

Relatar como falha pintava o painel de vermelho no primeiro minuto de uso, com
um `ERRO: db_not_found` no histórico do agendador. O efeito colateral é pior
que o susto: ensina o operador a ignorar o indicador de erro, que é justamente
o que precisa continuar significando alguma coisa quando algo quebrar de
verdade.
"""

import os
import tempfile
import unittest

from omini_rtksync.cli import OmniSyncEngine
from omini_rtksync.config import Settings


class TestBancoAusenteNaoEFalha(unittest.TestCase):
    def ciclo_sem_banco(self):
        d = tempfile.mkdtemp()
        caminho = os.path.join(d, "nao-criado-ainda.sqlite")
        self.assertFalse(os.path.exists(caminho))
        motor = OmniSyncEngine(Settings(db_path=caminho, enable_web=False, validate_credentials=False))
        return motor.sync_all()

    def test_a_brand_new_gateway_is_not_reported_as_a_failure(self):
        resumo = self.ciclo_sem_banco()
        self.assertTrue(
            resumo.get("success"),
            "banco ainda inexistente e o estado normal de uma stack recem subida",
        )

    def test_it_says_it_is_waiting_rather_than_staying_silent(self):
        # Silencio seria pior que o erro: o operador precisa saber POR QUE o
        # painel esta vazio.
        self.assertTrue(self.ciclo_sem_banco().get("waiting_for_gateway"))

    def test_the_summary_still_has_the_shape_the_panel_expects(self):
        # O painel le estes campos sem verificar existencia; faltar qualquer um
        # trocaria o aviso amigavel por um KeyError na renderizacao.
        resumo = self.ciclo_sem_banco()
        for campo in ("total_connections", "refreshed", "details", "timestamp"):
            self.assertIn(campo, resumo)


if __name__ == "__main__":
    unittest.main()
