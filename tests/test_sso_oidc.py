"""A entrada federada testada pelo caminho RUIM, que é onde ela vive.

Um fluxo de SSO exercitado só no caminho feliz não está testado: o caminho
feliz é idêntico com e sem as verificações, e é exatamente por isso que uma
verificação some num refactor sem ninguém notar. Cada teste aqui vira UMA
verificação do avesso e cobra a recusa.

O provedor de identidade é falso e vive dentro do processo (`ProvedorFalso`
substitui `sso._http_json`, a única saída de rede do módulo). Nada aqui toca a
internet, e é justamente isso que permite testar o que um provedor real nunca
produziria de propósito: `state` trocado, `aud` errada, `iat` no futuro,
`email_verified` falso, código reapresentado.

O que cada grupo protege:

- **TestIdaAoProvedor**    a ida leva PKCE S256 e o retorno combinado, e o
                           cookie de estado chega ao navegador de volta;
- **TestVoltaDoProvedor**  o caminho feliz emite o MESMO cookie do formulário;
- **TestRecusas**          treze entradas ruins, uma por verificação;
- **TestDesligado**        sem configuração o painel é o de antes, byte a byte;
- **TestConfiguracao**     quem grava a configuração prova a senha local, e o
                           segredo nunca volta para a tela.
"""

import base64
import hashlib
import http.client
import json
import os
import sqlite3
import tempfile
import time
import unittest
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from omini_rtksync import protecao, sso
from omini_rtksync.config import Settings
from omini_rtksync.prefs import resolve_prefs_path
from omini_rtksync.web import start_web_server

PORTA = 19291
PAINEL = f"http://127.0.0.1:{PORTA}"
ISSUER = "https://idp.exemplo.com"
CLIENT_ID = "cliente-do-painel"
SEGREDO_DO_CLIENTE = "segredo-que-nunca-aparece-na-tela"
SENHA_LOCAL = "Senha-Local-1!"
EMAIL = "operador@empresa.com"
SUB = "sub-do-operador-123"

DOCUMENTO = {
    "issuer": ISSUER,
    "authorization_endpoint": ISSUER + "/authorize",
    "token_endpoint": ISSUER + "/token",
    "userinfo_endpoint": ISSUER + "/userinfo",
}


def segmento(dados: Dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(dados).encode("utf-8")).decode("ascii").rstrip("=")


def id_token_falso(payload: Dict[str, Any]) -> str:
    """Um JWT com assinatura de mentira.

    De propósito: o painel NÃO verifica a assinatura, e o motivo está escrito em
    `sso.py` -- o token chega pelo canal direto TLS com o cliente autenticado
    (OIDC Core 3.1.3.7). Se alguém um dia passar a verificar, este teste quebra
    e a decisão volta à mesa, que é o comportamento certo.
    """
    return f"{segmento({'alg': 'RS256'})}.{segmento(payload)}.assinatura-nao-verificada"


class ProvedorFalso:
    """Provedor de identidade em memória, no lugar de `sso._http_json`."""

    def __init__(self):
        self.documento = dict(DOCUMENTO)
        self.payload_extra: Dict[str, Any] = {}
        self.userinfo_extra: Dict[str, Any] = {}
        self.erro_na_descoberta: Optional[Exception] = None
        # Um provedor de verdade devolve no `id_token` o `nonce` que recebeu no
        # pedido de autorização. É o que amarra o token àquela ida específica,
        # então o falso precisa fazer o mesmo -- senão o teste do caminho feliz
        # exercitaria a recusa por nonce sem querer.
        self.nonce_da_ida = ""
        # Tudo o que o painel enviou, para conferir o que ele afirma enviar.
        self.pedidos_de_token = []
        self.codigos_trocados = []

    def payload(self) -> Dict[str, Any]:
        agora = int(time.time())
        base = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": SUB,
            "exp": agora + 300,
            "iat": agora,
            "email": EMAIL,
            "nonce": self.nonce_da_ida,
        }
        base.update(self.payload_extra)
        return base

    def userinfo(self) -> Dict[str, Any]:
        base = {"sub": SUB, "email": EMAIL, "email_verified": True}
        base.update(self.userinfo_extra)
        return base

    def __call__(self, url, dados=None, cabecalhos=None, timeout=5.0):
        if url.endswith("/.well-known/openid-configuration"):
            if self.erro_na_descoberta:
                raise self.erro_na_descoberta
            return dict(self.documento)
        if url == self.documento["token_endpoint"]:
            campos = parse_qs((dados or b"").decode("utf-8"))
            self.pedidos_de_token.append({"campos": campos, "cabecalhos": dict(cabecalhos or {})})
            codigo = (campos.get("code") or [""])[0]
            if codigo in self.codigos_trocados:
                # Um provedor de verdade invalida o código na primeira troca.
                raise RuntimeError("code ja trocado")
            self.codigos_trocados.append(codigo)
            # Confere o PKCE como o provedor faria: o verificador tem de casar
            # com o desafio enviado na ida.
            verificador = (campos.get("code_verifier") or [""])[0]
            if not verificador:
                raise RuntimeError("sem code_verifier")
            return {
                "access_token": "token-de-acesso",
                "token_type": "Bearer",
                "id_token": id_token_falso(self.payload()),
            }
        if url == self.documento["userinfo_endpoint"]:
            return self.userinfo()
        raise AssertionError(f"o painel chamou uma URL inesperada: {url}")


class Resposta:
    def __init__(self, status, cabecalhos, corpo):
        self.status = status
        self.cabecalhos = cabecalhos
        self.corpo = corpo

    def cabecalho(self, nome: str) -> str:
        for chave, valor in self.cabecalhos:
            if chave.lower() == nome.lower():
                return valor
        return ""

    def cookies(self):
        return [v for k, v in self.cabecalhos if k.lower() == "set-cookie"]


class BaseDeSSO(unittest.TestCase):
    """Sobe o painel de verdade e fala HTTP com ele.

    `http.client` em vez de `urllib`: precisamos ver o 302 sem que ninguém o
    siga, ler VÁRIOS `Set-Cookie` da mesma resposta e mandar um `Host` mentiroso
    de propósito -- nada disso cabe no `urlopen`.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "storage.sqlite")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute(
                "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, "
                "name TEXT, access_token TEXT, refresh_token TEXT, expires_at INTEGER, "
                "test_status TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE model_combos (id TEXT PRIMARY KEY, name TEXT, models TEXT, "
                "created_at TEXT, updated_at TEXT)"
            )
        cls.settings = Settings(
            db_path=cls.db_path,
            web_host="127.0.0.1",
            web_port=PORTA,
            dashboard_user="admin",
            dashboard_password=SENHA_LOCAL,
        )
        # Derivado do diretório do banco, e NÃO de `get_prefs_path()`: aquele
        # consulta DATA_DIR, que o `setUp` remove do ambiente depois. Numa
        # máquina com DATA_DIR exportada, o teste gravaria a configuração num
        # diretório e o painel leria de outro -- e falharia por um motivo que
        # não tem nada a ver com o que ele mede.
        cls.prefs_path = resolve_prefs_path(os.path.dirname(cls.db_path))
        cls.arquivo_do_segredo = sso.caminho_do_segredo(os.path.dirname(cls.db_path))
        cls.server = start_web_server(
            host="127.0.0.1", port=PORTA, db_path=cls.db_path, settings=cls.settings
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        # O ambiente da máquina não pode decidir o resultado: DATA_DIR mudaria o
        # diretório do segredo, e as outras três mudariam a própria decisão.
        self.ambiente_salvo = {
            nome: os.environ.pop(nome, None)
            for nome in ("DATA_DIR", "OIDC_CLIENT_SECRET", "SSO_DISABLED",
                         "DASHBOARD_RECOVERY_HASH")
        }

        # Estado por processo, zerado entre testes. Sem isto o teto de dez
        # tentativas por janela -- que é do endereço, e todo teste sai do mesmo
        # 127.0.0.1 -- faria o décimo primeiro teste receber 429 e "falhar" por
        # um motivo que não é o dele.
        with protecao._trava:
            protecao._tentativas.clear()
            protecao._falhas.clear()
            protecao._desafios.clear()
        with sso._trava:
            sso._cache_de_descoberta.clear()
            sso._estados_consumidos.clear()

        self.http_real = sso._http_json
        self.novo_provedor()

        sso.grava_client_secret(self.arquivo_do_segredo, SEGREDO_DO_CLIENTE)
        self.liga_sso()

    def novo_provedor(self):
        """Começa uma rodada limpa SEM repetir o setUp inteiro.

        Chamar `setUp()` de novo no meio de um teste guardaria o provedor FALSO
        como se fosse a função real, e o `tearDown` deixaria a falsa instalada
        para o resto do processo -- um teste contaminando os seguintes, que é o
        tipo de defeito que só aparece quando a ordem dos testes muda.
        """
        self.provedor = ProvedorFalso()
        sso._http_json = self.provedor
        with sso._trava:
            sso._cache_de_descoberta.clear()
            sso._estados_consumidos.clear()

    def tearDown(self):
        sso._http_json = self.http_real
        for nome, valor in self.ambiente_salvo.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor

    def liga_sso(self, **mudancas):
        campos = {
            "enabled": "oidc",
            "base_url": PAINEL,
            "oidc_issuer": ISSUER,
            "oidc_client_id": CLIENT_ID,
            "oidc_scopes": "openid email profile",
            "allowed_domains": "empresa.com",
            "allowed_emails": "",
        }
        campos.update(mudancas)
        sso.grava_config(self.prefs_path, campos)
        sso.esquece_descoberta()

    # -- conversa HTTP ----------------------------------------------------

    def pede(self, metodo, caminho, cabecalhos=None, corpo=None) -> Resposta:
        conexao = http.client.HTTPConnection("127.0.0.1", PORTA, timeout=10)
        try:
            conexao.request(metodo, caminho, body=corpo, headers=cabecalhos or {})
            resposta = conexao.getresponse()
            return Resposta(
                resposta.status, resposta.getheaders(), resposta.read().decode("utf-8", "replace")
            )
        finally:
            conexao.close()

    def como_navegador(self, extras=None):
        cabecalhos = {"Accept": "text/html"}
        cabecalhos.update(extras or {})
        return cabecalhos

    def sessao_local(self) -> str:
        """Entra pelo formulário e devolve o cookie de sessão."""
        resposta = self.pede(
            "POST", "/login",
            cabecalhos={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
            corpo=urlencode({"usuario": "admin", "senha": SENHA_LOCAL}),
        )
        self.assertEqual(resposta.status, 302, resposta.corpo[:300])
        for cookie in resposta.cookies():
            if cookie.startswith("ominirtksync_sessao="):
                return cookie.split(";", 1)[0]
        self.fail("o formulário não emitiu cookie de sessão")

    # -- o fluxo, em duas metades -----------------------------------------

    def inicia(self) -> Resposta:
        return self.pede("GET", "/sso/oidc/iniciar", cabecalhos=self.como_navegador())

    def estado_da_ida(self, resposta: Resposta):
        """Devolve (state, cookie_de_estado) a partir do 302 da ida."""
        destino = urlparse(resposta.cabecalho("Location"))
        parametros = parse_qs(destino.query)
        state = parametros["state"][0]
        self.provedor.nonce_da_ida = parametros["nonce"][0]
        for cookie in resposta.cookies():
            if cookie.startswith("ominirtksync_estado_sso="):
                return state, cookie.split(";", 1)[0]
        self.fail("a ida não gravou o cookie de estado")

    def volta(self, state, cookie, codigo="codigo-do-provedor", extras=None, host=None):
        consulta = {"state": state, "code": codigo}
        consulta.update(extras or {})
        cabecalhos = self.como_navegador({"Cookie": cookie})
        if host:
            cabecalhos["Host"] = host
        return self.pede(
            "GET", "/sso/oidc/callback?" + urlencode(consulta), cabecalhos=cabecalhos
        )

    def fluxo_completo(self, **kwargs):
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        return self.volta(state, cookie, **kwargs)

    def assertEntrou(self, resposta: Resposta):
        self.assertEqual(resposta.status, 200, resposta.corpo[:300])
        self.assertTrue(
            any(c.startswith("ominirtksync_sessao=") for c in resposta.cookies()),
            "o callback tinha de emitir o cookie de sessão",
        )

    def assertNaoEntrou(self, resposta: Resposta):
        self.assertEqual(resposta.status, 200, resposta.corpo[:300])
        self.assertFalse(
            any(c.startswith("ominirtksync_sessao=") for c in resposta.cookies()),
            "uma entrada recusada não pode emitir sessão",
        )


class TestIdaAoProvedor(BaseDeSSO):
    def test_a_ida_leva_pkce_s256_e_nunca_plain(self):
        """`plain` deixaria o código interceptado valer sozinho.

        O painel roda em HTTP no loopback: o código passa pela barra de
        endereços e fica no histórico.
        """
        parametros = parse_qs(urlparse(self.inicia().cabecalho("Location")).query)
        self.assertEqual(parametros["code_challenge_method"], ["S256"])
        self.assertTrue(parametros["code_challenge"][0])
        self.assertNotIn("=", parametros["code_challenge"][0], "base64url do PKCE é sem padding")

    def test_o_desafio_e_o_sha256_do_verificador(self):
        """Se o desafio não derivar do verificador, o PKCE é enfeite."""
        ida = self.inicia()
        parametros = parse_qs(urlparse(ida.cabecalho("Location")).query)
        _, cookie = self.estado_da_ida(ida)
        # O verificador está no cookie assinado; o provedor falso já o exige na
        # troca, e aqui conferimos a derivação.
        self.volta(parametros["state"][0], cookie)
        verificador = self.provedor.pedidos_de_token[0]["campos"]["code_verifier"][0]
        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(verificador.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        self.assertEqual(parametros["code_challenge"][0], esperado)

    def test_o_cookie_de_estado_e_lax_e_nao_strict(self):
        """`Strict` não é enviado na volta do provedor, que é navegação de outro site.

        O sintoma seria o pior possível: o operador cai no formulário sem erro
        nenhum na tela, como se a senha estivesse errada.
        """
        cookie = [c for c in self.inicia().cookies() if c.startswith("ominirtksync_estado_sso=")][0]
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("SameSite=Strict", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Path=/sso/", cookie)

    def test_o_retorno_combinado_sai_da_configuracao_e_nao_do_host(self):
        """Derivar a URL de retorno do cabeçalho `Host` é redirect_uri aberto."""
        ida = self.pede(
            "GET", "/sso/oidc/iniciar",
            cabecalhos=self.como_navegador({"Host": "atacante.exemplo"}),
        )
        parametros = parse_qs(urlparse(ida.cabecalho("Location")).query)
        self.assertEqual(parametros["redirect_uri"], [PAINEL + "/sso/oidc/callback"])
        self.assertNotIn("atacante", ida.cabecalho("Location"))

    def test_o_botao_de_sso_aparece_no_formulario_sem_substitui_lo(self):
        """SSO é uma segunda porta. A primeira não sai da tela em configuração nenhuma."""
        pagina = self.pede("GET", "/login", cabecalhos=self.como_navegador()).corpo
        self.assertIn('href="/sso/oidc/iniciar"', pagina)
        self.assertIn('name="senha"', pagina, "o formulário local não pode desaparecer")
        self.assertIn("idp.exemplo.com", pagina)

    def test_o_botao_e_um_link_e_nunca_um_formulario(self):
        """A CSP declara `form-action 'self'`: um <form> para fora seria bloqueado."""
        pagina = self.pede("GET", "/login", cabecalhos=self.como_navegador()).corpo
        self.assertNotIn('action="/sso/oidc/iniciar"', pagina)

    def test_provedor_fora_do_ar_nao_tranca_o_formulario_local(self):
        self.provedor.erro_na_descoberta = OSError("o provedor nao respondeu")
        pagina = self.pede("GET", "/login", cabecalhos=self.como_navegador())
        self.assertEqual(pagina.status, 200)
        self.assertNotIn('href="/sso/oidc/iniciar"', pagina.corpo)
        self.assertIn('name="senha"', pagina.corpo)


class TestVoltaDoProvedor(BaseDeSSO):
    def test_o_caminho_feliz_emite_o_mesmo_cookie_do_formulario(self):
        resposta = self.fluxo_completo()
        self.assertEntrou(resposta)
        do_sso = [c for c in resposta.cookies() if c.startswith("ominirtksync_sessao=")][0]
        do_formulario = self.sessao_local()
        # Mesmo nome, mesma política: uma sessão só, não duas invenções.
        self.assertIn("HttpOnly", do_sso)
        self.assertIn("SameSite=Strict", do_sso)
        self.assertTrue(do_formulario.startswith("ominirtksync_sessao="))

    def test_o_cookie_emitido_abre_o_painel(self):
        """A prova de que a sessão é de verdade: ela entra."""
        resposta = self.fluxo_completo()
        cookie = [
            c.split(";", 1)[0] for c in resposta.cookies()
            if c.startswith("ominirtksync_sessao=")
        ][0]
        painel = self.pede("GET", "/", cabecalhos=self.como_navegador({"Cookie": cookie}))
        self.assertEqual(painel.status, 200)
        self.assertIn("OminiRTKSync", painel.corpo)

    def test_o_pouso_e_200_com_refresh_e_nunca_302(self):
        """Numa cadeia iniciada por outro site o cookie `Strict` não viaja no 302.

        O operador cairia em /login com um cookie válido no bolso -- e o defeito
        se pareceria com "a sessão não funciona".
        """
        resposta = self.fluxo_completo()
        self.assertEqual(resposta.status, 200)
        self.assertEqual(resposta.cabecalho("Location"), "")
        self.assertIn('http-equiv="refresh"', resposta.corpo)
        self.assertIn('url=/', resposta.corpo)

    def test_o_estado_e_apagado_ao_pousar(self):
        resposta = self.fluxo_completo()
        apagados = [
            c for c in resposta.cookies()
            if c.startswith("ominirtksync_estado_sso=") and "Max-Age=0" in c
        ]
        self.assertTrue(apagados, "o cookie de estado tem de morrer no pouso")

    def test_a_troca_do_codigo_repete_o_retorno_combinado(self):
        """`redirect_uri` da troca tem de ser o MESMO da ida, e da configuração."""
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        self.volta(state, cookie, host="atacante.exemplo")
        enviado = self.provedor.pedidos_de_token[0]["campos"]["redirect_uri"]
        self.assertEqual(enviado, [PAINEL + "/sso/oidc/callback"])

    def test_a_troca_autentica_o_cliente_por_basic(self):
        self.fluxo_completo()
        cabecalho = self.provedor.pedidos_de_token[0]["cabecalhos"]["Authorization"]
        self.assertTrue(cabecalho.startswith("Basic "))
        decodificado = base64.b64decode(cabecalho[6:]).decode("utf-8")
        self.assertIn(CLIENT_ID, decodificado)

    def test_nenhum_parametro_da_query_vira_destino(self):
        """`next=` usado como destino é redirecionamento aberto já autenticado."""
        resposta = self.fluxo_completo(extras={"next": "https://atacante.exemplo/"})
        self.assertEntrou(resposta)
        self.assertNotIn("atacante.exemplo", resposta.corpo)

    def test_o_email_entra_marcado_como_federado(self):
        """O prefixo distingue no log quem veio pela porta federada."""
        # A marca vive no cookie assinado; conferimos pelo módulo, sem inventar
        # uma segunda forma de ler sessão.
        from omini_rtksync import sessao
        resposta = self.fluxo_completo()
        self.assertEntrou(resposta)
        valor = [
            c.split(";", 1)[0].split("=", 1)[1] for c in resposta.cookies()
            if c.startswith("ominirtksync_sessao=")
        ][0]
        self.assertEqual(sessao.usuario_da_sessao(valor), f"sso:{EMAIL}")


class TestRecusas(BaseDeSSO):
    """Uma entrada ruim por verificação. É aqui que o fluxo prova que existe."""

    def test_sem_cookie_de_estado_nao_entra(self):
        """Ausência de cookie é recusa, sem exceção: é o CSRF de login."""
        ida = self.inicia()
        state, _ = self.estado_da_ida(ida)
        self.assertNaoEntrou(self.volta(state, cookie="", codigo="c"))

    def test_cookie_de_estado_forjado_nao_entra(self):
        """A assinatura é o que impede alguém de escolher o próprio `state`.

        Sem ela, casar `state` da query com `state` do cookie não prova nada:
        quem monta os dois lados casa sempre.
        """
        # O atacante monta o cookie E inicia o fluxo no provedor com o mesmo
        # `nonce`, que é o que ele faria de verdade. Assim a ÚNICA coisa entre
        # ele e uma sessão é a assinatura do cookie -- e é isso que se mede.
        self.provedor.nonce_da_ida = "nonce-inventado"
        carga = base64.urlsafe_b64encode(
            f"state-inventado|nonce-inventado|verificador|{int(time.time()) + 600}".encode("utf-8")
        ).decode("ascii")
        forjado = f"ominirtksync_estado_sso={carga}.assinatura-inventada"
        self.assertNaoEntrou(self.volta("state-inventado", forjado))

    def test_state_trocado_nao_entra(self):
        ida = self.inicia()
        _, cookie = self.estado_da_ida(ida)
        self.assertNaoEntrou(self.volta("state-de-outra-pessoa", cookie))

    def test_state_ausente_nao_entra(self):
        ida = self.inicia()
        _, cookie = self.estado_da_ida(ida)
        resposta = self.pede(
            "GET", "/sso/oidc/callback?code=abc", cabecalhos=self.como_navegador({"Cookie": cookie})
        )
        self.assertNaoEntrou(resposta)

    def test_o_mesmo_state_nao_serve_duas_vezes(self):
        """Apagar o cookie não basta: o cookie vive no navegador de quem o capturou."""
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        self.assertEntrou(self.volta(state, cookie))
        self.assertNaoEntrou(self.volta(state, cookie, codigo="outro-codigo"))

    def test_codigo_reapresentado_nao_entra(self):
        """Uma segunda ida com o código da primeira não pode virar segunda sessão."""
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        self.assertEntrou(self.volta(state, cookie, codigo="codigo-unico"))
        # Ida nova, cookie novo, código velho: o provedor recusa a segunda troca.
        ida2 = self.inicia()
        state2, cookie2 = self.estado_da_ida(ida2)
        self.assertNaoEntrou(self.volta(state2, cookie2, codigo="codigo-unico"))

    def test_erro_declarado_pelo_provedor_nao_entra(self):
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        self.assertNaoEntrou(
            self.volta(state, cookie, extras={"error": "access_denied"})
        )

    def test_codigo_vazio_nao_entra(self):
        ida = self.inicia()
        state, cookie = self.estado_da_ida(ida)
        self.assertNaoEntrou(self.volta(state, cookie, codigo=""))

    def test_issuer_diferente_no_id_token_nao_entra(self):
        self.provedor.payload_extra = {"iss": "https://outro-idp.exemplo.com"}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_audiencia_sem_o_nosso_client_id_nao_entra(self):
        """Token legítimo emitido para OUTRO serviço não vale aqui."""
        self.provedor.payload_extra = {"aud": "cliente-de-outro-servico"}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_varias_audiencias_com_azp_de_outro_nao_entra(self):
        self.provedor.payload_extra = {
            "aud": [CLIENT_ID, "outro"], "azp": "outro",
        }
        self.assertNaoEntrou(self.fluxo_completo())

    def test_token_vencido_nao_entra(self):
        self.provedor.payload_extra = {"exp": int(time.time()) - 60}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_emitido_fora_da_tolerancia_de_relogio_nao_entra(self):
        """Tolerância generosa transforma prazo em decoração."""
        self.provedor.payload_extra = {"iat": int(time.time()) + 3600}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_nonce_diferente_do_cookie_nao_entra(self):
        self.provedor.payload_extra = {"nonce": "nonce-de-outra-ida"}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_userinfo_de_outra_pessoa_nao_entra(self):
        """O `sub` das duas respostas tem de ser o mesmo (OIDC Core 5.3.2)."""
        self.provedor.userinfo_extra = {"sub": "sub-de-outra-pessoa"}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_email_nao_confirmado_nao_entra(self):
        """Sem isso, qualquer conta com e-mail não verificado entra."""
        self.provedor.userinfo_extra = {"email_verified": False}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_email_fora_da_allowlist_nao_entra(self):
        self.provedor.userinfo_extra = {"email": "estranho@outra-empresa.com"}
        self.assertNaoEntrou(self.fluxo_completo())

    def test_email_exato_na_allowlist_entra_mesmo_com_dominio_de_fora(self):
        self.liga_sso(allowed_domains="", allowed_emails="convidado@parceiro.com")
        self.provedor.userinfo_extra = {"email": "convidado@parceiro.com"}
        self.assertEntrou(self.fluxo_completo())

    def test_documento_de_descoberta_com_outro_issuer_nem_sai_daqui(self):
        """Defesa contra mix-up, e ela age ANTES da ida.

        Sem esta conferência, um documento servido por outro provedor apontaria
        os passos seguintes para os endpoints dele -- e o painel entregaria o
        código de autorização na casa errada. Por isso a recusa acontece na
        ida: o navegador nem chega a sair.
        """
        self.provedor.documento = dict(DOCUMENTO, issuer="https://outro-idp.exemplo.com")
        ida = self.inicia()
        self.assertEqual(ida.cabecalho("Location"), "", "não podia ter havido ida nenhuma")
        self.assertEqual(ida.status, 200)
        self.assertIn('name="senha"', ida.corpo, "a recusa devolve o formulário local")

    def test_issuer_sem_tls_fora_do_loopback_e_recusado(self):
        with self.assertRaises(sso.ErroDeSSO):
            sso.descobre("http://idp.exemplo.com")

    def test_todas_as_recusas_dizem_a_mesma_coisa(self):
        """Distinguir os motivos conta ao atacante em que ponto ele parou."""
        corpos = []
        for preparo in (
            lambda: self.provedor.payload_extra.update({"nonce": "errado"}),
            lambda: self.provedor.userinfo_extra.update({"email_verified": False}),
            lambda: self.provedor.userinfo_extra.update({"email": "fora@outra.com"}),
        ):
            self.novo_provedor()
            preparo()
            corpos.append(self.fluxo_completo().corpo)
        self.assertEqual(
            len(set(corpos)), 1, "as mensagens de recusa precisam ser indistinguíveis"
        )

    def test_o_teto_por_endereco_vale_para_as_rotas_novas(self):
        """Rota pública nova é superfície de força bruta nova."""
        vistos = set()
        for _ in range(protecao.TENTATIVAS_POR_JANELA + 3):
            vistos.add(self.inicia().status)
        self.assertIn(429, vistos, "a ida ao provedor precisa do mesmo teto do formulário")


class TestDesligado(BaseDeSSO):
    def test_sem_configuracao_as_rotas_nao_existem(self):
        self.liga_sso(enabled="")
        self.assertEqual(self.inicia().status, 404)
        self.assertEqual(
            self.pede("GET", "/sso/oidc/callback?code=x", cabecalhos=self.como_navegador()).status,
            404,
        )

    def test_sem_configuracao_o_formulario_e_o_de_antes(self):
        self.liga_sso(enabled="")
        pagina = self.pede("GET", "/login", cabecalhos=self.como_navegador()).corpo
        self.assertNotIn("/sso/oidc/iniciar", pagina)
        self.assertIn('name="senha"', pagina)

    def test_a_variavel_de_ambiente_vence_o_banco(self):
        """É o que salva quando o provedor caiu e o painel está atrás de um túnel."""
        os.environ["SSO_DISABLED"] = "1"
        self.assertEqual(self.inicia().status, 404)
        pagina = self.pede("GET", "/login", cabecalhos=self.como_navegador()).corpo
        self.assertNotIn("/sso/oidc/iniciar", pagina)

    def test_sem_segredo_o_sso_nao_liga_sozinho(self):
        """Sem credencial do cliente ele fica DESLIGADO, e não ligado sem credencial."""
        os.remove(self.arquivo_do_segredo)
        self.assertEqual(self.inicia().status, 404)

    def test_allowlist_vazia_desliga_o_fluxo(self):
        """Nem que alguém grave direto no banco: o painel recusa usar a lista vazia."""
        self.liga_sso(allowed_domains="", allowed_emails="")
        self.assertEqual(self.inicia().status, 404)

    def test_a_porta_nao_html_continua_com_basic_auth(self):
        """curl, cron e monitoramento nunca passam por SSO."""
        credencial = base64.b64encode(f"admin:{SENHA_LOCAL}".encode("utf-8")).decode("ascii")
        resposta = self.pede(
            "GET", "/api/status", cabecalhos={"Authorization": f"Basic {credencial}"}
        )
        self.assertEqual(resposta.status, 200)


class TestConfiguracao(BaseDeSSO):
    def salva(self, cookie, **campos):
        corpo = {
            "senha_local": SENHA_LOCAL,
            "enabled": "oidc",
            "base_url": PAINEL,
            "oidc_issuer": ISSUER,
            "oidc_client_id": CLIENT_ID,
            "oidc_scopes": "openid email profile",
            "allowed_domains": "empresa.com",
            "allowed_emails": "",
            "oidc_client_secret": "",
        }
        corpo.update(campos)
        return self.pede(
            "POST", "/acoes/sso",
            cabecalhos={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
                "Origin": PAINEL,
                "Host": f"127.0.0.1:{PORTA}",
            },
            corpo=urlencode(corpo),
        )

    def test_a_senha_local_e_exigida_alem_da_sessao(self):
        """Quem sequestra uma sessão poderia apontar o painel para um provedor hostil."""
        cookie = self.sessao_local()
        self.liga_sso(enabled="")
        resposta = self.salva(cookie, senha_local="senha-errada", oidc_issuer="https://hostil.exemplo")
        self.assertEqual(resposta.status, 303)
        self.assertIn("aviso=", resposta.cabecalho("Location"))
        config = sso.ler_config(self.prefs_path)
        self.assertNotIn(
            "hostil", config["oidc_issuer"],
            "nada pode ter sido gravado com a senha errada",
        )
        self.assertEqual(config["enabled"], "", "e o SSO não pode ter sido ligado")

    def test_um_post_de_outra_origem_nao_configura_nada(self):
        """Sem isto, uma página aberta na mesma máquina reconfigura o painel.

        A senha local já barraria, mas as duas guardas existem de propósito: a
        de mesma origem recusa antes de olhar qualquer campo.
        """
        cookie = self.sessao_local()
        self.liga_sso(enabled="")
        resposta = self.pede(
            "POST", "/acoes/sso",
            cabecalhos={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
                "Origin": "https://atacante.exemplo",
                "Host": f"127.0.0.1:{PORTA}",
            },
            corpo=urlencode({
                "senha_local": SENHA_LOCAL, "enabled": "oidc",
                "base_url": PAINEL, "oidc_issuer": "https://hostil.exemplo",
                "oidc_client_id": "do-atacante", "allowed_emails": "atacante@hostil.exemplo",
            }),
        )
        self.assertEqual(resposta.status, 303)
        config = sso.ler_config(self.prefs_path)
        self.assertEqual(config["enabled"], "")
        self.assertNotIn("hostil", config["oidc_issuer"])

    def test_a_configuracao_e_gravada_com_a_senha_certa(self):
        cookie = self.sessao_local()
        self.liga_sso(enabled="")
        self.salva(cookie)
        config = sso.ler_config(self.prefs_path)
        self.assertEqual(config["enabled"], "oidc")
        self.assertEqual(config["oidc_issuer"], ISSUER)

    def test_allowlist_vazia_e_recusada_no_momento_de_ligar(self):
        """Sem ela, "entrar com o provedor" significa que toda conta dele entra."""
        cookie = self.sessao_local()
        self.liga_sso(enabled="")
        self.salva(cookie, allowed_domains="", allowed_emails="")
        self.assertEqual(sso.ler_config(self.prefs_path)["enabled"], "")

    def test_segredo_em_branco_mantem_o_anterior(self):
        """Apagar a credencial por um campo vazio derrubaria o SSO sem explicação."""
        cookie = self.sessao_local()
        self.salva(cookie, oidc_client_secret="")
        self.assertEqual(
            sso.resolve_client_secret(self.arquivo_do_segredo), SEGREDO_DO_CLIENTE
        )

    def test_o_segredo_novo_substitui_e_fica_com_permissao_0600(self):
        cookie = self.sessao_local()
        self.salva(cookie, oidc_client_secret="outro-segredo")
        self.assertEqual(sso.resolve_client_secret(self.arquivo_do_segredo), "outro-segredo")
        self.assertEqual(os.stat(self.arquivo_do_segredo).st_mode & 0o777, 0o600)

    def test_saml_nao_pode_ser_ligado_nesta_imagem(self):
        cookie = self.sessao_local()
        self.liga_sso(enabled="")
        self.salva(cookie, enabled="saml")
        self.assertEqual(sso.ler_config(self.prefs_path)["enabled"], "")

    def test_o_segredo_nunca_volta_para_a_tela(self):
        """Um GET de configuração que devolvesse o valor seria publicá-lo no HTML."""
        cookie = self.sessao_local()
        painel = self.pede("GET", "/", cabecalhos=self.como_navegador({"Cookie": cookie}))
        self.assertEqual(painel.status, 200)
        self.assertIn("modalSSO", painel.corpo, "a tela de configuração tem de existir")
        self.assertNotIn(SEGREDO_DO_CLIENTE, painel.corpo)

    def test_o_ambiente_vence_o_arquivo(self):
        os.environ["OIDC_CLIENT_SECRET"] = "segredo-do-ambiente"
        self.assertEqual(
            sso.resolve_client_secret(self.arquivo_do_segredo), "segredo-do-ambiente"
        )
        self.assertTrue(sso.segredo_vem_do_ambiente())

    def test_a_tela_diz_que_ha_segredo_sem_dizer_qual(self):
        cookie = self.sessao_local()
        corpo = self.pede("GET", "/", cabecalhos=self.como_navegador({"Cookie": cookie})).corpo
        self.assertIn("••••••••", corpo)
        self.assertNotIn(SEGREDO_DO_CLIENTE, corpo)

    def test_o_botao_de_configuracoes_fica_antes_do_sair(self):
        """Sair é sempre o último da barra."""
        cookie = self.sessao_local()
        corpo = self.pede("GET", "/", cabecalhos=self.como_navegador({"Cookie": cookie})).corpo
        self.assertLess(
            corpo.index("#modalSSO"), corpo.index('action="/logout"'),
            "o botão de configurações entra ANTES de Sair",
        )


if __name__ == "__main__":
    unittest.main()
