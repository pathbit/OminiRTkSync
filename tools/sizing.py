#!/usr/bin/env python3
"""Resolve a formula de dimensionamento com os numeros medidos e publicados.

Cada constante aqui tem uma procedencia diferente, e misturar as tres sem dizer
qual e qual e como um numero inventado nasce:

  MEDIDO     -- PROFILES, copiado da saida do comando abaixo. Quem quiser
                conferir roda o mesmo comando; quem quiser dimensionar o proprio
                time roda sem `--until` na propria maquina e troca estes valores.
  PUBLICADO  -- API_TIERS, da tabela de rate limits por tier da API da Anthropic.
  ARBITRADO  -- CONCURRENCY e SLACK. Ninguem mediu isto: sao escolhas de
                operacao, variadas de proposito para mostrar o quanto mexem no
                resultado. Nao trate como medicao.
"""

import math

# --- MEDIDO: perfil de UMA sessao ativa de agente ---
MEASUREMENT_COMMAND = (
    "./.venv/bin/python tools/measure_agent_usage.py --until 2026-09-13T00:00:00Z"
)
PROFILES = {
    "mediana": {"rate_per_hour": 194, "billable_input": 4178, "output": 706, "total_input": 88353},
    "p90": {"rate_per_hour": 343, "billable_input": 8227, "output": 1361, "total_input": 303214},
}

# --- PUBLICADO: teto por tier da API (Opus 5 / Sonnet 5) ---
API_TIERS = {
    "Start": {"rpm": 1_000, "itpm": 2_000_000, "otpm": 400_000},
    "Build": {"rpm": 5_000, "itpm": 5_000_000, "otpm": 1_000_000},
    "Scale": {"rpm": 10_000, "itpm": 10_000_000, "otpm": 2_000_000},
}

TEAM_SIZES = [3, 12, 40]

# --- ARBITRADO: nada disto foi medido ---
CONCURRENCY = [0.4, 0.6, 1.0]   # fracao do time pedindo ao mesmo tempo
DEFAULT_CONCURRENCY = 0.6       # o valor usado na tabela de assinatura
SLACK = 0.30                    # folga de operacao
WINDOW_HOURS = 5                # PUBLICADO: janela de reset de 5 h


def burst_demand(team_size, concurrency, profile, slack=SLACK):
    """Demanda por minuto: e o teto que estoura primeiro."""
    simultaneous = team_size * concurrency
    rpm = simultaneous * profile["rate_per_hour"] / 60 * (1 + slack)
    return simultaneous, rpm, rpm * profile["billable_input"], rpm * profile["output"]


def smallest_tier(rpm, itpm, otpm):
    for name, tier in API_TIERS.items():
        if rpm <= tier["rpm"] and itpm <= tier["itpm"] and otpm <= tier["otpm"]:
            return name
    return "acima de Scale"


print("### PROCEDENCIA DAS ENTRADAS")
print(f"medido     : {MEASUREMENT_COMMAND}")
print("publicado  : https://platform.claude.com/docs/en/api/rate-limits")
print(f"arbitrado  : c in {CONCURRENCY} (tabela de assinatura usa c={DEFAULT_CONCURRENCY}), "
      f"folga={SLACK:.0%}, W_h={WINDOW_HOURS}h")


print("\n\n### CAMINHO DE CHAVE DE API - teto publicado, resolve numericamente")
for label, profile in PROFILES.items():
    print(f"\nperfil {label}: R_h={profile['rate_per_hour']} req/h, "
          f"T_in={profile['billable_input']} tok/req, T_out={profile['output']} tok/req, "
          f"folga={SLACK:.0%}")
    print(f"{'devs':>5} {'c':>5} {'U_sim':>6} {'RPM':>7} {'ITPM':>10} {'OTPM':>9}  tier minimo")
    for size in TEAM_SIZES:
        for factor in CONCURRENCY:
            simultaneous, rpm, itpm, otpm = burst_demand(size, factor, profile)
            print(f"{size:>5} {factor:>5.1f} {simultaneous:>6.1f} {rpm:>7.0f} "
                  f"{itpm:>10,.0f} {otpm:>9,.0f}  {smallest_tier(rpm, itpm, otpm)}")

print("\n\n### CAMINHO DE ASSINATURA (OmniRoute) - C_janela e [A MEDIR]")
print("A unidade aqui e TOKEN TOTAL (entrada cacheada inclusa): o medidor da")
print("assinatura nao publica o que conta, e a calibracao de Settings > Usage")
print("so pode ser feita contra o total trafegado.\n")
print("D_total = U_sim * R_h * (T_tot + T_out) * W_h * (1 + F)")
for label, profile in PROFILES.items():
    for size in TEAM_SIZES:
        factor = DEFAULT_CONCURRENCY
        simultaneous = size * factor
        demand = (simultaneous * profile["rate_per_hour"]
                  * (profile["total_input"] + profile["output"])
                  * WINDOW_HOURS * (1 + SLACK))
        print(f"perfil {label:>7} | {size:>2} devs | c={factor} | U_sim={simultaneous:4.1f} | "
              f"W={WINDOW_HOURS}h | D = {demand:,.0f} tokens -> L = ceil(D / C_janela)")

print("\n\n### folga da rajada contra UMA licenca de API no tier Start (1000 rpm)")
for size in TEAM_SIZES:
    _, rpm, _, _ = burst_demand(size, DEFAULT_CONCURRENCY, PROFILES["mediana"])
    print(f"{size:>2} devs, c={DEFAULT_CONCURRENCY} -> pico exigido {rpm:6.0f} rpm; "
          f"Start cobre {math.floor(1000 / rpm)}x a demanda")
