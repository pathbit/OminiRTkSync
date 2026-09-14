"""Os SEIS cartoes do painel, sempre os mesmos e sempre na mesma ordem.

A regra do dono e uma so: "as telas tem cards diferentes entre Litellmrtk,
ominirtk e etc, tem que ter todos os cards iguais". O cartao EXISTE nos tres,
sempre; o que muda e o conteudo. Quando este gateway nao tem aquele conceito, o
cartao aparece com o estado vazio dizendo por que esta vazio AQUI -- e nao some
da tela.

Esta guarda existe porque a alternativa e alguem abrir as tres telas lado a lado
e reparar na falta. Foi assim que "Chaves virtuais" e "Modelos cadastrados"
ficaram so no LiteLlmRTKSync por tempo demais.
"""

import os
import sqlite3
import tempfile
import time
import unittest

from omini_rtksync.gateway import get_all_api_keys, get_all_registered_models
from omini_rtksync.models import (
    ConnectionRecord,
    RegisteredModelRecord,
    VirtualKeyRecord,
)
from omini_rtksync import render

# A ordem e contrato, nao gosto: e a mesma nos tres paineis.
ORDEM_DOS_CARTOES = [
    "gateway.title",
    "cron.title",
    "connections.title",
    "keys.title",
    "models.title",
    "combos.title",
]


def pagina(**overrides):
    base = dict(
        connections=[],
        combos=[],
        cron={"active": True, "intervalSeconds": 300, "totalRenewals": 0},
        gateway={"url": "http://gw:20128", "online": True, "statusCode": 200,
                 "latencyMs": 3, "dbOk": True, "dbConnections": 0, "dbCombos": 0},
        db_path="/app/data/storage.sqlite",
        router_url="http://gw:20128",
        current_user="admin",
        is_default_password=False,
        refresh_margin=900,
    )
    base.update(overrides)
    return render.render_dashboard(**base)


class OsSeisCartoesEstaoNaTela(unittest.TestCase):
    def test_os_seis_titulos_aparecem_na_ordem_do_contrato(self):
        for lang in ("en", "pt", "es"):
            html = pagina(lang=lang)
            posicoes = []
            for chave in ORDEM_DOS_CARTOES:
                titulo = render.translate(chave, lang)
                self.assertIn(titulo, html, f"cartao ausente em {lang}: {chave}")
                posicoes.append(html.index(titulo))
            self.assertEqual(
                posicoes,
                sorted(posicoes),
                f"os cartoes sairam fora de ordem em {lang}: {ORDEM_DOS_CARTOES}",
            )

    def test_cartao_sem_dado_mostra_o_estado_vazio_e_nao_some(self):
        """Estado vazio honesto e melhor que assimetria: o cartao continua la."""
        html = pagina(lang="pt")
        for chave in ("connections.empty", "keys.empty", "models.empty", "combos.empty"):
            self.assertIn(render.translate(chave, "pt"), html)
        # Os quatro usam o MESMO bloco de estado vazio da familia.
        self.assertEqual(html.count("bi-inbox fs-1"), 4)


class ChavesVirtuaisNaTela(unittest.TestCase):
    def _chave(self, **campos):
        dados = {
            "id": "72ad7e37-27fd-40cb-9d94-cb4f52471e99",
            "name": "litellm-bridge",
            "createdAt": "2026-09-13T20:21:08.112Z",
            "expiresAt": None,
            "revokedAt": None,
            "lastUsedAt": "2026-09-13T21:13:02.897Z",
            "isActive": True,
            "isBanned": False,
            "modelAccessMode": "all",
            "allowedModels": [],
            "scopes": ["self:usage"],
        }
        dados.update(campos)
        return VirtualKeyRecord.from_row(dados)

    def test_a_linha_usa_as_sete_colunas_da_familia(self):
        html = render.render_keys_table([self._chave()], "pt")
        self.assertIn("litellm-bridge", html)
        self.assertIn("Chave virtual", html)
        self.assertIn("detalhe-chave-0", html)
        # As mesmas sete colunas dos irmaos, na mesma casca.
        for classe in ("c-provedor", "c-nome", "c-tipo", "c-status",
                       "c-validade", "c-renovacao", "c-detalhe"):
            self.assertIn(classe, html)

    def test_chave_sem_nome_e_identificada_pelo_id_e_nunca_pelo_token(self):
        html = render.render_keys_table([self._chave(name="")], "pt")
        self.assertIn("72ad7e37-27fd-40cb-9d94-cb4f52471e99", html)

    def test_os_tres_caminhos_de_recusa_dao_o_mesmo_estado_na_celula(self):
        """Revogada, banida e desativada: o gateway recusa as tres."""
        for campo, valor in (("revokedAt", "2026-09-13T22:00:00Z"),
                             ("isBanned", True),
                             ("isActive", False)):
            self.assertEqual(self._chave(**{campo: valor}).health_status, "invalid")
        self.assertEqual(self._chave().health_status, "no_expiration")

    def test_qual_foi_o_caminho_da_recusa_aparece_no_modal(self):
        html = render.render_keys_table([self._chave(isBanned=True)], "pt")
        self.assertIn("Banida", html)

    def test_validade_e_derivada_de_expires_at(self):
        futuro = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 7200))
        passado = "2020-01-01T00:00:00Z"
        self.assertEqual(self._chave(expiresAt=futuro).health_status, "active")
        self.assertEqual(self._chave(expiresAt=passado).health_status, "expired")


class ModelosCadastradosNaTela(unittest.TestCase):
    def _modelo(self, conexao=None, **campos):
        dados = {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "provider": "gemini",
            "connectionId": "conn-1",
            "source": "imported",
            "description": "Stable release of Gemini 2.5 Pro",
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
            "supportedEndpoints": ["chat"],
        }
        dados.update(campos)
        return RegisteredModelRecord.from_row(dados, conexao)

    def test_a_linha_usa_as_sete_colunas_da_familia(self):
        html = render.render_models_table([self._modelo()], "pt")
        self.assertIn("gemini-2.5-pro", html)
        self.assertIn("Modelo sincronizado", html)
        self.assertIn("detalhe-modelo-0", html)
        for classe in ("c-provedor", "c-nome", "c-tipo", "c-status",
                       "c-validade", "c-renovacao", "c-detalhe"):
            self.assertIn(classe, html)

    def test_o_status_e_herdado_da_conexao_que_serve_o_modelo(self):
        conexao = ConnectionRecord(
            id="conn-1", provider="gemini", name="Google AI Studio PathBit",
            data={"apiKey": "k", "credentialState": "invalid"},
        )
        modelo = self._modelo(conexao=conexao)
        self.assertEqual(modelo.health_status, "invalid")
        html = render.render_models_table([modelo], "pt")
        self.assertIn("Google AI Studio PathBit", html)
        self.assertIn("vêm da conexão", html)

    def test_modelo_orfao_nao_alega_saude_que_ninguem_mediu(self):
        self.assertEqual(self._modelo().health_status, "not_checked")

    def test_catalogo_grande_e_truncado_com_o_total_declarado(self):
        """Truncar em silencio mentiria sobre o tamanho do catalogo."""
        muitos = [self._modelo(id=f"m-{i}") for i in range(render.MAX_LINHAS_DE_MODELO + 25)]
        html = render.render_models_table(muitos, "pt")
        self.assertIn(f"detalhe-modelo-{render.MAX_LINHAS_DE_MODELO - 1}", html)
        self.assertNotIn(f"detalhe-modelo-{render.MAX_LINHAS_DE_MODELO}", html)
        self.assertIn(str(len(muitos)), html)


class ONadaDeSegredoNaTela(unittest.TestCase):
    """O material da chave virtual nao sai do banco -- nem por engano."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "storage.sqlite")
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
                key_prefix TEXT, key_hash TEXT, created_at TEXT NOT NULL,
                expires_at TEXT, revoked_at TEXT, last_used_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1, is_banned INTEGER NOT NULL DEFAULT 0,
                model_access_mode TEXT NOT NULL DEFAULT 'all',
                allowed_models TEXT DEFAULT '[]', scopes TEXT
            )
        """)
        conn.execute(
            "INSERT INTO api_keys (id, name, key, key_prefix, key_hash, created_at, scopes) "
            "VALUES ('k1', 'ponte', 'sk-segredo-inteiro-nao-pode-vazar', 'sk-segred', "
            "'hash-tambem-nao', '2026-09-13T20:21:08.112Z', '[\"self:usage\"]')"
        )
        conn.execute("""
            CREATE TABLE key_value (
                namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            )
        """)
        conn.execute(
            "INSERT INTO key_value VALUES ('syncedAvailableModels', 'gemini:conn-1', "
            '\'[{"id":"gemini-2.5-pro","name":"Gemini 2.5 Pro","source":"imported"}]\')'
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_a_leitura_nao_traz_o_token_nem_o_prefixo_nem_o_hash(self):
        chaves = get_all_api_keys(self.db_path)
        self.assertEqual(len(chaves), 1)
        bruto = repr(chaves)
        for segredo in ("sk-segredo-inteiro-nao-pode-vazar", "sk-segred", "hash-tambem-nao"):
            self.assertNotIn(segredo, bruto)
        self.assertEqual(chaves[0]["name"], "ponte")

    def test_a_pagina_renderizada_nao_contem_o_token(self):
        chaves = [VirtualKeyRecord.from_row(k) for k in get_all_api_keys(self.db_path)]
        html = pagina(keys=chaves, lang="pt")
        for segredo in ("sk-segredo-inteiro-nao-pode-vazar", "sk-segred", "hash-tambem-nao"):
            self.assertNotIn(segredo, html)
        self.assertIn("ponte", html)

    def test_o_catalogo_sai_do_key_value_com_provedor_e_conexao(self):
        modelos = get_all_registered_models(self.db_path)
        self.assertEqual(len(modelos), 1)
        self.assertEqual(modelos[0]["id"], "gemini-2.5-pro")
        self.assertEqual(modelos[0]["provider"], "gemini")
        self.assertEqual(modelos[0]["connectionId"], "conn-1")


class SchemaAntigoNaoDerrubaOPainel(unittest.TestCase):
    """Instalacao sem as tabelas novas mostra o cartao vazio, nao um erro."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "storage.sqlite")
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sem_api_keys_e_sem_key_value_devolve_lista_vazia(self):
        self.assertEqual(get_all_api_keys(self.db_path), [])
        self.assertEqual(get_all_registered_models(self.db_path), [])


if __name__ == "__main__":
    unittest.main()
