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
    """Normaliza um timestamp ISO para exibição."""
    if not value:
        return "—"
    return str(value).replace("T", " ").replace("Z", " UTC")


def render_refresh_reason(conn: Any, refresh_margin: int, lang: str = DEFAULT_LANGUAGE) -> str:
    """Explica, em uma frase, por que a conexão foi ou não renovada.

    Sem isso o painel mostra apenas "0 renovadas" e não há como distinguir
    "nada precisava ser renovado" de "a renovação falhou".
    """
    if conn.is_local:
        models = conn.local_models
        if models:
            return translate("reason.local_ok", lang, count=len(models))
        return translate("reason.local_unreachable", lang)

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
        pass

    icon = '<i class="bi bi-arrow-repeat me-1 text-success" aria-hidden="true"></i>'
    detail = f'<div class="text-secondary">{esc(ago)}</div>' if ago else ""
    return f'{icon}<span class="font-monospace">{esc(format_timestamp(stamp))}</span>{detail}'


def render_remaining(conn: Any, lang: str) -> str:
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
    return f'<span class="text-secondary">{esc(translate("duration.no_expiry", lang))}</span>'


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


def render_connections_table(connections: List[Any], refresh_margin: int, lang: str) -> str:
    if not connections:
        return f"""
        <div class="text-center text-secondary py-5">
          <i class="bi bi-inbox fs-1 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("connections.empty", lang))}
        </div>"""

    rows = []
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

        rows.append(f"""
            <tr>
              <td><span class="provider-chip">{esc(c.provider)}</span></td>
              <td class="fw-semibold">{esc(c.name)}{detail}</td>
              <td class="text-nowrap">
                <i class="bi {kind_icon} me-1 text-secondary" aria-hidden="true"></i>{esc(kind)}
              </td>
              <td>{health_badge(c.health_status, lang)}</td>
              <td class="text-nowrap">{render_remaining(c, lang)}</td>
              <td class="text-nowrap small">{render_last_refresh(c, lang)}</td>
              <td class="small text-secondary">{esc(render_refresh_reason(c, refresh_margin, lang))}</td>
            </tr>""")

    return f"""
        <div class="table-responsive">
          <table class="table table-dark table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{esc(translate("table.provider", lang))}</th>
                <th scope="col">{esc(translate("table.name", lang))}</th>
                <th scope="col">{esc(translate("table.type", lang))}</th>
                <th scope="col">{esc(translate("table.status", lang))}</th>
                <th scope="col">{esc(translate("table.remaining", lang))}</th>
                <th scope="col">{esc(translate("table.last_refresh", lang))}</th>
                <th scope="col">{esc(translate("table.diagnosis", lang))}</th>
              </tr>
            </thead>
            <tbody>{"".join(rows)}
            </tbody>
          </table>
        </div>"""


def render_combos_table(combos: List[Dict[str, Any]], lang: str) -> str:
    if not combos:
        return f"""
        <div class="text-center text-secondary py-4">
          <i class="bi bi-diagram-3 fs-3 d-block mb-2" aria-hidden="true"></i>
          {esc(translate("combos.empty", lang))}
        </div>"""

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
              <button class="btn btn-success btn-sm" type="submit">
                <i class="bi bi-play-fill me-1" aria-hidden="true"></i>{esc(translate("action.run_now", lang))}
              </button>
            </form>
          </div>
        </div>
        <div class="card-body">
          <p class="d-flex align-items-center gap-2 mb-3">
            <i class="bi {state_icon}" aria-hidden="true"></i><span>{esc(state_text)}</span>
          </p>
          <dl class="row mb-0 small">
            <dt class="col-6 text-secondary fw-normal">{esc(translate("cron.next_run", lang))}</dt>
            <dd class="col-6 text-end font-monospace">{esc(format_timestamp(cron.get("nextRunAt")))}</dd>
            <dt class="col-6 text-secondary fw-normal">{esc(translate("cron.total_renewals", lang))}</dt>
            <dd class="col-6 text-end font-monospace">{esc(cron.get("totalRenewals", 0))}</dd>
            <dt class="col-12 text-secondary fw-normal mt-2">{esc(translate("cron.last_result", lang))}</dt>
            <dd class="col-12 font-monospace small mb-0 {'text-danger' if failed else ''}">
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
    db_ok = bool(gateway.get("dbSummary"))
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
              {esc(gateway.get("dbSummary") or "—")}
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
      <div class="alert alert-{esc(tone)} d-flex align-items-center gap-2" role="status">
        <i class="bi {icon}" aria-hidden="true"></i>
        <div>{esc(flash.get("message", ""))}</div>
      </div>"""


def render_dashboard(
    *,
    connections: List[Any],
    combos: List[Dict[str, Any]],
    cron: Dict[str, Any],
    gateway: Dict[str, Any],
    db_path: str,
    router_url: str,
    current_user: str,
    is_default_password: bool,
    refresh_margin: int,
    auth_from_env: bool = False,
    flash: Optional[Dict[str, str]] = None,
    lang: str = DEFAULT_LANGUAGE,
) -> str:
    """Monta a página completa do dashboard, já com todos os dados embutidos."""
    lang = normalize_language(lang)
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
  <title>OminiRTKSync</title>
  <link rel="stylesheet" href="{BOOTSTRAP_CSS}">
  <link rel="stylesheet" href="{BOOTSTRAP_ICONS}">
  <link rel="stylesheet" href="{FLAG_ICONS}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="{GOOGLE_FONTS}">
  <style>
    :root {{ --surface: #12151c; --surface-2: #171b24; --line: #242a36; }}
    body {{ background: #0b0d12; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); }}
    .card-header {{ background: var(--surface-2); border-bottom: 1px solid var(--line); font-weight: 600; }}
    .metric .metric-label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #8b93a7; }}
    .metric .metric-value {{ font-size: 2rem; font-weight: 700; line-height: 1.2; margin-top: .35rem; }}
    .provider-chip {{ background: var(--surface-2); border: 1px solid var(--line); border-radius: .35rem;
                      padding: .15rem .5rem; font-family: var(--bs-font-monospace); font-size: .78rem;
                      text-transform: uppercase; }}
    .table-dark {{ --bs-table-bg: transparent; }}
    .brand-mark {{ width: 2.25rem; height: 2.25rem; display: grid; place-items: center; border-radius: .5rem;
                   background: linear-gradient(135deg, #2563eb, #7c3aed); color: #fff; font-size: 1.1rem; }}
    .accordion-item, .accordion-button {{ background: var(--surface); color: #dee2e6; }}
    .accordion-button:not(.collapsed) {{ background: var(--surface-2); color: #fff; box-shadow: none; }}
    .cron-log {{ white-space: pre-wrap; word-break: break-word; font-size: .8rem; color: #b9c0cf;
                 background: #0b0d12; border: 1px solid var(--line); border-radius: .35rem; padding: .6rem; }}
  </style>
</head>
<body>
  <div class="container-xl py-4">

    {render_flash(flash)}
    {render_security_banner(is_default_password, lang)}

    <header class="d-flex flex-wrap align-items-center justify-content-between gap-3 mb-4">
      <div class="d-flex align-items-center gap-3">
        <span class="brand-mark"><i class="bi bi-lightning-charge-fill" aria-hidden="true"></i></span>
        <div>
          <h1 class="h4 mb-0">OminiRTKSync</h1>
          <p class="text-secondary small mb-0 font-monospace">
            {esc(router_url or translate("app.gateway_unset", lang))}
          </p>
        </div>
      </div>
      <div class="d-flex align-items-center gap-2">
        {render_language_switcher(lang)}
        <form method="post" action="/acoes/atualizar" class="m-0 d-inline">
          <button class="btn btn-outline-light btn-sm" type="submit"
                  title="{esc(translate("action.refresh_title", lang))}">
            <i class="bi bi-arrow-clockwise me-1" aria-hidden="true"></i>{esc(translate("action.refresh", lang))}
          </button>
        </form>
        <button class="btn btn-outline-light btn-sm" data-bs-toggle="modal" data-bs-target="#modalCredenciais">
          <i class="bi bi-key me-1" aria-hidden="true"></i>{esc(translate("action.access", lang))}
        </button>
        <form method="post" action="/acoes/sincronizar" class="m-0">
          <button class="btn btn-primary btn-sm" type="submit">
            <i class="bi bi-arrow-repeat me-1" aria-hidden="true"></i>{esc(translate("action.sync_now", lang))}
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
      <div class="card-header d-inline-flex align-items-center gap-2">
        <i class="bi bi-diagram-3" aria-hidden="true"></i>{esc(translate("combos.title", lang))}
      </div>
      {render_combos_table(combos, lang)}
    </div>

    <footer class="d-flex flex-wrap justify-content-between gap-2 text-secondary small pb-3">
      <span>
        <i class="bi bi-person-circle me-1" aria-hidden="true"></i>{esc(translate("footer.signed_in", lang))}
        <span class="font-monospace">{esc(current_user)}</span>
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
    }});
  </script>
</body>
</html>"""
