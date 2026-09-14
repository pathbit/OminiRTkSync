#!/usr/bin/env python3
"""Mede o perfil de consumo real de um agente a partir do historico do Claude Code.

Le APENAS os campos numericos de `usage`, o `message.id` e o `timestamp`.
Nenhum conteudo de conversa e lido, agregado ou impresso.

Cuidado que muda o resultado: uma unica resposta da API costuma ser gravada em
VARIAS linhas `type: "assistant"` (texto + cada bloco de ferramenta), todas com
o mesmo `message.id` e o mesmo objeto `usage`. Contar linhas infla requisicoes e
tokens em ~2x. Aqui cada `message.id` conta uma vez, e o fator de inflacao e
impresso no final, medido -- nao estimado.

REPRODUTIBILIDADE. O historico e um corpus VIVO: ele cresce a cada sessao, entao
rodar sem corte hoje e amanha da numeros diferentes, e nenhum dos dois esta
errado. Para publicar um numero que outra pessoa consiga conferir, use
`--until`: os arquivos de sessao sao append-only, logo o recorte ate um instante
passado nao muda mais (a nao ser que alguem apague sessoes antigas do disco).

    ./.venv/bin/python tools/measure_agent_usage.py --until 2026-09-13T00:00:00Z

Sem `--until` o script le o historico inteiro, que e o que voce quer quando esta
medindo a SUA maquina para dimensionar o SEU time.
"""

import argparse
import glob
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

HISTORY_ROOT = os.path.expanduser("~/.claude/projects")
IDLE_GAP = timedelta(minutes=20)   # lacuna que encerra uma "hora ativa"
MIN_TURNS_PER_SESSION = 10         # sessao curta demais nao diz nada sobre ritmo
MIN_ACTIVE_SECONDS = 300


def parse_instant(text):
    """Le um instante ISO-8601 e devolve sempre com fuso, para poder comparar."""
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def read_session(path, until=None, tally=None):
    """Devolve os turnos unicos de um arquivo de sessao, ordenados no tempo.

    `tally` acumula, sobre TODOS os arquivos e antes de qualquer filtro de
    sessao, o numero de linhas de resposta e o numero de `message.id` distintos:
    e a razao entre esses dois que mede a inflacao de contar linha por linha.
    """
    turns = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("type") != "assistant":
                    continue
                message = record.get("message") or {}
                usage = message.get("usage") or {}
                if not isinstance(usage, dict):
                    continue
                fresh_input = usage.get("input_tokens") or 0
                cache_write = usage.get("cache_creation_input_tokens") or 0
                cache_read = usage.get("cache_read_input_tokens") or 0
                output = usage.get("output_tokens") or 0
                if fresh_input + cache_write + cache_read + output <= 0:
                    continue
                try:
                    when = parse_instant(record.get("timestamp"))
                except Exception:
                    continue
                # O corte vale por TURNO, nao por arquivo: uma sessao que comecou
                # antes e continuou depois entra so com a parte anterior ao corte.
                if until is not None and when >= until:
                    continue
                # Deduplicacao: uma resposta da API = um message.id, nao uma linha.
                # Entre as linhas que repetem o id, fica a de maior contagem: as
                # primeiras podem trazer `output_tokens` ainda parcial.
                message_id = message.get("id") or f"{path}:{when.isoformat()}"
                if tally is not None:
                    tally["lines"] += 1
                    tally["ids"].add(message_id)
                candidate = (when, fresh_input + cache_write, output, cache_read)
                previous = turns.get(message_id)
                if previous is None or sum(candidate[1:]) > sum(previous[1:]):
                    turns[message_id] = candidate
    except Exception:
        return []
    return sorted(turns.values(), key=lambda t: t[0])


def active_seconds(turns):
    """Tempo em que o agente esteve de fato requisitando, ignorando pausas longas."""
    total = 0.0
    for previous, current in zip(turns, turns[1:]):
        delta = current[0] - previous[0]
        if timedelta(0) <= delta <= IDLE_GAP:
            total += delta.total_seconds()
    return total


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument(
    "--until",
    metavar="ISO",
    help="Ignora turnos a partir deste instante ISO-8601 (ex.: 2026-09-13T00:00:00Z). "
         "E o que torna a medicao reproduzivel: o historico so cresce para a frente.",
)
args = parser.parse_args()
cutoff = parse_instant(args.until) if args.until else None

billable_input, output_tokens, cached_input, total_input = [], [], [], []
request_rates, session_spans = [], []
grand_billable = grand_output = grand_cached = grand_turns = 0
tally = {"lines": 0, "ids": set()}

for session_path in glob.glob(os.path.join(HISTORY_ROOT, "**", "*.jsonl"), recursive=True):
    turns = read_session(session_path, until=cutoff, tally=tally)
    if len(turns) < MIN_TURNS_PER_SESSION:
        continue
    elapsed = active_seconds(turns)
    if elapsed < MIN_ACTIVE_SECONDS:
        continue
    count = len(turns)
    billable_input.append(sum(t[1] for t in turns) / count)
    output_tokens.append(sum(t[2] for t in turns) / count)
    cached_input.append(sum(t[3] for t in turns) / count)
    total_input.append(sum(t[1] + t[3] for t in turns) / count)
    request_rates.append(count / (elapsed / 3600.0))
    session_spans.append((turns[0][0], turns[-1][0]))
    grand_billable += sum(t[1] for t in turns)
    grand_output += sum(t[2] for t in turns)
    grand_cached += sum(t[3] for t in turns)
    grand_turns += count

# Imprime o caminho SEM expandir o `~`: a saida deste script vai colada para
# dentro da wiki, e caminho de home carrega o nome de usuario da maquina.
print("historico lido                             : ~/.claude/projects/**/*.jsonl")
print(f"corte (--until)                            : {args.until or 'nenhum -- corpus vivo, nao reproduz'}")
print(f"sessoes analisadas                         : {len(request_rates)}")
print(f"turnos unicos (dedup por message.id)        : {grand_turns}")
print(f"T_in  entrada que conta p/ ITPM  mediana    : {statistics.median(billable_input):.0f}")
print(f"T_in  entrada que conta p/ ITPM  p90        : {percentile(billable_input, 0.90):.0f}")
print(f"T_out saida                      mediana    : {statistics.median(output_tokens):.0f}")
print(f"T_out saida                      p90        : {percentile(output_tokens, 0.90):.0f}")
print(f"T_cache leitura de cache         mediana    : {statistics.median(cached_input):.0f}")
print(f"T_tot entrada total (conta+cache) mediana   : {statistics.median(total_input):.0f}")
print(f"T_tot entrada total (conta+cache) p90       : {percentile(total_input, 0.90):.0f}")
print(f"R_h   requisicoes por hora ativa mediana    : {statistics.median(request_rates):.0f}")
print(f"R_h   requisicoes por hora ativa p90        : {percentile(request_rates, 0.90):.0f}")
total_all = grand_billable + grand_output + grand_cached
print(f"fracao de leitura de cache no total         : {grand_cached / total_all * 100:.1f}%")
print(f"razao entrada total / entrada que conta     : "
      f"{statistics.median(total_input) / statistics.median(billable_input):.1f}x")

# Concorrencia: quantas sessoes se sobrepoem no tempo. Numa maquina de um unico
# operador isto mede paralelismo de subagentes, nao concorrencia de time.
events = []
for start, end in session_spans:
    events.append((start, 1))
    events.append((end, -1))
events.sort()
open_sessions = peak = 0
for _, delta in events:
    open_sessions += delta
    peak = max(peak, open_sessions)
print(f"pico de sessoes simultaneas                 : {peak}")

# Inflacao de contar linha em vez de resposta. Contado sobre TODOS os arquivos,
# antes dos filtros de sessao: e o fator que erra por dois quem le o historico
# sem deduplicar. Aqui ele deixa de ser afirmacao e passa a ser saida de script.
distinct_ids = len(tally["ids"])
inflation = tally["lines"] / distinct_ids if distinct_ids else float("nan")
print(f"linhas de resposta (com usage)              : {tally['lines']}")
print(f"message.id distintos                        : {distinct_ids}")
print(f"inflacao de contar linha e nao resposta     : {inflation:.2f}x")
