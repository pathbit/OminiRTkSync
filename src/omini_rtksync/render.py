"""Renderização server-side do dashboard do OminiRTKSync.

Todo o HTML é montado aqui, no servidor, com os dados já embutidos. O navegador
nunca consulta o banco: ele recebe a página pronta. Isso mantém o SQLite
inteiramente do lado do servidor e faz o painel funcionar mesmo com JavaScript
desabilitado — o jQuery serve só para conforto.

Ícones: Bootstrap Icons e flag-icons (fontes/CSS de ícones), nunca emoji.
Idioma padrão: inglês, com português e espanhol no seletor de bandeiras.
"""

import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .i18n import DEFAULT_LANGUAGE, LANGUAGES, normalize_language, translate

# Icone da aba, embutido como data URI: /favicon.ico responde 401 atras do
# Basic Auth, entao um arquivo servido deixaria a aba sem icone ate o
# operador autenticar -- e a pagina de erro nunca teria icone nenhum.
FAVICON = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%23310a5c'/><g transform='translate(6 6) scale(1.25)' fill='%23ffffff'><path d='M7 16h2V6h5a1 1 0 0 0 .8-.4l.975-1.3a.5.5 0 0 0 0-.6L14.8 2.4A1 1 0 0 0 14 2H9v-.586a1 1 0 0 0-2 0V7H2a1 1 0 0 0-.8.4L.225 8.7a.5.5 0 0 0 0 .6l.975 1.3a1 1 0 0 0 .8.4h5z'/></g></svg>"

BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
BOOTSTRAP_ICONS = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"
FLAG_ICONS = "https://cdn.jsdelivr.net/npm/flag-icons@7.2.3/css/flag-icons.min.css"
BOOTSTRAP_JS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
JQUERY_JS = "https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js"
# Tipografia: Google Fonts, com pilha de sistema como reserva se o CDN cair.
GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
)
FONT_STACK = "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
MONO_STACK = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

# Estado semântico -> (classe do badge, ícone)
HEALTH_PRESENTATION = {
    "active": ("text-bg-success", "bi-check-circle-fill"),
    "expiring_soon": ("text-bg-warning", "bi-hourglass-split"),
    "expired": ("text-bg-danger", "bi-x-octagon-fill"),
    "rate_limited": ("text-bg-warning", "bi-pause-circle-fill"),
    "no_expiration": ("text-bg-secondary", "bi-infinity"),
    "unknown": ("text-bg-secondary", "bi-question-circle-fill"),
    # Estados vindos da validação viva da credencial.
    "invalid": ("text-bg-danger", "bi-shield-exclamation"),
    "unreachable": ("text-bg-warning", "bi-plug"),
    "not_checked": ("text-bg-secondary", "bi-dash-circle"),
}

# Quem emite a chave virtual e o proprio gateway: a coluna "Provedor" da tabela
# de chaves nao tem outro valor possivel, e deixa-la vazia quebraria a leitura
# das sete colunas que os tres paineis compartilham.
PROVEDOR_DO_GATEWAY = "omniroute"

# Teto de linhas da tabela de modelos. Com OpenRouter ligado o catalogo do
# gateway passa de quinhentas entradas, e cada linha carrega um modal: a pagina
# inteira iria a quase um megabyte de HTML. O total continua no cabecalho do
# cartao e o rodape declara quantas ficaram de fora.
MAX_LINHAS_DE_MODELO = 150


def esc(value: Any) -> str:
    """Escapa qualquer valor para inserção segura no HTML."""
    return html.escape(str(value if value is not None else ""), quote=True)


def format_duration(seconds: Optional[int], lang: str = DEFAULT_LANGUAGE) -> str:
    """Formata uma duração em segundos de forma legível."""
    if seconds is None:
        return translate("duration.unlimited", lang)
    if seconds <= 0:
        return translate("duration.expired", lang)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rest = minutes % 60
    if hours < 24:
        return f"{hours}h {rest:02d}min"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def format_timestamp(value: Optional[str]) -> str:
    """Normaliza um timestamp ISO para exibição.

    Troca APENAS o "T" que separa data de hora, e não todo "T" da string. A
    versão anterior fazia `.replace("T", " ")` no texto inteiro, o que a tornava
    destrutiva ao ser aplicada duas vezes: a primeira passada produzia
    "2026-09-13 19:08:48 UTC", e a segunda comia o "T" de "UTC" e escrevia
    "19:08:48 U C" na tela. Um defeito que só aparece quando alguém formata um
    valor já formatado -- e isso é fácil de acontecer sem ninguém notar.
    """
    if not value:
        return "—"
    texto = str(value)
    if texto.endswith("Z"):
        texto = texto[:-1] + " UTC"
    # O separador ISO é o "T" na posição 10 (AAAA-MM-DDTHH:MM:SS).
    if len(texto) > 10 and texto[10] == "T":
        texto = texto[:10] + " " + texto[11:]
    return texto


def render_refresh_reason(conn: Any, refresh_margin: int, lang: str = DEFAULT_LANGUAGE) -> str:
    """Explica, em uma frase, por que a conexão foi ou não renovada.

    Sem isso o painel mostra apenas "0 renovadas" e não há como distinguir
    "nada precisava ser renovado" de "a renovação falhou".
    """
    if conn.is_local:
        # Quem diz se a instância respondeu é a sonda, não o tamanho do
        # catálogo: uma instalação nova, de pé e sem nenhum modelo baixado,
        # devolve lista vazia com HTTP 200. Contar modelos aqui a anunciava
        # como inalcançável, contradizendo o "ativa" que o próprio ciclo
        # acabara de gravar no banco.
        if conn.data.get("testStatus") == "unreachable":
            return translate("reason.local_unreachable", lang)
        models = conn.local_models
        if models:
            return translate("reason.local_ok", lang, count=len(models))
        return translate("reason.local_empty", lang)

    if not conn.is_oauth:
        return translate("reason.api_key", lang)

    remaining = conn.remaining_seconds
    if remaining is None:
        return translate("reason.no_expiry", lang)
    if remaining <= 0:
        return translate("reason.expired", lang)

    margin_min = max(1, refresh_margin // 60)
    if remaining <= refresh_margin:
        return translate("reason.inside_margin", lang, margin=margin_min)
    return translate(
        "reason.outside_margin",
        lang,
        margin=margin_min,
        eta=format_duration(remaining - refresh_margin, lang),
    )


def render_last_refresh(conn: Any, lang: str) -> str:
    """Mostra quando a credencial foi renovada pela ultima vez, e ha quanto tempo."""
    stamp = conn.last_refresh_at
    if not stamp:
        return f'<span class="text-secondary">{esc(translate("table.never_refreshed", lang))}</span>'

    ago = ""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        elapsed = int((datetime.now(timezone.utc) - moment).total_seconds())
        if elapsed >= 0:
            ago = translate("table.time_ago", lang, elapsed=format_duration(elapsed, lang))
    except (ValueError, TypeError):
        # Carimbo de tempo em formato desconhecido vira "sem informacao" na
        # tela. Uma data ilegivel nao pode derrubar a renderizacao da pagina.
        pass

    icon = '<i class="bi bi-arrow-repeat me-1 text-success" aria-hidden="true"></i>'
    detail = f'<div class="text-secondary">{esc(ago)}</div>' if ago else ""
    return f'{icon}<span class="font-monospace">{esc(format_timestamp_curto(stamp))}</span>{detail}'


def render_remaining(conn: Any, lang: str, curto: bool = False) -> str:
    """Validade restante, sem chamar de ilimitado o que so esta faltando.

    Um token OAuth sempre expira. Quando nao ha expiresAt legivel, isso e dado
    ausente -- normalmente porque o gateway gravou a validade num formato que
    nao soube reler -- e nao uma credencial eterna. So chave estatica pode ser
    apresentada como sem expiracao.
    """
    remaining = conn.remaining_seconds
    if remaining is not None:
        return esc(format_duration(remaining, lang))

    if conn.is_oauth:
        return (
            '<span class="text-warning d-inline-flex align-items-center gap-1">'
            '<i class="bi bi-exclamation-triangle" aria-hidden="true"></i>'
            f'{esc(translate("duration.unknown_expiry", lang))}</span>'
        )
    # Na celula cabe o fato; a razao ("chave estatica") fica no modal.
    chave = "duration.no_expiry_short" if curto else "duration.no_expiry"
    return f'<span class="text-secondary">{esc(translate(chave, lang))}</span>'


def format_timestamp_curto(value: Optional[str]) -> str:
    """Data enxuta para a celula da tabela: dia/mes e hora, sem ano nem segundos.

    A forma completa ("2026-09-13 18:40:52 UTC") nao cabe na coluna e era
    cortada no meio, o que deixava a informacao pior do que util. O carimbo
    inteiro continua no modal de detalhe, a um clique da linha.
    """
    if not value:
        return "—"
    try:
        momento = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return format_timestamp(value)
    return momento.strftime("%d/%m %H:%M")


def render_notice_page(
    title: str, body: str, link_label: str = "", refresh_url: str = ""
) -> bytes:
    """Pagina autonoma para respostas fora do painel autenticado.

    E o que o navegador exibe quando o usuario aperta ESC no dialogo do Basic
    Auth, entao nao pode conter nem credencial nem dica de credencial.

    `refresh_url` instala um `<meta http-equiv="refresh">`. E o pouso do SSO:
    responder 302 dali nao funciona, porque numa cadeia de redirecionamento
    iniciada por outro site o Chrome nao envia o cookie de sessao
    `SameSite=Strict` no salto seguinte -- o operador cairia em `/login` com um
    cookie valido no bolso. Uma pagina de verdade, com refresh, quebra a cadeia:
    o salto seguinte e navegacao de mesma origem.
    """
    link = (
        f'<p><a href="/">{esc(link_label)}</a></p>' if link_label else ""
    )
    refresh = (
        f'<meta http-equiv="refresh" content="0;url={esc(refresh_url)}">' if refresh_url else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  {refresh}
  <link rel="icon" href="{FAVICON}">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>body {{ background: #240046; }}</style>
</head>
<body class="d-flex align-items-center justify-content-center" style="min-height:100vh">
  <div class="card text-center" style="max-width:34rem">
    <div class="card-body p-4">
      <i class="bi bi-shield-lock fs-1 text-secondary d-block mb-3" aria-hidden="true"></i>
      <h1 class="h5 mb-3">{esc(title)}</h1>
      <p class="text-secondary mb-3">{esc(body)}</p>
      {link}
    </div>
  </div>
</body>
</html>""".encode("utf-8")


def egress_chip(conn: Any, sharing_count: int, lang: str) -> str:
    """Marca de saída de rede da conexão, em modo somente leitura.

    O risco de bloqueio não vem de várias sessões na mesma conta -- isso os
    provedores aceitam -- e sim de várias contas saindo pelo mesmo endereço.
    Por isso "compartilhada" só vira aviso a partir da segunda conta nessa
    situação: sozinha, ela é a única dona daquele IP.
    """
    estado = conn.egress_status
    if estado == "bound":
        pool = conn.egress_binding or "?"
        return (
            '<span class="badge bg-success-subtle text-success-emphasis">'
            f'<i class="bi bi-shield-check me-1" aria-hidden="true"></i>'
            f'{esc(translate("egress.bound", lang))}: {esc(pool)}</span>'
        )
    if estado == "shared":
        if sharing_count > 1:
            return (
                '<span class="badge bg-warning-subtle text-warning-emphasis">'
                f'<i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>'
                f'{esc(translate("egress.shared", lang, count=sharing_count))}</span>'
            )
        return (
            '<span class="text-secondary">'
            f'<i class="bi bi-diagram-3 me-1" aria-hidden="true"></i>'
            f'{esc(translate("egress.single", lang))}</span>'
        )
    return (
        '<span class="text-secondary">'
        f'<i class="bi bi-question-circle me-1" aria-hidden="true"></i>'
        f'{esc(translate("egress.unknown", lang))}</span>'
    )


def render_login_page(
    lang: str = DEFAULT_LANGUAGE,
    erro: str = "",
    desafio: str = "",
    dificuldade: int = 4,
    sso_nome: str = "",
    sso_indisponivel: bool = False,
) -> bytes:
    """Formulario de entrada, com a mesma casca e a mesma paleta do painel.

    Existe porque o dialogo do Basic Auth e uma janela do NAVEGADOR: nao se
    traduz, nao se estiliza, nao oferece logout e nao e HTML -- qualquer
    ferramenta que dirija um navegador para no dialogo, porque nao ha nada na
    pagina para preencher. Esta pagina resolve os quatro de uma vez.
    """
    lang = normalize_language(lang)
    aviso = (
        f'<div class="alert alert-danger d-flex align-items-center gap-2 mb-3" role="alert">'
        f'<i class="bi bi-exclamation-octagon-fill" aria-hidden="true"></i>'
        f'<span>{esc(erro)}</span></div>'
        if erro
        else ""
    )
    desafio_html = (
        f'<input type="hidden" name="desafio" value="{esc(desafio)}">'
        f'<input type="hidden" name="resposta" id="resposta" value="">'
        f'<p class="text-secondary small d-flex align-items-center gap-2" id="aviso-desafio">'
        f'<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>'
        f'{esc(translate("auth.challenge", lang))}</p>'
        f'<script>'
        f'(async () => {{'
        f'  const desafio = {desafio!r};'
        f'  const alvo = "0".repeat({dificuldade});'
        f'  const cod = new TextEncoder();'
        f'  for (let n = 0; n < 20000000; n++) {{'
        f'    const buf = await crypto.subtle.digest("SHA-256", cod.encode(desafio + n));'
        f'    const hex = [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");'
        f'    if (hex.startsWith(alvo)) {{'
        f'      document.getElementById("resposta").value = String(n);'
        f'      document.getElementById("aviso-desafio").remove();'
        f'      break;'
        f'    }}'
        f'  }}'
        f'}})();'
        f'</script>'
        if desafio
        else ""
    )
    # O botao e um <a>, NUNCA um <form>: a CSP do painel declara
    # `form-action 'self'` e o navegador bloqueia, sem erro visivel na tela, a
    # submissao de um formulario que redireciona para fora.
    #
    # O formulario de senha continua acima dele em qualquer configuracao. SSO e
    # uma segunda porta, nunca a unica: se o provedor cair e o formulario tiver
    # saido da tela, ninguem entra.
    sso_html = ""
    if sso_nome:
        sso_html = (
            '<div class="text-center text-secondary small my-3">&middot; &middot; &middot;</div>'
            f'<a class="btn btn-outline-light w-100" href="/sso/oidc/iniciar">'
            f'<i class="bi bi-box-arrow-in-right me-1" aria-hidden="true"></i>'
            f'{esc(translate("sso.sign_in_with", lang, provider=sso_nome))}</a>'
        )
    elif sso_indisponivel:
        sso_html = (
            '<p class="text-secondary small mt-3 mb-0 d-flex align-items-start gap-2">'
            '<i class="bi bi-plug" aria-hidden="true"></i>'
            f'<span>{esc(translate("sso.unavailable", lang))}</span></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>OminiRTKSync</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <style>
    :root {{ --bg: #240046; --surface: #310a5c; --line: #4d1d88;
             --accent: #b57bff; --text: #e6e8ee; }}
    body {{ background: var(--bg); color: var(--text); font-family: {FONT_STACK}; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); }}
    .btn-primary {{ --bs-btn-bg: var(--accent); --bs-btn-border-color: var(--accent);
                    --bs-btn-color: var(--bg); --bs-btn-hover-bg: var(--accent);
                    --bs-btn-hover-border-color: var(--accent); --bs-btn-hover-color: var(--bg); }}
    .form-control {{ background: var(--bg); border-color: var(--line); color: var(--text); }}
    .form-control:focus {{ background: var(--bg); color: var(--text);
                           border-color: var(--accent); box-shadow: none; }}
  </style>
</head>
<body class="d-flex align-items-center justify-content-center" style="min-height:100vh">
  <main class="card" style="max-width:24rem;width:100%">
    <div class="card-body p-4">
      <h1 class="h5 mb-1 d-flex align-items-center gap-2">
        <i class="bi bi-shield-lock" aria-hidden="true"></i>OminiRTKSync
      </h1>
      <p class="text-secondary small mb-4">{esc(translate("auth.login_intro", lang))}</p>
      {aviso}
      <form method="post" action="/login">
        <div class="mb-3">
          <label class="form-label small" for="usuario">{esc(translate("auth.user", lang))}</label>
          <input class="form-control" id="usuario" name="usuario" autocomplete="username" autofocus required>
        </div>
        <div class="mb-4">
          <label class="form-label small" for="senha">{esc(translate("auth.password", lang))}</label>
          <input class="form-control" id="senha" name="senha" type="password"
                 autocomplete="current-password" required>
        </div>
        {desafio_html}
        <button class="btn btn-primary w-100" type="submit">
          <i class="bi bi-box-arrow-in-right me-1" aria-hidden="true"></i>{esc(translate("auth.enter", lang))}
        </button>
      </form>
      {sso_html}
    </div>
  </main>
</body>
</html>""".encode("utf-8")


def health_badge(status: str, lang: str) -> str:
    """Monta o badge de saúde com ícone de fonte."""
    css, icon = HEALTH_PRESENTATION.get(status, HEALTH_PRESENTATION["unknown"])
    label = translate(f"health.{status}", lang)
    return (
        f'<span class="badge {css} d-inline-flex align-items-center gap-1">'
        f'<i class="bi {icon}" aria-hidden="true"></i>{esc(label)}</span>'
    )


def render_language_switcher(current: str) -> str:
    """Seletor de idioma com bandeiras reais (flag-icons), não emoji."""
    current = normalize_language(current)
    _, current_flag = LANGUAGES[current]
    items = []
    for code, (label, flag) in LANGUAGES.items():
        active = " active" if code == current else ""
        items.append(
            f'<li><button class="dropdown-item d-flex align-items-center gap-2{active}" '
            f'type="submit" name="lang" value="{esc(code)}">'
            f'<span class="fi {esc(flag)}"></span>{esc(label)}</button></li>'
        )
    return f"""
        <form method="post" action="/acoes/idioma" class="m-0 dropdown">
          <button class="btn btn-outline-light btn-sm dropdown-toggle d-inline-flex align-items-center gap-2"
                  type="button" data-bs-toggle="dropdown" aria-expanded="false"
                  aria-label="{esc(translate('language.label', current))}">
            <span class="fi {esc(current_flag)}"></span>
          </button>
          <ul class="dropdown-menu dropdown-menu-end">{"".join(items)}
          </ul>
        </form>"""


def metric_card(label: str, value: Any, icon: str, tone: str) -> str:
    return f"""
      <div class="col-6 col-lg-3">
        <div class="card metric h-100">
          <div class="card-body">
            <div class="d-flex align-items-center gap-2 metric-label">
              <i class="bi {icon} {tone}" aria-hidden="true"></i><span>{esc(label)}</span>
            </div>
            <div class="metric-value {tone}">{esc(value)}</div>
          </div>
        </div>
      </div>"""


def render_security_banner(is_default_password: bool, lang: str) -> str:
    if not is_default_password:
        return ""
    return f"""
      <div class="alert alert-warning d-flex align-items-center justify-content-between gap-3" role="alert">
        <div class="d-flex align-items-start gap-2">
          <i class="bi bi-shield-exclamation fs-5" aria-hidden="true"></i>
          <div><strong>{esc(translate("security.title", lang))}</strong>
            {translate("security.body", lang)}</div>
        </div>
        <button class="btn btn-warning btn-sm text-nowrap" data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key-fill me-1" aria-hidden="true"></i>{esc(translate("action.change_credentials", lang))}
        </button>
      </div>"""



def render_connection_details(conn: Any, refresh_margin: int, sharing_count: int, lang: str) -> str:
    """Modal com o que nao cabe na linha da tabela.

    A tabela existe para varrer muitas conexoes de relance; o diagnostico e uma
    frase inteira, e espremido entre sete colunas ele sobrepunha a coluna
    vizinha. Aqui ele aparece por extenso, junto do resto do estado daquela
    conexao, sem competir com nada.
    """
    linhas = [
        (translate("table.provider", lang), f'<span class="provider-chip">{esc(conn.provider)}</span>'),
        (translate("table.status", lang), health_badge(conn.health_status, lang)),
        (translate("table.remaining", lang), render_remaining(conn, lang)),
        (translate("table.last_refresh", lang), render_last_refresh(conn, lang)),
    ]
    if conn.is_local and conn.base_url:
        linhas.append((translate("table.type", lang),
                       f'<span class="font-monospace">{esc(conn.base_url)}</span>'))
    if not conn.is_local:
        chip = egress_chip(conn, sharing_count, lang)
        if chip:
            linhas.append((translate("egress.title", lang), chip))

    corpo = "".join(
        f'<dt class="col-5 text-secondary fw-normal">{esc(rotulo)}</dt>'
        f'<dd class="col-7 text-end">{valor}</dd>'
        for rotulo, valor in linhas
    )
    modelos = ""
    if conn.is_local and conn.local_models:
        itens = "".join(f'<li class="font-monospace small">{esc(m)}</li>' for m in conn.local_models)
        modelos = (f'<p class="text-secondary small mb-1 mt-3">{esc(translate("table.models", lang))}</p>'
                   f'<ul class="mb-0">{itens}</ul>')

    return f"""
  <div class="modal fade" id="detalhe-{esc(conn.id)}" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-info-circle" aria-hidden="true"></i>{esc(conn.name)}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">
          <dl class="row mb-0 small">{corpo}</dl>
          <p class="text-secondary small mb-1 mt-3">{esc(translate("table.diagnosis", lang))}</p>
          <p class="mb-0">{esc(render_refresh_reason(conn, refresh_margin, lang))}</p>
          {modelos}
        </div>
      </div>
    </div>
  </div>"""


def render_connections_table(connections: List[Any], refresh_margin: int, lang: str) -> str:
    if not connections:
        return estado_vazio(translate("connections.empty", lang))

    # Uma conta sozinha compartilhando nao e problema: ela e a unica dona
    # daquele IP. O alerta comeca na segunda, quando o provedor passa a ver
    # identidades distintas na mesma origem.
    compartilhando = sum(
        1 for c in connections if not c.is_local and c.egress_status == "shared"
    )

    rows = []
    detalhes = []
    for c in connections:
        if c.is_local:
            kind, kind_icon = translate("type.local", lang), "bi-hdd-network"
        elif c.is_oauth:
            kind, kind_icon = translate("type.oauth", lang), "bi-person-badge"
        elif c.has_api_key:
            kind, kind_icon = translate("type.api_key", lang), "bi-key"
        else:
            kind, kind_icon = translate("type.local", lang), "bi-hdd-network"

        # Instancia local: mostra a origem e os modelos que ela realmente serve.
        detail = ""
        if c.is_local:
            models = c.local_models
            parts = []
            if c.base_url:
                parts.append(f'<span class="font-monospace">{esc(c.base_url)}</span>')
            if models:
                preview = ", ".join(models[:3]) + (f" (+{len(models) - 3})" if len(models) > 3 else "")
                parts.append(
                    f'<span class="badge text-bg-dark">{len(models)} '
                    f'{esc(translate("table.models", lang))}</span> {esc(preview)}'
                )
            if parts:
                detail = f'<div class="small text-secondary mt-1">{" · ".join(parts)}</div>'
        else:
            # Saida de rede: somente leitura. Quem roteia a requisicao e o
            # gateway; o painel existe para o operador ver quais contas dividem
            # endereco antes que o provedor veja primeiro.
            chip = egress_chip(c, compartilhando, lang)
            if chip:
                detail = f'<div class="small mt-1">{chip}</div>'

        rows.append(f"""
            <tr>
              <td><span class="provider-chip">{esc(c.provider)}</span></td>
              <td class="fw-semibold">{esc(c.name)}{detail}</td>
              <td class="text-nowrap">
                <i class="bi {kind_icon} me-1 text-secondary" aria-hidden="true"></i>{esc(kind)}
              </td>
              <td>{health_badge(c.health_status, lang)}</td>
              <td class="text-nowrap">{render_remaining(c, lang, curto=True)}</td>
              <td class="text-nowrap small">{render_last_refresh(c, lang)}</td>
              <td class="text-end">{detail_button(f"detalhe-{c.id}", lang)}</td>
            </tr>""")
        detalhes.append(render_connection_details(c, refresh_margin, compartilhando, lang))

    return cabecalho_de_dominio(rows, lang) + "".join(detalhes)


def cabecalho_de_dominio(rows: List[str], lang: str) -> str:
    """A casca das tabelas de dominio: SEMPRE as mesmas sete colunas.

    Conexoes, chaves virtuais e modelos sao coisas diferentes lidas do mesmo
    jeito -- quem serve, como se chama, de que tipo e, como esta, quanto tempo
    resta, quando foi renovado, e o (i) que abre o resto. Uma casca so mantem a
    largura das colunas identica entre os cartoes e entre os tres paineis.
    """
    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0 tabela-dominio">
            <colgroup>
              <col class="c-provedor"><col class="c-nome"><col class="c-tipo">
              <col class="c-status"><col class="c-validade"><col class="c-renovacao">
              <col class="c-detalhe">
            </colgroup>
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.provider", lang))}</th>
                <th scope="col">{esc(translate("table.name", lang))}</th>
                <th scope="col">{esc(translate("table.type", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
                <th scope="col">{esc(translate("table.remaining", lang))}</th>
                <th scope="col">{esc(translate("table.last_refresh", lang))}</th>
                <th scope="col" class="text-end">{esc(translate("table.details", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""


def estado_vazio(mensagem: str) -> str:
    """O bloco de estado vazio da familia: icone bi-inbox e uma frase.

    Os quatro cartoes de tabela usam exatamente este bloco. Cada um deles existe
    nos tres paineis por contrato; quando o gateway deste produto nao tem aquele
    conceito, ou ainda nao tem dado nenhum, o cartao continua na tela e a frase
    diz por que esta vazio AQUI. Assimetria de cartoes e pior que estado vazio.
    """
    return f"""
        <div class="text-center text-secondary py-5">
          <i class="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true"></i>
          {esc(mensagem)}
        </div>"""


def detail_button(modal_id: str, lang: str) -> str:
    """Botao (i) da linha, que abre o modal de detalhe daquele item."""
    return f"""<button class="btn btn-outline-light btn-sm py-0 px-2" type="button"
                        data-bs-toggle="modal" data-bs-target="#{esc(modal_id)}"
                        title="{esc(translate("table.details", lang))}">
                  <i class="bi bi-info-circle" aria-hidden="true"></i>
                </button>"""


def render_detail_modal(modal_id: str, titulo: str, linhas: List[tuple], lang: str,
                        extra: str = "") -> str:
    """Modal de detalhe no formato que a familia usa: titulo, pares e um extra."""
    corpo = "".join(
        f'<dt class="col-5 text-secondary fw-normal">{esc(rotulo)}</dt>'
        f'<dd class="col-7 text-end">{valor}</dd>'
        for rotulo, valor in linhas
    )
    return f"""
  <div class="modal fade" id="{esc(modal_id)}" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-info-circle" aria-hidden="true"></i>{esc(titulo)}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">
          <dl class="row mb-0 small">{corpo}</dl>
          {extra}
        </div>
      </div>
    </div>
  </div>"""


def render_remaining_seconds(remaining: Optional[int], lang: str) -> str:
    """Validade restante de um item que nao e conexao (chave virtual, modelo).

    Sem ``expiresAt`` a chave e estatica: vale ate ser revogada, e isso e
    "sem expiracao" de verdade -- nao o dado ausente que render_remaining trata
    com cautela no caso do OAuth.
    """
    if remaining is not None:
        return esc(format_duration(remaining, lang))
    return f'<span class="text-secondary">{esc(translate("duration.no_expiry_short", lang))}</span>'


def render_timestamp_cell(carimbo: Optional[str], lang: str, icone: str) -> str:
    """Celula de carimbo de tempo curto, com o valor inteiro guardado no modal."""
    if not carimbo:
        return f'<span class="text-secondary">{esc(translate("table.never_refreshed", lang))}</span>'
    return (f'<i class="bi {icone} me-1 text-secondary" aria-hidden="true"></i>'
            f'<span class="font-monospace">{esc(format_timestamp_curto(carimbo))}</span>')


def key_state_label(key: Any, lang: str) -> str:
    """Por qual caminho a chave foi recusada -- ou que ela segue aceita.

    A celula da tabela diz "recusada" nos tres casos, porque o efeito e o mesmo.
    Qual deles foi so cabe aqui, no modal.
    """
    dados = key.data
    if dados.get("isBanned"):
        return translate("keys.banned", lang)
    if dados.get("revokedAt"):
        return translate("keys.revoked", lang)
    if dados.get("isActive") is False:
        return translate("keys.disabled", lang)
    return translate("keys.enabled", lang)


def render_key_details(key: Any, modal_id: str, lang: str) -> str:
    """Modal com o que nao cabe na linha da chave virtual.

    Escopos, modo de acesso, tetos de requisicao e o instante exato de emissao
    sao diagnostico: espremidos na tabela empurrariam as colunas uteis para fora
    da tela. O TOKEN nunca entra aqui -- ele sequer e lido do banco.
    """
    acesso = (
        translate("keys.access_restricted", lang)
        if key.model_access_mode == "restricted"
        else translate("keys.access_all", lang)
    )
    escopos = ", ".join(key.scopes) if key.scopes else translate("table.not_declared", lang)
    linhas = [
        (translate("table.status", lang), health_badge(key.health_status, lang)),
        (translate("table.key_state", lang), esc(key_state_label(key, lang))),
        (translate("table.remaining", lang), render_remaining_seconds(key.remaining_seconds, lang)),
        (translate("table.issued_at", lang),
         f'<span class="font-monospace">{esc(format_timestamp(key.issued_at))}</span>'),
        (translate("table.last_used", lang),
         f'<span class="font-monospace">{esc(format_timestamp(key.last_used_at))}</span>'),
        (translate("table.model_access", lang), esc(acesso)),
        (translate("table.scopes", lang), f'<span class="font-monospace">{esc(escopos)}</span>'),
    ]
    extra = ""
    if key.allowed_models:
        itens = "".join(f'<li class="font-monospace small">{esc(m)}</li>' for m in key.allowed_models)
        extra = (f'<p class="text-secondary small mb-1 mt-3">{esc(translate("table.models", lang))}</p>'
                 f'<ul class="mb-0">{itens}</ul>')
    return render_detail_modal(modal_id, key.name, linhas, lang, extra)


def render_keys_table(keys: List[Any], lang: str) -> str:
    """Chaves virtuais emitidas pelo gateway, uma por linha, nas sete colunas."""
    if not keys:
        return estado_vazio(translate("keys.empty", lang))

    rows = []
    detalhes = []
    # Id do modal pelo INDICE, nunca pelo nome: nome de chave aceita espaco,
    # acento e barra, e nada disso vale como id de elemento HTML.
    for indice, key in enumerate(keys):
        modal_id = f"detalhe-chave-{indice}"
        rows.append(f"""
            <tr>
              <td><span class="provider-chip">{esc(PROVEDOR_DO_GATEWAY)}</span></td>
              <td class="fw-semibold">{esc(key.name)}</td>
              <td class="text-nowrap">
                <i class="bi bi-key me-1 text-secondary" aria-hidden="true"></i>{esc(translate("type.virtual_key", lang))}
              </td>
              <td>{health_badge(key.health_status, lang)}</td>
              <td class="text-nowrap">{render_remaining_seconds(key.remaining_seconds, lang)}</td>
              <td class="text-nowrap small">{render_timestamp_cell(key.issued_at, lang, "bi-clock")}</td>
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_key_details(key, modal_id, lang))

    return cabecalho_de_dominio(rows, lang) + "".join(detalhes)


def render_model_details(model: Any, modal_id: str, lang: str) -> str:
    """Modal com o que nao cabe na linha do modelo.

    Os limites de contexto, os endpoints e a descricao sao texto longo; o nome
    da conexao dona esta aqui porque e ele que explica de onde vem o status da
    linha -- um modelo nao tem saude propria.
    """
    nao_declarado = f'<span class="text-secondary">{esc(translate("table.not_declared", lang))}</span>'

    def numero(valor: Any) -> str:
        return f'<span class="font-monospace">{esc(f"{valor:,}".replace(",", " "))}</span>' \
            if isinstance(valor, int) else nao_declarado

    linhas = [
        (translate("table.provider", lang),
         f'<span class="provider-chip">{esc(model.provider)}</span>' if model.provider else nao_declarado),
        (translate("table.connection", lang),
         esc(model.connection_name) if model.connection_name else nao_declarado),
        (translate("table.status", lang), health_badge(model.health_status, lang)),
        (translate("table.source", lang),
         f'<span class="font-monospace">{esc(model.source)}</span>' if model.source else nao_declarado),
        (translate("table.context_limit", lang), numero(model.input_token_limit)),
        (translate("table.output_limit", lang), numero(model.output_token_limit)),
        (translate("table.endpoints", lang),
         f'<span class="font-monospace">{esc(", ".join(model.supported_endpoints))}</span>'
         if model.supported_endpoints else nao_declarado),
    ]
    extra = f'<p class="text-secondary small mb-0 mt-3">{esc(translate("models.inherited", lang))}</p>'
    if model.description:
        extra = (f'<p class="text-secondary small mb-1 mt-3">{esc(translate("table.description", lang))}</p>'
                 f'<p class="small mb-0">{esc(model.description)}</p>' + extra)
    return render_detail_modal(modal_id, model.id, linhas, lang, extra)


def render_models_table(models: List[Any], lang: str) -> str:
    """Modelos que o gateway conhece, nas mesmas sete colunas dos irmaos.

    O catalogo de um gateway com OpenRouter ligado passa de quinhentas entradas,
    e cada linha traz um modal junto: renderizar tudo levaria a pagina a quase um
    megabyte de HTML para uma tabela que ninguem le ate o fim. A tela mostra as
    primeiras MAX_LINHAS_DE_MODELO, o cabecalho continua contando o total e o
    rodape diz quantas ficaram de fora -- truncar em silencio seria mentir sobre
    o tamanho do catalogo.
    """
    if not models:
        return estado_vazio(translate("models.empty", lang))

    visiveis = models[:MAX_LINHAS_DE_MODELO]
    rows = []
    detalhes = []
    for indice, model in enumerate(visiveis):
        modal_id = f"detalhe-modelo-{indice}"
        provedor = (f'<span class="provider-chip">{esc(model.provider)}</span>'
                    if model.provider else '<span class="text-secondary">—</span>')
        rows.append(f"""
            <tr>
              <td>{provedor}</td>
              <td class="fw-semibold font-monospace">{esc(model.id)}</td>
              <td class="text-nowrap">
                <i class="bi bi-cpu me-1 text-secondary" aria-hidden="true"></i>{esc(translate("type.synced_model", lang))}
              </td>
              <td>{health_badge(model.health_status, lang)}</td>
              <td class="text-nowrap">{render_remaining_seconds(model.remaining_seconds, lang)}</td>
              <td class="text-nowrap small">{render_timestamp_cell(model.last_refresh_at, lang, "bi-arrow-repeat")}</td>
              <td class="text-end">{detail_button(modal_id, lang)}</td>
            </tr>""")
        detalhes.append(render_model_details(model, modal_id, lang))

    rodape = ""
    if len(models) > len(visiveis):
        rodape = (f'<p class="text-secondary small mb-0 px-3 py-2">'
                  f'{esc(translate("models.showing", lang, shown=len(visiveis), total=len(models)))}</p>')

    return cabecalho_de_dominio(rows, lang) + rodape + "".join(detalhes)


def render_combos_table(combos: List[Dict[str, Any]], lang: str) -> str:
    if not combos:
        return estado_vazio(translate("combos.empty", lang))

    rows = []
    for combo in combos:
        models = combo.get("models") or []
        if isinstance(models, str):
            models = [models]
        preview = ", ".join(str(m) for m in models[:4])
        if len(models) > 4:
            preview += f" (+{len(models) - 4})"
        rows.append(f"""
            <tr>
              <td class="fw-semibold">{esc(combo.get("name", "—"))}</td>
              <td class="small text-secondary">{esc(preview) or "—"}</td>
            </tr>""")

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.combo", lang))}</th>
                <th scope="col">{esc(translate("table.cascade", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""


def render_cron_history(history: List[Dict[str, Any]], lang: str) -> str:
    """Lista de execuções do cron, cada uma com o log do que realmente aconteceu."""
    if not history:
        return f'<p class="text-secondary small mb-0">{esc(translate("cron.no_runs", lang))}</p>'

    items = []
    for index, entry in enumerate(history):
        failed = not entry.get("success", True) or entry.get("error")
        tone = "danger" if failed else "secondary"
        icon = "bi-exclamation-octagon-fill" if failed else "bi-check-circle"
        log_lines = entry.get("log") or []
        if entry.get("error") and not any(str(entry["error"]) in line for line in log_lines):
            log_lines = [f"ERRO: {entry['error']}", *log_lines]

        body = (
            "<pre class=\"cron-log mb-0\">" + esc("\n".join(log_lines)) + "</pre>"
            if log_lines
            else f'<p class="text-secondary small mb-0">{esc(translate("cron.no_runs", lang))}</p>'
        )

        items.append(f"""
          <div class="accordion-item">
            <h3 class="accordion-header">
              <button class="accordion-button collapsed py-2" type="button"
                      data-bs-toggle="collapse" data-bs-target="#ciclo{index}"
                      aria-expanded="false" aria-controls="ciclo{index}">
                <span class="d-flex align-items-center gap-2 w-100 pe-3">
                  <i class="bi {icon} text-{tone}" aria-hidden="true"></i>
                  <span class="font-monospace small">{esc(format_timestamp(entry.get("timestamp")))}</span>
                  <span class="ms-auto small text-secondary">
                    {esc(translate("cron.result_line", lang,
                                   inspected=entry.get("totalInspected", 0),
                                   refreshed=entry.get("refreshedCount", 0),
                                   duration=entry.get("durationMs", 0)))}
                  </span>
                </span>
              </button>
            </h3>
            <div id="ciclo{index}" class="accordion-collapse collapse">
              <div class="accordion-body py-2">{body}</div>
            </div>
          </div>""")

    return f'<div class="accordion accordion-flush" id="historicoCron">{"".join(items)}</div>'


def render_cron_card(cron: Dict[str, Any], lang: str) -> str:
    active = bool(cron.get("active"))
    state_icon = "bi-broadcast text-success" if active else "bi-pause-circle text-secondary"
    state_text = (
        translate("cron.active", lang, interval=cron.get("intervalSeconds", "—"))
        if active
        else translate("cron.disabled", lang)
    )
    last = cron.get("lastResult") or {}
    failed = bool(last) and (not last.get("success", True) or last.get("error"))

    return f"""
      <div class="card h-100">
        <div class="card-header d-flex align-items-center justify-content-between">
          <span class="d-inline-flex align-items-center gap-2">
            <i class="bi bi-alarm" aria-hidden="true"></i>{esc(translate("cron.title", lang))}
          </span>
          <div class="d-flex gap-2">
            <button class="btn btn-outline-light btn-sm" type="button"
                    data-bs-toggle="modal" data-bs-target="#modalHistorico">
              <i class="bi bi-list-columns-reverse me-1" aria-hidden="true"></i>Logs
              {'<span class="badge text-bg-danger ms-1">!</span>' if failed else ""}
            </button>
            <form method="post" action="/acoes/cron" class="m-0">
              <button class="btn btn-outline-light btn-sm" type="submit">
                <i class="bi bi-play-fill me-1" aria-hidden="true"></i>{esc(translate("cron.run_now", lang))}
              </button>
            </form>
          </div>
        </div>
        <div class="card-body">
          <p class="d-flex align-items-center gap-2 mb-3">
            <i class="bi {state_icon}" aria-hidden="true"></i><span>{esc(state_text)}</span>
          </p>
          <dl class="row mb-0 small">
            <dt class="col-4 text-secondary fw-normal">{esc(translate("cron.next_run", lang))}</dt>
            <dd class="col-8 text-end font-monospace text-nowrap">{esc(format_timestamp(cron.get("nextRunAt")))}</dd>
            <dt class="col-4 text-secondary fw-normal">{esc(translate("cron.total_renewals", lang))}</dt>
            <dd class="col-8 text-end font-monospace">{esc(cron.get("totalRenewals", 0))}</dd>
            <dt class="col-4 text-secondary fw-normal mt-2">{esc(translate("cron.last_result", lang))}</dt>
            <dd class="col-8 text-end font-monospace small mb-0 mt-2 {'text-danger' if failed else ''}">
              {esc(translate("cron.result_line", lang,
                             inspected=last.get("totalInspected", 0),
                             refreshed=last.get("refreshedCount", 0),
                             duration=last.get("durationMs", 0))
                   if last else translate("cron.no_runs", lang))}
              {esc(last.get("error") or "")}
            </dd>
          </dl>
        </div>
      </div>"""


def render_gateway_card(gateway: Dict[str, Any], db_path: str, lang: str) -> str:
    online = bool(gateway.get("online"))
    tone = "text-success" if online else "text-danger"
    # Le a bandeira; o resumo textual nunca serve como booleano.
    db_ok = bool(gateway.get("dbOk"))
    if online and db_ok:
        diagnosis = translate("gateway.diag_ok", lang)
    elif online:
        diagnosis = translate("gateway.diag_db_failed", lang)
    else:
        diagnosis = translate("gateway.diag_gateway_failed", lang)
    icon = "bi-plug-fill" if online else "bi-plug"
    label = (
        f'ONLINE (HTTP {esc(gateway.get("statusCode", "—"))})'
        if online
        else f'{esc(translate("gateway.offline", lang))} — '
             f'{esc(gateway.get("error") or translate("gateway.no_response", lang))}'
    )

    return f"""
      <div class="card h-100">
        <div class="card-header d-flex align-items-center justify-content-between">
          <span class="d-inline-flex align-items-center gap-2">
            <i class="bi bi-hdd-network" aria-hidden="true"></i>{esc(translate("gateway.title", lang))}
          </span>
          <form method="post" action="/acoes/testar-gateway" class="m-0">
            <button class="btn btn-outline-light btn-sm" type="submit">
              <i class="bi bi-activity me-1" aria-hidden="true"></i>{esc(translate("action.test_connection", lang))}
            </button>
          </form>
        </div>
        <div class="card-body">
          <dl class="row mb-0 small">
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.gateway", lang))}</dt>
            <dd class="col-7 text-end font-monospace text-truncate">{esc(gateway.get("url") or "—")}</dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.status", lang))}</dt>
            <dd class="col-7 text-end font-monospace {tone}">
              <i class="bi {icon} me-1" aria-hidden="true"></i>{label}
            </dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.latency", lang))}</dt>
            <dd class="col-7 text-end font-monospace">{esc(gateway.get("latencyMs", "—"))} ms</dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.database", lang))}</dt>
            <dd class="col-7 text-end font-monospace text-truncate" title="{esc(db_path)}">
              {esc(translate("gateway.db_summary", lang,
                            connections=gateway.get("dbConnections", 0),
                            combos=gateway.get("dbCombos", 0))
                   if db_ok else translate("gateway.db_missing", lang))}
            </dd>
            <dt class="col-5 text-secondary fw-normal">{esc(translate("gateway.diagnostics", lang))}</dt>
            <dd class="col-7 text-end font-monospace mb-0 {tone}">{esc(diagnosis)}</dd>
          </dl>
        </div>
      </div>"""


def render_flash(flash: Optional[Dict[str, str]]) -> str:
    if not flash:
        return ""
    tone = flash.get("tone", "info")
    icon = {
        "success": "bi-check-circle-fill",
        "danger": "bi-exclamation-octagon-fill",
        "warning": "bi-exclamation-triangle-fill",
        "info": "bi-info-circle-fill",
    }.get(tone, "bi-info-circle-fill")
    return f"""
      <div class="alert alert-{esc(tone)} d-flex align-items-center gap-2" role="status" data-aviso>
        <i class="bi {icon}" aria-hidden="true"></i>
        <div>{esc(flash.get("message", ""))}</div>
      </div>"""


def campo_de_texto(
    nome: str, rotulo: str, valor: str, ajuda: str = "", tipo: str = "text",
    travado: bool = False, marcador: str = "",
) -> str:
    """Um campo do formulario de SSO, com rotulo traduzido e ajuda opcional."""
    dica = f'<div class="form-text">{esc(ajuda)}</div>' if ajuda else ""
    return f"""
            <div class="mb-3">
              <label class="form-label small" for="sso_{esc(nome)}">{esc(rotulo)}</label>
              <input class="form-control" id="sso_{esc(nome)}" name="{esc(nome)}"
                     type="{esc(tipo)}" value="{esc(valor)}" placeholder="{esc(marcador)}"
                     autocomplete="off"{' disabled' if travado else ''}>
              {dica}
            </div>"""


def render_sso_modal(
    config: Dict[str, str],
    lang: str,
    tem_segredo: bool,
    segredo_do_ambiente: bool,
    desligado_pelo_ambiente: bool,
    endereco_de_retorno: str,
) -> str:
    """Tela de configuracao da entrada federada, com as duas abas.

    O segredo do cliente NUNCA volta para ca: o campo nasce vazio, a tela diz
    apenas se existe um guardado, e salvar em branco MANTEM o anterior. Um GET
    de configuracao que devolvesse o valor seria o mesmo que publica-lo no HTML.
    """
    ativo = config.get("enabled") or ""
    aviso_ambiente = (
        f'<div class="alert alert-warning d-flex align-items-start gap-2" role="alert">'
        f'<i class="bi bi-power" aria-hidden="true"></i>'
        f'<div>{esc(translate("sso.disabled_by_env", lang))}</div></div>'
        if desligado_pelo_ambiente
        else ""
    )

    if segredo_do_ambiente:
        estado_do_segredo = translate("sso.secret_from_env", lang)
    elif tem_segredo:
        estado_do_segredo = translate("sso.secret_stored", lang)
    else:
        estado_do_segredo = translate("sso.secret_missing", lang)

    aba_oidc = "".join([
        campo_de_texto("base_url", translate("sso.base_url", lang), config.get("base_url", ""),
                       translate("sso.base_url_help", lang), marcador="https://painel.exemplo.com"),
        f"""
            <div class="mb-3">
              <label class="form-label small">{esc(translate("sso.redirect_uri", lang))}</label>
              <div class="form-control font-monospace small text-secondary">{esc(endereco_de_retorno or "-")}</div>
            </div>""",
        campo_de_texto("oidc_issuer", translate("sso.oidc_issuer", lang), config.get("oidc_issuer", ""),
                       marcador="https://accounts.google.com"),
        campo_de_texto("oidc_client_id", translate("sso.oidc_client_id", lang),
                       config.get("oidc_client_id", "")),
        campo_de_texto("oidc_client_secret", translate("sso.oidc_client_secret", lang), "",
                       estado_do_segredo, tipo="password",
                       travado=segredo_do_ambiente,
                       marcador="••••••••" if tem_segredo else ""),
        campo_de_texto("oidc_scopes", translate("sso.oidc_scopes", lang),
                       config.get("oidc_scopes", ""), marcador="openid email profile"),
    ])

    aba_saml = "".join([
        f"""
            <div class="alert alert-secondary d-flex align-items-start gap-2" role="note">
              <i class="bi bi-exclamation-triangle" aria-hidden="true"></i>
              <div>{esc(translate("sso.saml_unavailable", lang))}</div>
            </div>""",
        campo_de_texto("saml_idp_entity_id", translate("sso.saml_entity_id", lang),
                       config.get("saml_idp_entity_id", ""), travado=True),
        campo_de_texto("saml_idp_sso_url", translate("sso.saml_sso_url", lang),
                       config.get("saml_idp_sso_url", ""), travado=True),
        campo_de_texto("saml_idp_cert", translate("sso.saml_cert", lang),
                       config.get("saml_idp_cert", ""), travado=True),
    ])

    return f"""
  <div class="modal fade" id="modalSSO" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-people" aria-hidden="true"></i>{esc(translate("sso.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">
          {aviso_ambiente}
          <p class="text-secondary small">{esc(translate("sso.intro", lang))}</p>
          <form method="post" action="/acoes/sso">
            <ul class="nav nav-pills nav-sso mb-3" role="tablist">
              <li class="nav-item" role="presentation">
                <button class="nav-link active" data-bs-toggle="pill" data-bs-target="#abaOIDC"
                        type="button" role="tab">{esc(translate("sso.tab_oidc", lang))}</button>
              </li>
              <li class="nav-item" role="presentation">
                <button class="nav-link" data-bs-toggle="pill" data-bs-target="#abaSAML"
                        type="button" role="tab">{esc(translate("sso.tab_saml", lang))}</button>
              </li>
            </ul>
            <div class="tab-content mb-3">
              <div class="tab-pane fade show active" id="abaOIDC" role="tabpanel">{aba_oidc}
              </div>
              <div class="tab-pane fade" id="abaSAML" role="tabpanel">{aba_saml}
              </div>
            </div>

            <hr>
            {campo_de_texto("allowed_domains", translate("sso.allowed_domains", lang),
                            config.get("allowed_domains", ""), marcador="empresa.com,filial.com")}
            {campo_de_texto("allowed_emails", translate("sso.allowed_emails", lang),
                            config.get("allowed_emails", ""),
                            translate("sso.allowlist_help", lang), marcador="chefe@empresa.com")}

            <div class="mb-3">
              <label class="form-label small" for="sso_enabled">{esc(translate("sso.provider", lang))}</label>
              <select class="form-select" id="sso_enabled" name="enabled">
                <option value=""{' selected' if ativo != "oidc" else ''}>{esc(translate("sso.provider_none", lang))}</option>
                <option value="oidc"{' selected' if ativo == "oidc" else ''}>{esc(translate("sso.provider_oidc", lang))}</option>
                <option value="saml" disabled>{esc(translate("sso.provider_saml", lang))}</option>
              </select>
            </div>

            <div class="mb-3">
              <label class="form-label small" for="sso_senha_local">{esc(translate("sso.local_password", lang))}</label>
              <input class="form-control" id="sso_senha_local" name="senha_local" type="password"
                     autocomplete="current-password" required>
              <div class="form-text">{esc(translate("sso.local_password_help", lang))}</div>
            </div>

            <p class="text-secondary small">{esc(translate("sso.logout_note", lang))}</p>

            <button class="btn btn-primary w-100" type="submit">
              <i class="bi bi-save me-1" aria-hidden="true"></i>{esc(translate("sso.save", lang))}
            </button>
          </form>
        </div>
      </div>
    </div>
  </div>"""


def render_dashboard(
    *,
    connections: List[Any],
    combos: List[Dict[str, Any]],
    cron: Dict[str, Any],
    # Chaves virtuais e modelos entraram depois dos outros cartoes e sao
    # opcionais na assinatura: quem chama sem eles (um teste antigo, um script)
    # continua desenhando a pagina, com os dois cartoes no estado vazio.
    keys: Optional[List[Any]] = None,
    models: Optional[List[Any]] = None,
    gateway: Dict[str, Any],
    db_path: str,
    router_url: str,
    current_user: str,
    is_default_password: bool,
    refresh_margin: int,
    auth_from_env: bool = False,
    flash: Optional[Dict[str, str]] = None,
    lang: str = DEFAULT_LANGUAGE,
    # Entrada federada. Opcional na assinatura pelo mesmo motivo de `keys` e
    # `models`: quem chama sem eles -- um teste antigo, um script -- continua
    # desenhando a pagina, com o SSO desligado.
    sso_config: Optional[Dict[str, str]] = None,
    sso_tem_segredo: bool = False,
    sso_segredo_do_ambiente: bool = False,
    sso_desligado_pelo_ambiente: bool = False,
    sso_endereco_de_retorno: str = "",
) -> str:
    """Monta a página completa do dashboard, já com todos os dados embutidos."""
    lang = normalize_language(lang)
    keys = keys or []
    models = models or []
    oauth_count = sum(1 for c in connections if c.is_oauth)
    apikey_count = sum(1 for c in connections if c.has_api_key)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    metrics = "".join([
        metric_card(translate("metric.total_connections", lang), len(connections), "bi-diagram-2", "text-info"),
        metric_card(translate("metric.oauth_accounts", lang), oauth_count, "bi-person-badge", "text-primary"),
        metric_card(translate("metric.api_keys", lang), apikey_count, "bi-key", "text-warning"),
        metric_card(translate("metric.combos", lang), len(combos), "bi-diagram-3", "text-success"),
    ])

    change_password_block = (
        f"""
          <div class="alert alert-secondary d-flex align-items-center gap-2 mb-0" role="note">
            <i class="bi bi-lock-fill" aria-hidden="true"></i>
            <div>{translate("auth.env_managed", lang)}</div>
          </div>"""
        if auth_from_env
        else f"""
          <form method="post" action="/acoes/credenciais">
            <div class="mb-3">
              <label class="form-label" for="novoUsuario">{esc(translate("auth.user", lang))}</label>
              <input class="form-control" id="novoUsuario" name="user" autocomplete="username" required>
            </div>
            <div class="mb-3">
              <label class="form-label" for="novaSenha">{esc(translate("auth.new_password", lang))}</label>
              <input type="password" class="form-control" id="novaSenha" name="password"
                     minlength="6" autocomplete="new-password" required
                     pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)(?=.*[^A-Za-z0-9]).{{6,}}"
                     title="{esc(translate("password.policy", lang))}">
              <div class="form-text">{esc(translate("password.policy", lang))}</div>
            </div>
            <button class="btn btn-primary w-100" type="submit">
              <i class="bi bi-save me-1" aria-hidden="true"></i>{esc(translate("action.save_credentials", lang))}
            </button>
          </form>"""
    )

    return f"""<!DOCTYPE html>
<html lang="{esc(lang)}" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <link rel="icon" href="{FAVICON}">
  <title>OminiRTKSync</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <link rel="stylesheet" href="{FLAG_ICONS}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="{GOOGLE_FONTS}">
  <style>
    /* ------------------------------------------------------------------
       Identidade visual: os tres paineis da familia RTKSync tem a MESMA
       estrutura e a MESMA folha de estilo. O que muda e o valor destes
       tokens -- roxo profundo.
       Trocar o produto e trocar estas oito linhas, nada mais.
       ------------------------------------------------------------------ */
    :root {{
      --bg:        #240046;   /* fundo da pagina */
      --surface:   #310a5c;   /* cartao */
      --surface-2: #3d1270;   /* cabecalho de cartao, chip */
      --line:      #4d1d88;   /* borda */
      --accent:    #b57bff;   /* acao primaria */
      --accent-2:  #d2aaff;   /* acao secundaria, realce */
      --brand-a:   #7a2fd6;   /* marca, inicio do gradiente */
      --brand-b:   #b57bff;   /* marca, fim do gradiente */
      --text:      #e6e8ee;
      --text-dim:  #b9a6d4;
    }}
    body {{ background: var(--bg); color: var(--text); }}
    .card {{ background: var(--surface); border: 1px solid var(--line); }}
    .card-header {{ background: var(--surface-2); border-bottom: 1px solid var(--line); font-weight: 600; }}
    .metric .metric-label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: var(--text-dim); }}
    .metric .metric-value {{ font-size: 2rem; font-weight: 700; line-height: 1.2; margin-top: .35rem; }}
    .provider-chip {{ background: var(--surface-2); border: 1px solid var(--line); border-radius: .35rem;
                      padding: .15rem .5rem; font-family: var(--bs-font-monospace); font-size: .78rem;
                      text-transform: uppercase; }}
    .table-dark {{ --bs-table-bg: transparent; --bs-table-border-color: var(--line); }}
    /* A marca e icone BRANCO sobre um tom claro do proprio tema. O gradiente
       de duas cores fazia as tres telas parecerem a mesma marca em cores
       diferentes; com a forma do icone distinta e o fundo discreto, quem
       identifica o produto e o desenho, e a cor fica por conta do tema. */
    .brand-mark {{ width: 2.25rem; height: 2.25rem; display: grid; place-items: center; border-radius: .5rem;
                   background: color-mix(in srgb, var(--brand-b) 22%, transparent);
                   border: 1px solid color-mix(in srgb, var(--brand-b) 45%, transparent);
                   color: #fff; font-size: 1.15rem; }}
    .accordion-item, .accordion-button {{ background: var(--surface); color: var(--text); }}
    .accordion-button:not(.collapsed) {{ background: var(--surface-2); color: #fff; box-shadow: none; }}
    .cron-log {{ white-space: pre-wrap; word-break: break-word; font-size: .8rem; color: var(--text-dim);
                 background: var(--bg); border: 1px solid var(--line); border-radius: .35rem; padding: .6rem; }}
    /* O botao primario segue o acento do produto, em vez do azul fixo do
       Bootstrap: senao os tres mudam de fundo e ficam com o mesmo botao, o que
       faz a identidade parecer acidental. */
    .btn-primary {{ --bs-btn-bg: var(--accent); --bs-btn-border-color: var(--accent);
                    --bs-btn-hover-bg: var(--accent-2); --bs-btn-hover-border-color: var(--accent-2);
                    --bs-btn-active-bg: var(--accent-2); --bs-btn-active-border-color: var(--accent-2);
                    --bs-btn-color: var(--bg); --bs-btn-hover-color: var(--bg); --bs-btn-active-color: var(--bg); }}
    a {{ color: var(--accent-2); }}
    a:hover {{ color: var(--accent); }}
    /* Abas do modal de SSO. Pintadas com os tokens que ja existem, e nunca com
       tokens novos: um token a mais aqui seria um componente que so este painel
       sabe desenhar, e a simetria entre os tres acabaria nele. */
    .nav-sso .nav-link {{ color: var(--text-dim); }}
    .nav-sso .nav-link.active {{ background: var(--accent); color: var(--bg); }}
    /* Barra de acoes do cabecalho: todos os controles com a MESMA altura. O
       seletor de idioma carrega so a bandeira, um elemento com altura propria;
       sem texto ao lado para definir a linha, ele esticava o botao. */
    .barra-acoes {{ display: flex; align-items: stretch; gap: .5rem; }}
    .barra-acoes > * {{ display: flex; align-items: center; }}
    .barra-acoes .btn {{ height: 2rem; padding-top: 0; padding-bottom: 0;
                         display: inline-flex; align-items: center; line-height: 1; }}
    .barra-acoes .fi {{ line-height: 1; }}
    /* A tabela de conexoes tem sete colunas, e sem largura declarada o
       navegador as reparte pelo conteudo: o diagnostico -- a coluna com a frase
       mais longa -- recebia a menor fatia e quebrava em quatro linhas, enquanto
       "Tipo" e "Status", de largura fixa, sobravam espaco. Declarar a divisao
       resolve na origem, e `table-layout: fixed` faz o navegador respeita-la em
       vez de recalcular pelo conteudo. */
    .tabela-dominio {{ table-layout: fixed; }}
    .tabela-dominio th, .tabela-dominio td {{ padding: .6rem .5rem; vertical-align: top; }}
    /* Com table-layout:fixed a largura da coluna e lei, e text-nowrap
       (white-space:nowrap!important) sem overflow:hidden nao corta nem quebra:
       o excesso se desenha POR CIMA da coluna vizinha. Foi assim que a validade
       apareceu escrita sobre a data de renovacao. O corte com reticencias
       mantem a linha legivel; o texto inteiro fica no botao (i) da linha. */
    .tabela-dominio td, .tabela-dominio th {{ overflow: hidden; text-overflow: ellipsis; }}
    /* O cabecalho nao pode quebrar no meio da palavra ("Detalhe" / "s"). */
    .tabela-dominio th {{ white-space: nowrap; }}
    .tabela-dominio col.c-provedor    {{ width: 8rem; }}
    .tabela-dominio col.c-nome        {{ width: auto; }}
    .tabela-dominio col.c-tipo        {{ width: 9.5rem; }}
    .tabela-dominio col.c-status      {{ width: 9.5rem; }}
    .tabela-dominio col.c-validade    {{ width: 11rem; }}
    .tabela-dominio col.c-renovacao   {{ width: 10.5rem; }}
    .tabela-dominio col.c-detalhe     {{ width: 5.5rem; }}
    /* O nome do provedor e um identificador longo e sem espaco
       (openai-compatible-chat-ollama-local): sem isto ele estoura a coluna ou
       forca a tabela a rolar horizontalmente inteira. */
    .tabela-dominio .provider-chip {{ display: inline-block; max-width: 100%;
                                       overflow-wrap: anywhere; white-space: normal; }}
    .tabela-dominio .diagnostico {{ overflow-wrap: anywhere; }}
    /* Em tela estreita a tabela rola sozinha, em vez de espremer as colunas
       ate o texto virar uma palavra por linha. */
    @media (max-width: 1200px) {{
      .tabela-dominio {{ min-width: 68rem; }}
    }}
  </style>
</head>
<body>
  <div class="container-xl py-4">

    {render_flash(flash)}
    {render_security_banner(is_default_password, lang)}

    <header class="d-flex flex-wrap align-items-center justify-content-between gap-3 mb-4">
      <div class="d-flex align-items-center gap-3">
        <span class="brand-mark"><i class="bi bi-signpost-split-fill" aria-hidden="true"></i></span>
        <div>
          <h1 class="h4 mb-0">OminiRTKSync</h1>
          <p class="text-secondary small mb-0 font-monospace">
            {esc(router_url or translate("app.gateway_unset", lang))}
          </p>
        </div>
      </div>
      <div class="barra-acoes">
        {render_language_switcher(lang)}
        <form method="post" action="/acoes/cron" class="m-0">
          <button class="btn btn-primary btn-sm" type="submit">
            <i class="bi bi-arrow-repeat me-1" aria-hidden="true"></i>{esc(translate("action.sync_now", lang))}
          </button>
        </form>
        <button class="btn btn-outline-light btn-sm" type="button"
                data-bs-toggle="modal" data-bs-target="#modalSSO">
          <i class="bi bi-gear me-1" aria-hidden="true"></i>{esc(translate("action.settings", lang))}
        </button>
        <form method="post" action="/logout" class="m-0">
          <button class="btn btn-outline-light btn-sm" type="submit">
            <i class="bi bi-box-arrow-right me-1" aria-hidden="true"></i>{esc(translate("auth.logout", lang))}
          </button>
        </form>
      </div>
    </header>

    <div class="row g-3 mb-4">{metrics}
    </div>

    <div class="row g-3 mb-4">
      <div class="col-lg-6">{render_gateway_card(gateway, db_path, lang)}</div>
      <div class="col-lg-6">{render_cron_card(cron, lang)}</div>
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-list-check" aria-hidden="true"></i>{esc(translate("connections.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(connections)}</span>
      </div>
      {render_connections_table(connections, refresh_margin, lang)}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-key" aria-hidden="true"></i>{esc(translate("keys.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(keys)}</span>
      </div>
      {render_keys_table(keys, lang)}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-cpu" aria-hidden="true"></i>{esc(translate("models.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(models)}</span>
      </div>
      {render_models_table(models, lang)}
    </div>

    <div class="card mb-4">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span class="d-inline-flex align-items-center gap-2">
          <i class="bi bi-diagram-3" aria-hidden="true"></i>{esc(translate("combos.title", lang))}
        </span>
        <span class="badge text-bg-dark">{len(combos)}</span>
      </div>
      {render_combos_table(combos, lang)}
    </div>

    <footer class="d-flex flex-wrap justify-content-between gap-2 text-secondary small pb-3">
      <span>
        <i class="bi bi-person-circle me-1" aria-hidden="true"></i>{esc(translate("footer.signed_in", lang))}
        <span class="font-monospace">{esc(current_user)}</span>
        <button class="btn btn-link btn-sm p-0 ms-2 align-baseline text-secondary"
                data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key me-1" aria-hidden="true"></i>{esc(translate("action.change_credentials", lang))}
        </button>
      </span>
      <span>
        <i class="bi bi-clock-history me-1" aria-hidden="true"></i>{esc(translate("footer.generated", lang))}
        <span class="font-monospace">{esc(generated_at)}</span>
      </span>
    </footer>
  </div>

  <div class="modal fade" id="modalHistorico" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-list-columns-reverse" aria-hidden="true"></i>{esc(translate("cron.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">{render_cron_history(cron.get("history") or [], lang)}
        </div>
      </div>
    </div>
  </div>

  <div class="modal fade" id="modalCredenciais" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h2 class="modal-title h6 d-inline-flex align-items-center gap-2">
            <i class="bi bi-shield-lock" aria-hidden="true"></i>{esc(translate("auth.title", lang))}
          </h2>
          <button type="button" class="btn-close" data-bs-dismiss="modal"
                  aria-label="{esc(translate("action.close", lang))}"></button>
        </div>
        <div class="modal-body">{change_password_block}
        </div>
      </div>
    </div>
  </div>
{render_sso_modal(sso_config or {}, lang, sso_tem_segredo, sso_segredo_do_ambiente,
                  sso_desligado_pelo_ambiente, sso_endereco_de_retorno)}

  <script src="{JQUERY_JS}"></script>
  <script src="{BOOTSTRAP_JS}"></script>
  <script>
    // A página é renderizada no servidor; o jQuery só cuida de conforto de uso.
    jQuery(function ($) {{
      $('form[action^="/acoes/"]').not('[action="/acoes/idioma"]').on('submit', function () {{
        $(this).find('button[type=submit]')
               .prop('disabled', true)
               .find('i').attr('class', 'bi bi-hourglass-split me-1');
      }});

      // O aviso da ultima acao viaja na querystring (POST-Redirect-GET, para o
      // F5 nao repetir a acao). O efeito colateral e que ele fica: a URL guarda
      // o texto, e recarregar traz de volta uma mensagem de algo que ja
      // aconteceu. Assim que a pagina desenha, a querystring e limpa do
      // historico -- sem nova requisicao -- e o aviso some sozinho.
      var $aviso = $('[data-aviso]');
      if ($aviso.length) {{
        if (window.history.replaceState) {{
          window.history.replaceState({{}}, document.title, window.location.pathname);
        }}
        window.setTimeout(function () {{
          $aviso.fadeOut(400, function () {{ $(this).remove(); }});
        }}, 6000);
      }}
    }});
  </script>
</body>
</html>"""
