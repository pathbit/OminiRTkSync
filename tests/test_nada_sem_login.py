"""Nada do painel responde sem login, exceto o que precisa ser público.

O painel lê o banco do gateway e mostra a saúde das credenciais. Uma rota que
escape da exigência de sessão entrega isso a quem chegar — e o jeito de uma rota
escapar não é alguém decidir abri-la, é alguém acrescentar uma rota nova e
esquecer de protegê-la. Por isso esta guarda enumera o despacho do servidor e
cobra o inverso: toda rota é fechada, menos as quatro que têm motivo declarado.

As quatro públicas, e por quê:

- `/healthz`     o healthcheck do Docker roda sem credencial nenhuma;
- `/login`       exigir sessão para exibir o formulário que cria a sessão é um
                 círculo fechado;
- `/robots.txt`  um rastreador não tem como autenticar, e a regra só serve se
                 ele conseguir lê-la;
- `/credenciais-atualizadas`  é servida no instante seguinte à troca de senha,
                 quando o navegador ainda guarda a anterior; exigir a nova ali
                 daria um 401 cru logo depois de a troca ter dado certo.

E as duas da entrada federada, acrescentadas quando o SSO entrou:

- `/sso/oidc/iniciar`   a ida ao provedor de identidade acontece sem sessão — é
                 a sessão que ela existe para criar. Exigir uma aqui seria o
                 mesmo círculo fechado do `/login`;
- `/sso/oidc/callback`  a volta do provedor também chega sem sessão, e vem de
                 outro site por definição. Quem prova a identidade nela não é
                 um cookie de sessão e sim, nesta ordem: o cookie de estado
                 assinado (com `state`, `nonce` e o verificador do PKCE), o
                 `state` conferido com `hmac.compare_digest` e de uso único, a
                 troca do código num canal TLS direto com o cliente
                 autenticado, e a allowlist obrigatória. Ver
                 `tests/test_sso_oidc.py`, que exercita cada uma dessas recusas.

As duas passam pelo mesmo `protecao.registra_tentativa` do formulário — rota
pública nova é superfície de força bruta nova — e as duas só existem quando o
SSO está ligado e completo: sem configuração, `rota_existe` devolve False e
elas respondem 404 como qualquer rota que este servidor não serve.
"""

import pathlib
import re
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SERVIDOR = RAIZ / "src" / "omini_rtksync" / "web.py"

PUBLICAS = {
    "/healthz", "/login", "/robots.txt", "/credenciais-atualizadas",
    "/sso/oidc/iniciar", "/sso/oidc/callback",
    # As duas do SAML2 sao publicas pela MESMA razao que as do OIDC: a ida
    # acontece antes de existir sessao, e a volta (`acs`) e um POST do PROVEDOR
    # de identidade, que nao carrega cookie nenhum deste painel. Exigir sessao
    # nelas tornaria o SSO impossivel -- e nao seria mais seguro: quem entra por
    # aqui ainda passa pela validacao de assinatura do proprio SAML.
    "/sso/saml/iniciar", "/sso/saml/acs",
}

# Rotas citadas no despacho: `route == "/x"`, `path == "/x"`, startswith("/x")
ROTA = re.compile(r'(?:route|path|rota_inicial)\s*==\s*"(/[a-z0-9/_-]*)"')
ROTA_PREFIXO = re.compile(r'startswith\(\s*"(/[a-z0-9/_-]+)"')


class NadaRespondeSemLogin(unittest.TestCase):
    def setUp(self):
        self.fonte = SERVIDOR.read_text(encoding="utf-8")

    def test_toda_rota_publica_esta_declarada_aqui(self):
        """Uma rota nova servida antes do require_auth tem de passar por aqui."""
        # Trecho entre o início do do_GET e a primeira exigência de sessão: é
        # exatamente o que o servidor entrega sem olhar credencial.
        inicio = self.fonte.find("def do_GET")
        fim = self.fonte.find("require_auth()", inicio)
        self.assertGreater(fim, inicio, "não achei a exigência de sessão no do_GET")
        antes_do_login = self.fonte[inicio:fim]

        servidas = set(ROTA.findall(antes_do_login))
        fora_da_lista = servidas - PUBLICAS
        self.assertEqual(
            fora_da_lista,
            set(),
            "estas rotas são servidas ANTES de exigir sessão e não estão na lista "
            f"de públicas: {sorted(fora_da_lista)} -- se a rota deve mesmo ser "
            "pública, acrescente-a à lista COM o motivo; se não, mova-a para "
            "depois do require_auth",
        )

    def test_o_post_tambem_exige_sessao(self):
        """O POST muda estado: escapar da sessão ali é pior que no GET."""
        inicio = self.fonte.find("def do_POST")
        fim = self.fonte.find("require_auth()", inicio)
        self.assertGreater(fim, inicio, "o do_POST precisa exigir sessão")
        antes = self.fonte[inicio:fim]
        servidas = set(ROTA.findall(antes))
        # /login e /logout são POST sem sessão por definição: um a cria, o outro
        # a destrói, e exigir sessão para sair é prender quem quer ir embora.
        # /sso/saml/acs entra pelo mesmo tipo de razão: quem posta ali é o
        # PROVEDOR de identidade, que não tem cookie deste painel. Exigir sessão
        # tornaria o SSO impossível -- e o que autoriza a entrada por ali não é
        # o cookie, é a assinatura da asserção, verificada antes de qualquer
        # sessão ser emitida.
        fora = servidas - {"/login", "/logout", "/sso/saml/acs"}
        self.assertEqual(
            fora,
            set(),
            f"POST sem exigir sessão: {sorted(fora)}",
        )

    def test_a_exigencia_de_sessao_vem_antes_do_corpo(self):
        """Ler o corpo antes de autenticar aceita carga de quem não entrou."""
        inicio = self.fonte.find("def do_POST")
        trecho = self.fonte[inicio : inicio + 2000]
        pos_auth = trecho.find("require_auth()")
        pos_corpo = trecho.find("Content-Length")
        if pos_corpo >= 0:
            self.assertLess(
                pos_auth,
                pos_corpo,
                "o corpo do POST é lido antes de a sessão ser exigida",
            )


if __name__ == "__main__":
    unittest.main()
