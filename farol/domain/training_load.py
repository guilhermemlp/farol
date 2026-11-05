"""TRIMP e carga de treino (CTL/ATL/TSB) via modelo de Banister, local,
sem depender de Intervals.icu. Portado de pulse/pulso_ingest.py.
"""
import math
import sqlite3
from datetime import date as _date
from datetime import timedelta

TAU_CTL = 42
TAU_ATL = 7


def trimp(duration_min: float | None, avg_hr: float | None, rhr: float | None, hr_max: float) -> float:
    """TRIMP de Banister. Precisa de duração, FC média, FC de repouso do dia
    e FC máxima real (de exame, não estimada por idade)."""
    if not duration_min or not avg_hr or rhr is None:
        return 0.0
    r = max(0.0, min(1.0, (avg_hr - rhr) / (hr_max - rhr)))
    return round(duration_min * r * 0.64 * math.exp(1.92 * r), 2)


def calculate_training_load_rows(
    from_date: str,
    end: str,
    trimp_by_date: dict[str, float],
    ctl_prev: float = 0.0,
    atl_prev: float = 0.0,
) -> list[dict]:
    """Calcula CTL/ATL/TSB sem persistir nada.

    A rotina de reconciliação usa esta mesma implementação para verificar a
    materialização existente sem abrir o banco em escrita. A fórmula e os
    arredondamentos são os mesmos de ``recompute_training_load``.
    """
    d = _date.fromisoformat(from_date)
    d_end = _date.fromisoformat(end)
    out = []
    while d <= d_end:
        ds = d.isoformat()
        load = float(trimp_by_date.get(ds, 0.0))
        ctl = ctl_prev * (1 - 1 / TAU_CTL) + load * (1 / TAU_CTL)
        atl = atl_prev * (1 - 1 / TAU_ATL) + load * (1 / TAU_ATL)
        ctl_r, atl_r = round(ctl, 2), round(atl, 2)
        tsb_r = round(ctl_r - atl_r, 2)
        out.append({"date": ds, "trimp_day": round(load, 2), "ctl": ctl_r, "atl": atl_r, "tsb": tsb_r})
        ctl_prev, atl_prev = ctl, atl
        d += timedelta(days=1)
    return out


def recompute_training_load(conn: sqlite3.Connection, from_date: str) -> list[dict]:
    """Recomputa CTL/ATL/TSB de `from_date` até o último dia com dado em
    `daily`, somando TRIMP por dia (dias sem atividade contam como 0).
    O ponto de partida (seed) vem da última linha de training_load antes de
    `from_date` (0/0 se não houver histórico). Idempotente — rodar de novo
    para o mesmo intervalo reescreve os mesmos valores."""
    seed = conn.execute(
        "SELECT ctl, atl FROM training_load WHERE date < ? ORDER BY date DESC LIMIT 1",
        (from_date,),
    ).fetchone()
    ctl_prev, atl_prev = (seed["ctl"], seed["atl"]) if seed else (0.0, 0.0)

    end = conn.execute("SELECT MAX(date) AS d FROM daily").fetchone()["d"]
    if not end:
        return []
    # trava contra data futura/errada em `daily` (a planilha é editável à mão)
    # -- sem isso, uma data ruim faz este loop tentar cobrir décadas.
    today_str = _date.today().isoformat()
    if end > today_str:
        end = today_str

    rows = conn.execute(
        "SELECT date, COALESCE(SUM(trimp), 0) AS t FROM activities "
        "WHERE date >= ? AND date <= ? GROUP BY date",
        (from_date, end),
    ).fetchall()
    trimp_by_date = {r["date"]: r["t"] for r in rows}

    out = calculate_training_load_rows(from_date, end, trimp_by_date, ctl_prev, atl_prev)
    for row in out:
        conn.execute(
            "INSERT INTO training_load (date, trimp_day, ctl, atl, tsb) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET trimp_day=excluded.trimp_day, ctl=excluded.ctl, "
            "atl=excluded.atl, tsb=excluded.tsb",
            (row["date"], row["trimp_day"], row["ctl"], row["atl"], row["tsb"]),
        )
    conn.commit()
    return out
