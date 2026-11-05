"""Regularidade do horario de dormir: desvio-padrao circular do horario de
inicio do sono (`daily.sleep_start`), numa janela movel.

Proxy simplificado do Sleep Regularity Index (Phillips et al., 2017): a
versao completa exige concordancia minuto-a-minuto de sono/vigilia entre
dias consecutivos, dado que o farol nao guarda (so sleep_start/sleep_end
agregados por noite). Pesquisa recente (SLEEP, 2024) liga irregularidade de
horario a maior risco cardiometabolico e de mortalidade, de forma
independente de duracao de sono -- ver parecer tecnico de 2026-09-04.

Circular porque o horario de dormir cruza meia-noite: 23h50 e 00h10 sao 20
minutos de diferenca, nao 1420. Um desvio-padrao linear ingenuo infla o
resultado para qualquer atleta que durma perto da meia-noite -- exatamente
o caso mais comum, entao nao e' um detalhe que pode ser ignorado aqui.
"""
import math
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from ..core.config import TZ_OFFSET_HOURS

# Abaixo disso, desvio-padrao circular e' ruido puro, nao regularidade.
_MIN_PONTOS = 4


def _minutos_do_dia_local(epoch_ms: str | int) -> float:
    """Minutos desde a meia-noite local (0-1440), a partir do timestamp
    epoch em ms UTC que o Garmin entrega para sleep_start/sleep_end."""
    dt_utc = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc)
    dt_local = dt_utc + timedelta(hours=TZ_OFFSET_HOURS)
    return dt_local.hour * 60 + dt_local.minute + dt_local.second / 60


def _circular_sd_minutos(minutos: list[float]) -> float | None:
    """Desvio-padrao circular (Fisher, 'Statistical Analysis of Circular
    Data'), convertido de radianos para minutos num relogio de 24h. None se
    R for exatamente 0 (pontos distribuidos de forma perfeitamente oposta --
    SD circular indefinido nesse caso) ou com menos de 2 pontos."""
    if len(minutos) < 2:
        return None
    angulos = [m / 1440 * 2 * math.pi for m in minutos]
    c = sum(math.cos(a) for a in angulos) / len(angulos)
    s = sum(math.sin(a) for a in angulos) / len(angulos)
    r = math.sqrt(c * c + s * s)
    if r <= 0:
        return None
    sd_rad = math.sqrt(-2 * math.log(r))
    return sd_rad * 1440 / (2 * math.pi)


def bedtime_regularity(conn, on_date: str, window_days: int = 14) -> float | None:
    """SD circular (em minutos) do horario de `sleep_start` nos
    `window_days` dias terminando em `on_date` (inclusive -- mesmo criterio
    de trimp_7d/CTL/ATL, que tambem incluem o dia-alvo: e' o proprio valor
    agregado do dia, nao uma baseline para compara-lo contra si mesmo).

    None com menos de `_MIN_PONTOS` dias com `sleep_start` no periodo --
    ~40% de cobertura torna isso comum em janelas curtas, e SD de 2-3 pontos
    nao sustenta a palavra "regularidade"."""
    inicio = (_date.fromisoformat(on_date) - timedelta(days=window_days - 1)).isoformat()
    rows = conn.execute(
        "SELECT sleep_start FROM daily WHERE date >= ? AND date <= ? AND sleep_start IS NOT NULL",
        (inicio, on_date),
    ).fetchall()
    if len(rows) < _MIN_PONTOS:
        return None
    minutos = [_minutos_do_dia_local(r["sleep_start"]) for r in rows]
    sd = _circular_sd_minutos(minutos)
    return round(sd, 1) if sd is not None else None
