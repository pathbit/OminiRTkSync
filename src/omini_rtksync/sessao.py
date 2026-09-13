"""Sessão do painel: um cookie assinado, emitido por um formulário de login.

O painel nasceu só com Basic Auth, e isso cobra três preços. O navegador abre um
diálogo próprio, fora da página, que não se pode estilizar nem traduzir; não há
logout, porque o navegador reenvia a credencial até fechar a janela; e qualquer
ferramenta que dirija um navegador trava no diálogo, que não é HTML.

O Basic Auth continua aceito — é o que faz `curl` e scripts funcionarem sem
sessão. O que muda é que agora existe uma segunda porta: um formulário que
entrega um cookie assinado.

O segredo que assina o cookie nasce a cada processo, em memória. Reiniciar o
serviço invalida as sessões abertas, o que é a escolha certa para um painel que
lê credenciais: não há sessão sobrevivendo a uma troca de senha ou a um
container recriado.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

NOME_DO_COOKIE = "ominirtksync_sessao"

# Cookie de ida e volta do SSO. Guarda `state`, `nonce` e o verificador do PKCE
# entre a saida para o provedor de identidade e o retorno dele. Nao e sessao:
# nao da acesso a nada, e morre em dez minutos.
NOME_DO_COOKIE_ESTADO_SSO = "ominirtksync_estado_sso"

# Oito horas: um turno de trabalho. Depois disso o operador entra de novo.
VALIDADE_EM_SEGUNDOS = 8 * 60 * 60

# Dez minutos: o tempo de digitar a senha no provedor de identidade e voltar.
VALIDADE_DO_ESTADO_SSO_EM_SEGUNDOS = 600

_SEGREDO = secrets.token_bytes(32)


def _assina(carga: str) -> str:
    return hmac.new(_SEGREDO, carga.encode("utf-8"), hashlib.sha256).hexdigest()


def emitir(usuario: str, agora: Optional[float] = None) -> str:
    """Devolve o valor do cookie para um usuário já autenticado."""
    expira = int((agora if agora is not None else time.time()) + VALIDADE_EM_SEGUNDOS)
    carga = f"{usuario}|{expira}"
    codificada = base64.urlsafe_b64encode(carga.encode("utf-8")).decode("ascii")
    return f"{codificada}.{_assina(carga)}"


def usuario_da_sessao(valor: str, agora: Optional[float] = None) -> Optional[str]:
    """Devolve o usuário se o cookie for íntegro e estiver no prazo, senão None."""
    if not valor or "." not in valor:
        return None
    codificada, assinatura = valor.rsplit(".", 1)
    try:
        carga = base64.urlsafe_b64decode(codificada.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    # compare_digest: a comparação não pode vazar, pelo tempo que leva, quantos
    # caracteres do início bateram.
    if not hmac.compare_digest(assinatura, _assina(carga)):
        return None
    if "|" not in carga:
        return None
    usuario, _, expira = carga.rpartition("|")
    try:
        if float(expira) < (agora if agora is not None else time.time()):
            return None
    except ValueError:
        return None
    return usuario or None


def cabecalho_para_gravar(valor: str) -> str:
    """Cookie de sessão: inacessível ao script da página e presa a este site.

    Sem `Secure` de propósito: o painel é servido em HTTP no loopback, e um
    cookie `Secure` simplesmente não seria gravado ali.
    """
    return (
        f"{NOME_DO_COOKIE}={valor}; Path=/; HttpOnly; SameSite=Strict; "
        f"Max-Age={VALIDADE_EM_SEGUNDOS}"
    )


def cabecalho_para_apagar() -> str:
    return f"{NOME_DO_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def ler_do_cabecalho(cabecalho_cookie: str) -> str:
    """Extrai o valor do nosso cookie de um cabeçalho Cookie cru."""
    return _valor_do_cookie(cabecalho_cookie, NOME_DO_COOKIE)


def _valor_do_cookie(cabecalho_cookie: str, nome_procurado: str) -> str:
    for parte in (cabecalho_cookie or "").split(";"):
        nome, _, valor = parte.strip().partition("=")
        if nome == nome_procurado:
            return valor
    return ""


# ---------------------------------------------------------------------------
# Estado da ida ao provedor de identidade (SSO)
#
# Fica aqui, e não num módulo próprio, para que o segredo que assina continue
# sendo UM só por processo: dois segredos seriam duas superfícies para manter
# em dia, e a segunda é sempre a que alguém esquece de rotacionar.
# ---------------------------------------------------------------------------


def emitir_estado_sso(
    state: str, nonce: str, verificador: str, agora: Optional[float] = None
) -> str:
    """Empacota e assina o que a volta do provedor precisa conferir."""
    expira = int(
        (agora if agora is not None else time.time()) + VALIDADE_DO_ESTADO_SSO_EM_SEGUNDOS
    )
    carga = f"{state}|{nonce}|{verificador}|{expira}"
    codificada = base64.urlsafe_b64encode(carga.encode("utf-8")).decode("ascii")
    return f"{codificada}.{_assina(carga)}"


def ler_estado_sso(valor: str, agora: Optional[float] = None):
    """Devolve (state, nonce, verificador) se íntegro e no prazo, senão None."""
    if not valor or "." not in valor:
        return None
    codificada, assinatura = valor.rsplit(".", 1)
    try:
        carga = base64.urlsafe_b64decode(codificada.encode("ascii")).decode("utf-8")
    except Exception:
        return None
    if not hmac.compare_digest(assinatura, _assina(carga)):
        return None
    partes = carga.split("|")
    if len(partes) != 4:
        return None
    state, nonce, verificador, expira = partes
    try:
        if float(expira) < (agora if agora is not None else time.time()):
            return None
    except ValueError:
        return None
    if not (state and nonce and verificador):
        return None
    return state, nonce, verificador


def cabecalho_para_gravar_estado_sso(valor: str) -> str:
    """Cookie de estado do SSO.

    `SameSite=Lax`, e NÃO `Strict` como o de sessão: a volta do provedor de
    identidade é navegação vinda de outro site, e um cookie `Strict`
    simplesmente não é enviado nela — o operador cairia de volta no formulário
    sem nenhum erro visível, que é o pior defeito possível aqui.

    `Path=/sso/` porque ele não tem o que fazer em nenhuma outra rota, e sem
    `Secure` pelo mesmo motivo do cookie de sessão: o painel é servido em HTTP
    no loopback.
    """
    return (
        f"{NOME_DO_COOKIE_ESTADO_SSO}={valor}; Path=/sso/; HttpOnly; SameSite=Lax; "
        f"Max-Age={VALIDADE_DO_ESTADO_SSO_EM_SEGUNDOS}"
    )


def cabecalho_para_apagar_estado_sso() -> str:
    return f"{NOME_DO_COOKIE_ESTADO_SSO}=; Path=/sso/; HttpOnly; SameSite=Lax; Max-Age=0"


def ler_estado_do_cabecalho(cabecalho_cookie: str) -> str:
    return _valor_do_cookie(cabecalho_cookie, NOME_DO_COOKIE_ESTADO_SSO)
