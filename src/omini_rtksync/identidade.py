"""Identidade deste produto: a ÚNICA fronteira entre os três painéis irmãos.

9RTKSync, OminiRTKSync e LiteLlmRTKSync são clones em código. O que muda entre
eles são cores, nome, logo e a quem cada um se conecta — e nada mais. Este
arquivo é o lugar onde essa diferença mora. Depois dele, nenhum outro módulo
comum pode escrever uma cor em hexadecimal, o nome do produto ou o nome do
gateway à mão: quem precisar, importa daqui.

Regra dura: este módulo NÃO importa nada de dentro do pacote. Ele é importado
por todos os outros, e um único import interno aqui criaria ciclo.

O contrato é fechado: são estes nomes, nem um a mais. Um nome extra é um
componente que só um painel sabe desenhar, e a simetria acaba ali. O guarda
está em tests/test_identidade_visual.py.
"""

# --------------------------------------------------------------------------
# Nomes
# --------------------------------------------------------------------------

# Nome comercial, como aparece na aba do navegador e no cabeçalho do painel.
NOME_DO_PRODUTO = "OminiRTKSync"

# Gateway que este sincronizador atende.
NOME_DO_GATEWAY = "OmniRoute"

# Slug minúsculo do gateway. Quem emite a chave virtual é o próprio gateway: a
# coluna "Provedor" da tabela de chaves não tem outro valor possível.
PROVEDOR_DO_GATEWAY = "omniroute"

# Prefixo dos contêineres da stack deste produto.
PREFIXO_DE_CONTAINER = "ominirtk-"

# --------------------------------------------------------------------------
# Portas publicadas no host. O valor efetivo continua vindo do ambiente
# (config.py); aqui fica só o padrão, para não haver duas fontes de verdade.
# --------------------------------------------------------------------------

PORTA_DO_PAINEL = 8082
PORTA_DE_METRICAS = 9092

# --------------------------------------------------------------------------
# Marca
# --------------------------------------------------------------------------

# Ícone do Bootstrap Icons que identifica o produto no cabeçalho.
ICONE_DO_PRODUTO = "bi-signpost-split-fill"

# Desenho do ícone da aba, em markup SVG. Fica aqui inteiro porque o LiteLlm usa
# dois <path> e os outros dois usam um só: guardar apenas o atributo `d` faria o
# template do favicon deixar de ser o mesmo texto nos três.
GLIFO_DO_FAVICON = (
    "<path d='M7 16h2V6h5a1 1 0 0 0 .8-.4l.975-1.3a.5.5 0 0 0 0-.6L14.8 2.4A1 1 0 0 0 14 2H9v-.586a1 1 0"
    " 0 0-2 0V7H2a1 1 0 0 0-.8.4L.225 8.7a.5.5 0 0 0 0 .6l.975 1.3a1 1 0 0 0 .8.4h5z'/>"
)

# Fundo do quadrado do favicon, já escapado para caber numa data URI. É o token
# --surface, e não a cor-base: sobre o --bg o glifo branco some.
COR_DO_FAVICON = "%23310a5c"

# --------------------------------------------------------------------------
# Paleta
# --------------------------------------------------------------------------

# Tokens de PAPEL: existem nos três painéis, com valores diferentes em cada um.
# A ordem é a ordem em que o :root os declara — ordem diferente é diff puro.
# O que NÃO está aqui (--text e os doze --bs-*) é estrutural: mesmo valor nos
# três, e por isso fica em render.py.
PALETA = {
    "--bg": "#240046",          # fundo da pagina
    "--surface": "#310a5c",     # cartao
    "--surface-2": "#3d1270",   # cabecalho de cartao, chip
    "--line": "#4d1d88",        # borda
    "--accent": "#b57bff",      # acao primaria
    "--accent-2": "#d2aaff",    # acao secundaria, realce
    "--brand-a": "#7a2fd6",     # marca, inicio do gradiente
    "--brand-b": "#b57bff",     # marca, fim do gradiente
    "--text-dim": "#b9a6d4",    # texto secundario
}

# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------

# Os três painéis podem ser abertos no mesmo navegador, no mesmo host, em portas
# diferentes — e cookie não se separa por porta. Sem o nome do produto no nome
# do cookie, entrar num painel derrubaria a sessão dos outros dois.
NOME_DO_COOKIE = "ominirtksync_sessao"
NOME_DO_COOKIE_DE_ESTADO = "ominirtksync_estado_sso"
