"""Análise de treino intervalado: separa os tiros executados (INTERVAL_ACTIVE)
do aquecimento/recuperação/parada, que senão poluem a média da atividade
inteira. Sob demanda (CLI própria) — não faz parte do sync diário, porque
puxa dado extra por atividade (uma chamada a mais por análise).

Uso: python -m farol.pipeline.splits [--activity-id ID]
Sem --activity-id, usa a atividade de corrida mais recente no banco.
"""
import argparse
import json
import sys

from ..core import db
from .garmin_client import login

WORK_TYPES = {"INTERVAL_ACTIVE"}
RETRYABLE_SPLIT_STATUSES = {"pending", "error", "unknown"}


def fetch_typed_splits(g, activity_id: int) -> list[dict]:
    """Retorna typed splits de uma chamada Garmin bem-sucedida.

    Erros de transporte sobem para o chamador. Isso impede que uma falha seja
    confundida com uma resposta válida sem segmentos (``[]``).
    """
    data = g.get_activity_typed_splits(activity_id)
    splits = data.get("splits", [])
    return splits if isinstance(splits, list) else []


def historical_splits_status(payload: str | None) -> str:
    """Classifica payload legado sem mudar o banco.

    JSON ``"null"`` e JSON inválido são ambíguos. Só o resumo estruturado
    conhecido é considerado ``success``; o restante continua ``unknown``.
    """
    if payload is None:
        return "pending"
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        return "unknown"
    if isinstance(parsed, dict) and isinstance(parsed.get("reps"), list):
        return "success"
    return "unknown"


def split_retry_candidates(conn) -> list[int]:
    """Retorna corridas Garmin elegíveis, sem backfill de estados legados."""
    rows = conn.execute(
        "SELECT activity_id, splits, splits_status FROM activities "
        "WHERE type = 'running' AND source = 'garmin' ORDER BY date DESC"
    ).fetchall()
    candidates = []
    for row in rows:
        status = row["splits_status"] or historical_splits_status(row["splits"])
        if status in RETRYABLE_SPLIT_STATUSES:
            candidates.append(row["activity_id"])
    return candidates


def fetch_lap_splits(g, activity_id: int) -> list[dict]:
    """Laps automaticos (tipicamente por km) de QUALQUER corrida --
    diferente de fetch_typed_splits, que so cobre treino intervalado
    estruturado (INTERVAL_ACTIVE). Usado por farol.insights.decoupling, que precisa
    de granularidade intra-corrida que a media da atividade inteira nao
    da. Confirmado contra corrida real (Longo 15km, 2026-08-22): 15 laps de
    1000m + 1 lap final parcial, cada um com distance/duration/averageHR e,
    de bonus, averageTemperature -- ver farol/insights/decoupling.py."""
    try:
        data = g.get_activity_splits(activity_id)
        return data.get("lapDTOs", [])
    except Exception:
        return []


def _media_dos_presentes(reps: list[dict], campo: str, casas: int) -> float | None:
    """Media de `campo` sobre os tiros que TEM esse campo -- nunca dividida
    por n_reps. Duas correcoes sobre a versao anterior (achado da auditoria
    de 2026-09-04, `sum(x for x in reps if x) / n`):

    1. Um campo ausente em TODOS os tiros (sem pod de dinamica de corrida,
       por exemplo) devolvia 0.0 -- silenciosamente diferente de "sem
       dado". Agora devolve None.
    2. Um campo ausente em PARTE dos tiros diluia a media dividindo pelo
       total de tiros, nao pela quantidade que realmente tinha o dado --
       2 tiros, so 1 com avg_power, devolvia metade do valor real. Agora
       divide pela contagem de presentes."""
    presentes = [r[campo] for r in reps if r.get(campo)]
    if not presentes:
        return None
    return round(sum(presentes) / len(presentes), casas)


def summarize_work_segments(splits: list[dict]) -> dict | None:
    """Filtra só os segmentos de trabalho real (ignora aquecimento,
    recuperação, parada, trechos finais soltos). Retorna None se a
    atividade não tiver estrutura de intervalo reconhecida."""
    work = [s for s in splits if s.get("type") in WORK_TYPES]
    if not work:
        return None

    reps = []
    for s in work:
        dist_km = (s.get("distance") or 0) / 1000
        dur_min = (s.get("duration") or 0) / 60
        reps.append(
            {
                "distance_km": round(dist_km, 3),
                "pace_min_km": round(dur_min / dist_km, 2) if dist_km else None,
                "avg_hr": s.get("averageHR"),
                "max_hr": s.get("maxHR"),
                "avg_cadence": s.get("averageRunCadence"),
                "ground_contact_time": s.get("groundContactTime"),
                "vertical_oscillation": s.get("verticalOscillation"),
                "avg_power": s.get("averagePower"),
            }
        )

    n = len(reps)
    total_dist = sum(r["distance_km"] for r in reps)
    total_dur_min = sum((r["pace_min_km"] or 0) * r["distance_km"] for r in reps)
    return {
        "n_reps": n,
        "reps": reps,
        "avg_distance_km": round(total_dist / n, 3),
        "avg_pace_min_km": round(total_dur_min / total_dist, 2) if total_dist else None,
        "avg_hr": _media_dos_presentes(reps, "avg_hr", 1),
        "max_hr": max((r["max_hr"] for r in reps if r["max_hr"]), default=None),
        "avg_cadence": _media_dos_presentes(reps, "avg_cadence", 1),
        "avg_ground_contact_time": _media_dos_presentes(reps, "ground_contact_time", 1),
        "avg_vertical_oscillation": _media_dos_presentes(reps, "vertical_oscillation", 2),
        "avg_power": _media_dos_presentes(reps, "avg_power", 1),
    }


def _most_recent_activity_id(conn) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT activity_id, date FROM activities WHERE type = 'running' AND activity_id > 0 "
        "ORDER BY date DESC, activity_id DESC LIMIT 1"
    ).fetchone()
    return (row["activity_id"], row["date"]) if row else None


def _fmt(valor: float | None, casas: int) -> str:
    """'sem dado' em vez de deixar `:.0f` quebrar com TypeError -- os campos
    de summarize_work_segments agora podem ser None de verdade (ver
    _media_dos_presentes) quando nenhum tiro tem o sensor correspondente."""
    return "sem dado" if valor is None else f"{valor:.{casas}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analisa os tiros executados de um treino intervalado.")
    parser.add_argument("--activity-id", type=int, default=None, help="ID da atividade (padrão: corrida mais recente no banco)")
    args = parser.parse_args()

    conn = db.connect_readonly()
    activity_id = args.activity_id
    activity_date = None
    if activity_id is None:
        found = _most_recent_activity_id(conn)
        if not found:
            print("Nenhuma corrida no banco. Rode farol.pipeline.ingest primeiro.")
            sys.exit(1)
        activity_id, activity_date = found
    conn.close()

    g = login()
    splits = fetch_typed_splits(g, activity_id)
    result = summarize_work_segments(splits)

    if result is None:
        print(f"Atividade {activity_id}{f' ({activity_date})' if activity_date else ''} não tem "
              "estrutura de intervalo reconhecida (sem segmentos INTERVAL_ACTIVE) — "
              "provavelmente foi corrida contínua, não intervalado.")
        return

    print(f"\nTreino executado — atividade {activity_id}{f' ({activity_date})' if activity_date else ''}")
    print(f"{result['n_reps']} tiro(s) de ~{result['avg_distance_km']*1000:.0f}m\n")
    for i, r in enumerate(result["reps"], 1):
        print(f"  Tiro {i}: {r['pace_min_km']:.2f} min/km | FC {r['avg_hr']:.0f}-{r['max_hr']:.0f} | cadência {r['avg_cadence']:.0f}")

    print("\nMédias dos tiros:")
    print(f"  Pace: {result['avg_pace_min_km']:.2f} min/km")
    print(f"  FC: {_fmt(result['avg_hr'], 0)} bpm (máx. {_fmt(result['max_hr'], 0)})")
    print(f"  Cadência: {_fmt(result['avg_cadence'], 1)} spm")
    print(f"  Contato com solo: {_fmt(result['avg_ground_contact_time'], 0)} ms")
    print(f"  Oscilação vertical: {_fmt(result['avg_vertical_oscillation'], 2)} cm")
    print(f"  Potência: {_fmt(result['avg_power'], 0)} W")


if __name__ == "__main__":
    main()
