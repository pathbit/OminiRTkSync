"""Entrada federada no painel: OpenID Connect, com biblioteca nenhuma.

O painel continua tendo o formulário de usuário e senha, sempre. Isto aqui é
uma SEGUNDA porta, opcional, para quem já tem provedor de identidade na
empresa. Se o provedor cair, o formulário local continua na tela e a credencial
de recuperação continua entrando -- SSO nunca é exclusivo.

=== ONDE CADA COISA MORA

Três gavetas, separadas por sensibilidade:

1. **O que não é segredo** vai no SQLite de preferências que já existe
   (`prefs.py`), com prefixo `sso.`: provedor ativo, issuer, client_id,
   allowlist, URL pública. É a mesma tabela do idioma e do usuário do painel.
2. **O client_secret** vai num arquivo 0600 ao lado da credencial de
   recuperação (`.sso_client_secret`), NUNCA no SQLite. O molde é
   `auth.ensure_recovery_hash`, copiado linha a linha.
3. **O ambiente vence**: `OIDC_CLIENT_SECRET` tem precedência sobre o arquivo.
   Sem nenhum dos dois não há segredo, e sem segredo o SSO fica DESLIGADO -- ele
   não "liga silenciosamente sem credencial".

Por que não cifrar o segredo no banco: cifra reversível precisa de chave, e a
chave ficaria num arquivo ao lado do próprio banco, no mesmo volume. É o mesmo
modelo de ataque do arquivo 0600 puro, com o custo de um AEAD artesanal (a
imagem não traz `cryptography`). A proteção real é a permissão e o isolamento do
volume; dizer "cifrado em repouso" com a chave ao lado é teatro.

=== POR QUE A ASSINATURA DO `id_token` NÃO É VERIFICADA AQUI

A stdlib não verifica RSA e a imagem não traz `cryptography`: verificar
localmente exigiria escrever PKCS#1 v1.5 à mão, em Python, e a checagem de
padding é exatamente onde essas implementações falham em silêncio -- aceitando
assinatura forjada. O `id_token` chega pelo canal DIRETO entre nós e o
`token_endpoint`, sobre TLS com certificado verificado e com o cliente
autenticado; OIDC Core 3.1.3.7, item 6, dispensa a verificação de assinatura
nesse caso. Em troca, o `userinfo_endpoint` é consultado e o `sub` das duas
respostas tem de ser o mesmo (OIDC Core 5.3.2), o que prova que a troca do
código foi real. Consequência aceita: o JWKS nunca é buscado.

=== SAML2 NÃO ESTÁ NESTA VERSÃO

Está declarado em `[project.optional-dependencies] saml` e a aba existe na
tela, desabilitada. O motivo é técnico e não de prazo: SAML assina sobre
Exclusive XML Canonicalization 1.0, e `xml.etree.canonicalize` faz C14N 2.0 --
outro algoritmo; não há verificação RSA na stdlib; e `xml.etree` não se defende
de XML Signature Wrapping, o ataque específico deste protocolo. Fazer isso à
mão daria uma validação que parece funcionar e aceita asserção forjada.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .prefs import get_preference, set_preference

# Chaves de preferência. Tudo que está aqui é público por natureza: quem lê o
# SQLite não ganha nada que já não esteja na barra de endereços do navegador.
CHAVE_ATIVO = "sso.enabled"
CHAVE_BASE_URL = "sso.base_url"
CHAVE_OIDC_ISSUER = "sso.oidc.issuer"
CHAVE_OIDC_CLIENT_ID = "sso.oidc.client_id"
CHAVE_OIDC_SCOPES = "sso.oidc.scopes"
CHAVE_SAML_ENTITY_ID = "sso.saml.idp_entity_id"
CHAVE_SAML_SSO_URL = "sso.saml.idp_sso_url"
CHAVE_SAML_CERT = "sso.saml.idp_cert"
CHAVE_DOMINIOS = "sso.allowed_domains"
CHAVE_EMAILS = "sso.allowed_emails"
CHAVE_ATUALIZADO_EM = "sso.updated_at"

ARQUIVO_DO_SEGREDO = ".sso_client_secret"

# Os nomes das duas variaveis aparecem LITERAIS em cada `os.environ.get` abaixo,
# e nao como constante. A guarda de `test_env_documentation.py` varre o fonte
# atras desses literais para cobrar que o `.env.example` documente cada variavel
# que o programa le: escondida atras de uma constante, a variavel existiria so
# para quem leu a fonte. E `OIDC_CLIENT_SECRET` tem SECRET no nome de proposito,
# porque e assim que o varredor de `test_ajuda_nao_publica_credencial.py` a
# reconhece no .env e procura o valor dela no que o git rastreia.

ESCOPOS_PADRAO = "openid email profile"

CAMINHO_DO_CALLBACK = "/sso/oidc/callback"
CAMINHO_DO_INICIO = "/sso/oidc/iniciar"

# Descoberta: uma hora para o que deu certo, um minuto para o que falhou. O
# negativo curto existe porque `/login` é público: sem ele, cada visita à tela
# de entrada com o provedor fora do ar pagaria o timeout inteiro.
TTL_DESCOBERTA_EM_SEGUNDOS = 3600.0
TTL_FALHA_DE_DESCOBERTA_EM_SEGUNDOS = 60.0

# Tolerância de relógio do `iat`. Container com hora errada derruba o login e o
# sintoma parece "SSO quebrado"; cinco minutos absorvem o desvio comum sem
# transformar o prazo em decoração.
TOLERANCIA_DE_RELOGIO_EM_SEGUNDOS = 300

# Teto do conjunto de `state` já usados. A rota de início é pública: sem teto,
# cada visita deixaria um registro e a memória cresceria sem limite.
MAXIMO_DE_ESTADOS_CONSUMIDOS = 500

HOSTS_DE_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}

_trava = threading.Lock()
_cache_de_descoberta: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_estados_consumidos: Dict[str, float] = {}


class ErroDeSSO(Exception):
    """Falha em qualquer ponto do fluxo federado.

    O `detalhe` é para o log interno. A TELA recebe sempre a mesma mensagem
    genérica: distinguir "state errado" de "e-mail fora da lista" conta ao
    atacante em que ponto ele parou.
    """

    def __init__(self, detalhe: str):
        super().__init__(detalhe)
        self.detalhe = detalhe


# ---------------------------------------------------------------------------
# Interruptor de emergência
# ---------------------------------------------------------------------------

def desligado_pelo_ambiente() -> bool:
    """`SSO_DISABLED` vence o banco, sem precisar tocar no SQLite.

    É o que salva quando o provedor caiu e o painel está atrás de um túnel:
    sobe o container com a variável e o formulário local volta a ser a única
    porta.
    """
    valor = os.environ.get("SSO_DISABLED", "").strip().lower()
    return valor in ("1", "true", "yes", "on", "sim")


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def ler_config(prefs_path: str) -> Dict[str, str]:
    """Lê a configuração do SQLite. Nunca devolve segredo."""
    def ler(chave: str, padrao: str = "") -> str:
        return (get_preference(prefs_path, chave, padrao) or "").strip()

    return {
        "enabled": ler(CHAVE_ATIVO),
        "base_url": ler(CHAVE_BASE_URL).rstrip("/"),
        "oidc_issuer": ler(CHAVE_OIDC_ISSUER).rstrip("/"),
        "oidc_client_id": ler(CHAVE_OIDC_CLIENT_ID),
        "oidc_scopes": ler(CHAVE_OIDC_SCOPES) or ESCOPOS_PADRAO,
        "saml_idp_entity_id": ler(CHAVE_SAML_ENTITY_ID),
        "saml_idp_sso_url": ler(CHAVE_SAML_SSO_URL),
        "saml_idp_cert": ler(CHAVE_SAML_CERT),
        "allowed_domains": ler(CHAVE_DOMINIOS),
        "allowed_emails": ler(CHAVE_EMAILS),
        "updated_at": ler(CHAVE_ATUALIZADO_EM),
    }


def grava_config(prefs_path: str, campos: Dict[str, str]) -> bool:
    """Grava a configuração pública. O segredo NÃO passa por aqui."""
    mapa = {
        "enabled": CHAVE_ATIVO,
        "base_url": CHAVE_BASE_URL,
        "oidc_issuer": CHAVE_OIDC_ISSUER,
        "oidc_client_id": CHAVE_OIDC_CLIENT_ID,
        "oidc_scopes": CHAVE_OIDC_SCOPES,
        "saml_idp_entity_id": CHAVE_SAML_ENTITY_ID,
        "saml_idp_sso_url": CHAVE_SAML_SSO_URL,
        "saml_idp_cert": CHAVE_SAML_CERT,
        "allowed_domains": CHAVE_DOMINIOS,
        "allowed_emails": CHAVE_EMAILS,
    }
    tudo_ok = True
    for nome, chave in mapa.items():
        if nome in campos:
            tudo_ok = set_preference(prefs_path, chave, str(campos[nome] or "")) and tudo_ok
    set_preference(prefs_path, CHAVE_ATUALIZADO_EM, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return tudo_ok


# ---------------------------------------------------------------------------
# O segredo: ambiente, depois arquivo 0600, depois nada
# ---------------------------------------------------------------------------

def caminho_do_segredo(base_dir: str) -> str:
    return os.path.join(base_dir or ".", ARQUIVO_DO_SEGREDO)


def segredo_vem_do_ambiente() -> bool:
    """A tela trava os campos quando o ambiente manda, como já faz com a senha."""
    return bool(os.environ.get("OIDC_CLIENT_SECRET", "").strip())


def resolve_client_secret(arquivo: str) -> str:
    """Ambiente primeiro, depois o arquivo 0600. Vazio significa SSO desligado.

    Espelha `auth.resolve_recovery_hash`: quem opera a variável de ambiente
    controla o segredo por fora, e uma troca pela tela não pode tornar essa
    variável inerte para sempre.
    """
    do_ambiente = os.environ.get("OIDC_CLIENT_SECRET", "").strip()
    if do_ambiente:
        return do_ambiente

    if arquivo and os.path.exists(arquivo):
        try:
            with open(arquivo, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            # Sem permissão, disco cheio, arquivo removido no meio do caminho:
            # tratado como "não há segredo", que é o estado em que o SSO fica
            # desligado -- e não ligado sem credencial.
            return ""
    return ""


def tem_client_secret(arquivo: str) -> bool:
    """Se existe segredo. É o ÚNICO jeito de a tela saber disso: nunca o valor."""
    return bool(resolve_client_secret(arquivo))


def grava_client_secret(arquivo: str, valor: str) -> bool:
    """Grava o segredo com permissão 0600, no molde de `auth.ensure_recovery_hash`."""
    if not arquivo or not valor:
        return False
    try:
        os.makedirs(os.path.dirname(arquivo) or ".", exist_ok=True)
        # 0600: só o dono do processo lê o segredo do cliente OAuth.
        fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(valor)
        return True
    except OSError:
        # Sem disco gravável o SSO não liga. Não existe "ligar sem segredo".
        return False


# ---------------------------------------------------------------------------
# Estado ligado/desligado
# ---------------------------------------------------------------------------

def allowlist_esta_vazia(config: Dict[str, str]) -> bool:
    """Allowlist vazia é "toda conta do provedor entra". O painel recusa ligar assim."""
    return not (
        _lista(config.get("allowed_domains", "")) or _lista(config.get("allowed_emails", ""))
    )


def oidc_esta_completo(config: Dict[str, str], arquivo_do_segredo: str) -> bool:
    """Tudo o que o fluxo precisa, inclusive a allowlist e o segredo."""
    if not all(
        config.get(campo)
        for campo in ("base_url", "oidc_issuer", "oidc_client_id")
    ):
        return False
    if allowlist_esta_vazia(config):
        return False
    return tem_client_secret(arquivo_do_segredo)


def provedor_ativo(config: Dict[str, str], arquivo_do_segredo: str) -> str:
    """Qual provedor responde hoje: "oidc" ou "" (desligado).

    UM provedor por vez, nunca os dois: com dois issuers legítimos a comparar,
    a resposta de um pode ser aceita como se fosse do outro (mix-up). Como só
    há um, `iss` tem um único valor esperado.
    """
    if desligado_pelo_ambiente():
        return ""
    if (config.get("enabled") or "") != "oidc":
        return ""
    if not oidc_esta_completo(config, arquivo_do_segredo):
        return ""
    return "oidc"


def nome_do_provedor(config: Dict[str, str]) -> str:
    """Como o botão se chama na tela: o host do issuer, que é o que o dono reconhece."""
    issuer = config.get("oidc_issuer") or ""
    try:
        host = urllib.parse.urlparse(issuer).netloc
    except ValueError:
        host = ""
    return host or issuer or "SSO"


def redirect_uri(config: Dict[str, str]) -> str:
    """SEMPRE da configuração, JAMAIS do cabeçalho `Host`.

    `Host` é escolhido pelo cliente. Derivar dali a URL de retorno é a
    definição de redirect_uri aberto: o atacante manda o código de autorização
    para onde quiser.
    """
    return (config.get("base_url") or "").rstrip("/") + CAMINHO_DO_CALLBACK


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_json(
    url: str,
    dados: Optional[bytes] = None,
    cabecalhos: Optional[Dict[str, str]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Única saída de rede deste módulo.

    É uma função de módulo, e não código embutido em cada passo, para que o
    teste possa substituí-la por uma falsa e exercitar TODOS os caminhos de
    recusa sem depender de provedor nenhum.

    Sem `context=`: o contexto TLS padrão do Python verifica o certificado, e a
    imagem carrega 119 CAs. Passar um contexto próprio aqui seria a chance de
    alguém desligar a verificação sem que ninguém reparasse.
    """
    pedido = urllib.request.Request(url, data=dados, headers=cabecalhos or {})
    with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def _exige_transporte_seguro(url: str) -> None:
    """Fora do loopback, só HTTPS. No loopback não há rede para escutar."""
    partes = urllib.parse.urlparse(url)
    if partes.scheme == "https":
        return
    if partes.scheme == "http" and partes.hostname in HOSTS_DE_LOOPBACK:
        return
    raise ErroDeSSO(f"transporte inseguro: {partes.scheme}://{partes.netloc}")


def descobre(issuer: str, agora: Optional[float] = None) -> Dict[str, Any]:
    """Documento de descoberta do provedor, com cache por processo.

    O campo `issuer` do documento tem de ser IDÊNTICO ao configurado. É a
    defesa contra mix-up: sem essa conferência, um documento servido por outro
    provedor apontaria os nossos passos seguintes para os endpoints dele.
    """
    agora = agora if agora is not None else time.time()
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        raise ErroDeSSO("issuer nao configurado")

    with _trava:
        registro = _cache_de_descoberta.get(issuer)
    if registro:
        quando, documento = registro
        vencimento = (
            TTL_DESCOBERTA_EM_SEGUNDOS if documento else TTL_FALHA_DE_DESCOBERTA_EM_SEGUNDOS
        )
        if agora - quando < vencimento:
            if documento is None:
                raise ErroDeSSO("descoberta falhou ha pouco (cache negativo)")
            return documento

    try:
        _exige_transporte_seguro(issuer)
        documento = _http_json(issuer + "/.well-known/openid-configuration", timeout=5.0)
        if str(documento.get("issuer", "")).rstrip("/") != issuer:
            raise ErroDeSSO("o issuer do documento nao e o configurado")
        for campo in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
            valor = documento.get(campo)
            if not valor:
                raise ErroDeSSO(f"documento de descoberta sem {campo}")
            _exige_transporte_seguro(str(valor))
    except Exception as erro:
        with _trava:
            _cache_de_descoberta[issuer] = (agora, None)
        if isinstance(erro, ErroDeSSO):
            raise
        raise ErroDeSSO(f"descoberta falhou: {type(erro).__name__}")

    with _trava:
        _cache_de_descoberta[issuer] = (agora, documento)
    return documento


def esquece_descoberta() -> None:
    """Descarta o cache. Chamado quando a configuração muda."""
    with _trava:
        _cache_de_descoberta.clear()


# ---------------------------------------------------------------------------
# Passo 1: ida ao provedor
# ---------------------------------------------------------------------------

def _base64url(bruto: bytes) -> str:
    return base64.urlsafe_b64encode(bruto).decode("ascii").rstrip("=")


def novo_desafio_pkce() -> Tuple[str, str]:
    """Devolve (verificador, desafio) com S256. Nunca "plain".

    O painel roda em HTTP no loopback: o código de autorização passa pela barra
    de endereços e fica no histórico. `state` e `nonce` não protegem contra um
    código interceptado -- o verificador, que só existe no cookie de estado e é
    consumido, protege. Atrás de túnel HTTPS o risco cai, mas a decisão não muda
    por ambiente.
    """
    verificador = secrets.token_urlsafe(64)
    desafio = _base64url(hashlib.sha256(verificador.encode("ascii")).digest())
    return verificador, desafio


def url_de_autorizacao(
    config: Dict[str, str], documento: Dict[str, Any], state: str, nonce: str, desafio: str
) -> str:
    parametros = {
        "response_type": "code",
        "client_id": config["oidc_client_id"],
        "redirect_uri": redirect_uri(config),
        "scope": config.get("oidc_scopes") or ESCOPOS_PADRAO,
        "state": state,
        "nonce": nonce,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        # Sem isto, quem já está logado no provedor com OUTRA conta entra com
        # ela sem perceber, e a tela não oferece caminho para trocar.
        "prompt": "select_account",
    }
    separador = "&" if "?" in str(documento["authorization_endpoint"]) else "?"
    return f"{documento['authorization_endpoint']}{separador}{urllib.parse.urlencode(parametros)}"


# ---------------------------------------------------------------------------
# Passo 2: volta do provedor
# ---------------------------------------------------------------------------

def _limpa_estados_consumidos(agora: float) -> None:
    vencidos = [
        s for s, quando in _estados_consumidos.items()
        if agora - quando > 2 * 600
    ]
    for s in vencidos:
        _estados_consumidos.pop(s, None)
    # Teto duro: se ainda sobrar gente demais, sai o mais antigo.
    while len(_estados_consumidos) > MAXIMO_DE_ESTADOS_CONSUMIDOS:
        mais_antigo = min(_estados_consumidos, key=_estados_consumidos.get)
        _estados_consumidos.pop(mais_antigo, None)


def consome_estado(state: str, agora: Optional[float] = None) -> bool:
    """Marca um `state` como usado. Devolve False se já tinha sido.

    Apagar o cookie não basta: o cookie vive no navegador, e quem capturou o
    par (cookie, código) poderia reapresentá-lo. Uso único de verdade precisa
    de memória do lado do servidor -- mesmo molde dos desafios de
    `protecao.py`.
    """
    agora = agora if agora is not None else time.time()
    with _trava:
        _limpa_estados_consumidos(agora)
        if state in _estados_consumidos:
            return False
        _estados_consumidos[state] = agora
        return True


def troca_codigo_por_tokens(
    config: Dict[str, str], documento: Dict[str, Any], codigo: str, verificador: str, segredo: str
) -> Dict[str, Any]:
    """Troca o código pelo par de tokens, com `client_secret_basic` e PKCE."""
    endpoint = str(documento["token_endpoint"])
    _exige_transporte_seguro(endpoint)
    corpo = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": codigo,
        # O MESMO redirect_uri da ida, e vindo da configuração nos dois casos.
        "redirect_uri": redirect_uri(config),
        "code_verifier": verificador,
    }).encode("ascii")
    credencial = base64.b64encode(
        f"{urllib.parse.quote(config['oidc_client_id'])}:{urllib.parse.quote(segredo)}".encode("utf-8")
    ).decode("ascii")
    return _http_json(
        endpoint,
        dados=corpo,
        cabecalhos={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credencial}",
            "Accept": "application/json",
        },
        timeout=10.0,
    )


def payload_do_id_token(id_token: str) -> Dict[str, Any]:
    """Decodifica o corpo do JWT SEM verificar assinatura (ver o topo do módulo)."""
    partes = str(id_token or "").split(".")
    if len(partes) != 3:
        raise ErroDeSSO("id_token nao tem tres partes")
    corpo = partes[1]
    corpo += "=" * (-len(corpo) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(corpo.encode("ascii")).decode("utf-8"))
    except Exception:
        raise ErroDeSSO("corpo do id_token ilegivel")


def confere_id_token(
    payload: Dict[str, Any],
    config: Dict[str, str],
    nonce_esperado: str,
    agora: Optional[float] = None,
) -> None:
    """Checklist fechado. Com o TLS resolvendo a origem, é ISTO que resta conferir."""
    agora = agora if agora is not None else time.time()
    client_id = config["oidc_client_id"]

    if str(payload.get("iss", "")).rstrip("/") != config["oidc_issuer"]:
        raise ErroDeSSO("iss diferente do issuer configurado")

    aud = payload.get("aud")
    audiencias = [aud] if isinstance(aud, str) else list(aud or [])
    if client_id not in audiencias:
        raise ErroDeSSO("aud nao contem o client_id")
    if len(audiencias) > 1 and str(payload.get("azp", "")) != client_id:
        raise ErroDeSSO("varias audiencias e azp diferente do client_id")

    try:
        exp = float(payload.get("exp"))
    except (TypeError, ValueError):
        raise ErroDeSSO("exp ausente ou ilegivel")
    if exp <= agora:
        raise ErroDeSSO("id_token vencido")

    try:
        iat = float(payload.get("iat"))
    except (TypeError, ValueError):
        raise ErroDeSSO("iat ausente ou ilegivel")
    if abs(agora - iat) > TOLERANCIA_DE_RELOGIO_EM_SEGUNDOS:
        raise ErroDeSSO("iat fora da tolerancia de relogio")

    if not hmac.compare_digest(str(payload.get("nonce", "")), nonce_esperado):
        raise ErroDeSSO("nonce diferente do cookie de estado")


def le_userinfo(documento: Dict[str, Any], access_token: str) -> Dict[str, Any]:
    endpoint = str(documento["userinfo_endpoint"])
    _exige_transporte_seguro(endpoint)
    return _http_json(
        endpoint,
        cabecalhos={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=10.0,
    )


def _lista(bruto: str) -> set:
    return {p.strip().lower().lstrip("@") for p in (bruto or "").split(",") if p.strip()}


def email_permitido(email: str, config: Dict[str, str]) -> bool:
    """Allowlist obrigatória: e-mail exato OU domínio.

    Sem ela, "entrar com o Google" significa que toda conta Google do planeta
    entra no painel.
    """
    endereco = (email or "").strip().lower()
    if not endereco or "@" not in endereco:
        return False
    if endereco in _lista(config.get("allowed_emails", "")):
        return True
    return endereco.rsplit("@", 1)[1] in _lista(config.get("allowed_domains", ""))


def conclui_callback(
    config: Dict[str, str],
    arquivo_do_segredo: str,
    state_da_query: str,
    codigo: str,
    erro_da_query: str,
    cookie_de_estado: str,
    agora: Optional[float] = None,
) -> str:
    """Roda a validação inteira e devolve o e-mail autorizado, ou levanta ErroDeSSO.

    A ordem é a da especificação e para na PRIMEIRA falha. Todas as falhas
    chegam à tela com a mesma mensagem: o detalhe fica aqui, para o log.
    """
    agora = agora if agora is not None else time.time()

    segredo = resolve_client_secret(arquivo_do_segredo)
    if not segredo:
        raise ErroDeSSO("sem client_secret: o SSO esta desligado")

    # 2. Cookie de estado presente e íntegro.
    estado = None
    from . import sessao  # import local: evita ciclo entre os dois módulos
    if cookie_de_estado:
        estado = sessao.ler_estado_sso(cookie_de_estado, agora=agora)
    if not estado:
        raise ErroDeSSO("cookie de estado ausente ou invalido")
    state_do_cookie, nonce_do_cookie, verificador = estado

    # 3. O `state` da query tem de ser o do cookie, e de uso único.
    if not state_da_query or not hmac.compare_digest(state_da_query, state_do_cookie):
        raise ErroDeSSO("state da query diferente do cookie")
    if not consome_estado(state_do_cookie, agora=agora):
        raise ErroDeSSO("state ja consumido (reapresentacao)")

    # 4. Erro declarado pelo provedor, e código presente.
    if erro_da_query:
        raise ErroDeSSO("o provedor devolveu erro")
    if not codigo:
        raise ErroDeSSO("code ausente")

    documento = descobre(config["oidc_issuer"], agora=agora)

    # 5. Troca do código.
    tokens = troca_codigo_por_tokens(config, documento, codigo, verificador, segredo)
    id_token = str(tokens.get("id_token") or "")
    access_token = str(tokens.get("access_token") or "")
    if not id_token or not access_token:
        raise ErroDeSSO("resposta do token_endpoint incompleta")

    # 6. Conferência do payload do id_token.
    payload = payload_do_id_token(id_token)
    confere_id_token(payload, config, nonce_do_cookie, agora=agora)

    # 7. userinfo, e o `sub` tem de ser o mesmo (OIDC Core 5.3.2).
    informacoes = le_userinfo(documento, access_token)
    sub_do_token = str(payload.get("sub") or "")
    if not sub_do_token or str(informacoes.get("sub") or "") != sub_do_token:
        raise ErroDeSSO("sub do userinfo diferente do sub do id_token")

    # 8. E-mail confirmado pelo provedor.
    email = str(informacoes.get("email") or payload.get("email") or "").strip()
    verificado = informacoes.get("email_verified", payload.get("email_verified"))
    if verificado is not True and str(verificado).lower() != "true":
        raise ErroDeSSO("email_verified ausente ou falso")

    # 9. Allowlist, conferida de novo aqui: a tela recusa gravar uma lista
    # vazia, e o fluxo recusa usar uma. As duas guardas são de propósito.
    if allowlist_esta_vazia(config):
        raise ErroDeSSO("allowlist vazia")
    if not email_permitido(email, config):
        raise ErroDeSSO("email fora da allowlist")

    return email.lower()
