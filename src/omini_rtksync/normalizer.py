"""Normalização de datas e auto-cura no banco do gateway."""

from datetime import datetime, timezone
from typing import Any, Optional


def parse_expiry_to_ms(val: Any) -> Optional[int]:
    """Converte valores variados (string ISO, string numérica, int) em epoch milissegundos."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        if v <= 0:
            return None
        return int(v * 1000) if v < 1e11 else int(v)

    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            num = float(val)
            return int(num * 1000) if num < 1e11 else int(num)
        except ValueError:
            pass

        iso_clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso_clean)
        except Exception:
            pass
        else:
            # Carimbo SEM fuso e lido como UTC, que e como os gateways gravam --
            # o mesmo criterio de `models.parse_instant`. Deixar o Python assumir
            # o fuso da maquina fazia este modulo e o models discordarem em horas
            # sobre o MESMO campo: um apagava a trava de rate limit por
            # considera-la vencida enquanto o outro ainda a desenhava na tela.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    return None
