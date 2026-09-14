"""Motor de agendamento em background (CronScheduler) do painel."""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .identidade import NOME_DO_PRODUTO
from .logs import get_logger


def _extract_log_lines(res: Any) -> List[str]:
    """Extrai as acoes registradas pelo motor de sincronizacao neste ciclo.

    Guarda so o que explica o resultado — erro, renovacao, auto-cura. Um ciclo
    sem nada a fazer devolve lista vazia, e a tela mostra isso como tal.
    """
    if not isinstance(res, dict):
        return [f"Resultado inesperado do motor: {res!r}"]

    lines: List[str] = []
    if res.get("error"):
        lines.append(f"ERRO: {res['error']}")

    for detail in res.get("details", []) or []:
        actions = detail.get("actions") or []
        if not actions:
            continue
        label = f"{detail.get('provider', '?')} · {detail.get('name', '?')}"
        for action in actions:
            lines.append(f"{label}: {action}")

    return lines


class CronScheduler:
    """Agendador em background da renovacao continua de contas OAuth e da saude das conexoes."""

    def __init__(
        self,
        sync_callback: Callable[[], Dict[str, Any]],
        interval_seconds: int = 300,
        name: str = f"{NOME_DO_PRODUTO}-Cron",
    ):
        self.sync_callback = sync_callback
        self.interval_seconds = max(10, interval_seconds)
        self.name = name
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Metricas de execucao
        self.total_runs = 0
        self.total_renewals = 0
        self.last_run_at: Optional[str] = None
        self.next_run_at: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []

    def start(self):
        """Sobe a thread de cron em background."""
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self._stop_event.clear()
            self._update_next_run(self.interval_seconds)
            self._thread = threading.Thread(target=self._run_loop, name=self.name, daemon=True)
            self._thread.start()

    def stop(self):
        """Encerra a thread de cron sem violencia."""
        with self._lock:
            self.is_running = False
            self._stop_event.set()

    def trigger_now(self) -> Dict[str, Any]:
        """Dispara um ciclo de sincronizacao agora, de forma sincrona."""
        return self._execute_cycle(reason="manual_trigger")

    def get_status(self) -> Dict[str, Any]:
        """Retrato detalhado do agendador para a API e para o painel."""
        with self._lock:
            return {
                "active": self.is_running,
                "intervalSeconds": self.interval_seconds,
                "totalRuns": self.total_runs,
                "totalRenewals": self.total_renewals,
                "lastRunAt": self.last_run_at,
                "nextRunAt": self.next_run_at,
                "lastResult": self.last_result,
                "history": list(reversed(self.history[-10:])),
            }

    def _update_next_run(self, delay_seconds: int):
        nxt = time.time() + delay_seconds
        self.next_run_at = datetime.fromtimestamp(nxt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _execute_cycle(self, reason: str = "scheduled_interval") -> Dict[str, Any]:
        start_ts = time.time()
        start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        get_logger().info(f"[CRON] Ciclo disparado ({reason}). Inspecionando conexoes de contas OAuth...")

        try:
            res = self.sync_callback()
        except Exception as e:
            res = {"success": False, "error": str(e)}

        duration_ms = int((time.time() - start_ts) * 1000)
        refreshed = res.get("refreshed", 0) if isinstance(res, dict) else 0
        total = res.get("total_connections", res.get("total", 0)) if isinstance(res, dict) else 0

        entry = {
            "timestamp": start_iso,
            "reason": reason,
            "durationMs": duration_ms,
            "totalInspected": total,
            "refreshedCount": refreshed,
            "success": res.get("success", True) if isinstance(res, dict) else False,
            "error": res.get("error") if isinstance(res, dict) else None,
            "log": _extract_log_lines(res),
        }

        with self._lock:
            self.total_runs += 1
            self.total_renewals += refreshed
            self.last_run_at = start_iso
            self.last_result = entry
            self.history.append(entry)
            if len(self.history) > 50:
                self.history.pop(0)
            self._update_next_run(self.interval_seconds)

        get_logger().info(
            f"[CRON] Ciclo concluido em {duration_ms}ms: {total} contas avaliadas, "
            f"{refreshed} renovadas via OAuth."
        )
        return entry

    def _run_loop(self):
        self._execute_cycle(reason="startup")
        while not self._stop_event.is_set():
            interrupted = self._stop_event.wait(timeout=self.interval_seconds)
            if interrupted:
                break
            if self.is_running:
                self._execute_cycle(reason="scheduled_interval")
