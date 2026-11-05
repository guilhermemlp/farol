"""Relatório retrospectivo de corridas, separado da classificação diária."""

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from ..core.config import REPORTS_DIR
from ..core.db import connect_readonly


FIELDS = (
    ("distance_km", "distância"), ("moving_minutes", "tempo em movimento"),
    ("avg_pace_min_km", "pace"), ("avg_speed_kmh", "velocidade média"),
    ("avg_hr", "FC média"), ("max_hr", "FC máxima"),
    ("avg_cadence", "cadência"), ("stride_length", "comprimento do passo"),
    ("ground_contact_time", "contato com o solo"),
    ("vertical_oscillation", "oscilação vertical"),
    ("vertical_ratio", "razão vertical"), ("elevation_gain_m", "subida"),
    ("elevation_loss_m", "descida"), ("calories", "calorias"),
    ("training_effect_aerobic", "efeito aeróbico Garmin"),
    ("training_effect_anaerobic", "efeito anaeróbico Garmin"),
    ("avg_power", "potência média"), ("max_power", "potência máxima"),
    ("rpe", "percepção de esforço"),
    ("air_temperature_c", "temperatura"),
    ("relative_humidity_pct", "umidade"), ("gap_pace_min_km", "GAP"),
    ("shoe", "tênis"),
)


def _median(rows, field):
    values = [float(row[field]) for row in rows if row[field] is not None]
    return statistics.median(values) if values else None


def _fmt(value, digits=1):
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def build_report(conn, since="2025-11-24", through=None):
    through = through or conn.execute("SELECT MAX(date) FROM activities").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM activities WHERE date BETWEEN ? AND ? "
        "AND type IN ('running', 'treadmill_running') AND distance_km > 0 "
        "AND (source = 'garmin' OR "
        "(source = 'legado' AND historical_source = 'strava_export')) "
        "ORDER BY date, activity_id", (since, through)).fetchall()
    by_month = defaultdict(list)
    by_source = defaultdict(list)
    for row in rows:
        by_month[row["date"][:7]].append(row)
        by_source[row["source"]].append(row)
    total_km = sum(row["distance_km"] for row in rows)
    moving = sum(row["moving_minutes"] or row["duration_min"] or 0 for row in rows)
    unresolved = conn.execute(
        "SELECT date FROM activities WHERE date BETWEEN ? AND ? "
        "AND type IN ('running', 'treadmill_running') AND source = 'legado' "
        "AND historical_source IS NULL ORDER BY date", (since, through)).fetchall()
    lines = ["# Histórico de corridas — Farol", "",
             f"Período: {since} a {through}. {len(rows)} corridas, "
             f"{_fmt(total_km, 2)} km, {_fmt(moving / 60, 2)} h em movimento.", "",
             "## Origem e cobertura", "",
             "| Origem | Corridas | km |", "|---|---:|---:|"]
    for source, group in sorted(by_source.items()):
        lines.append(f"| {source} | {len(group)} | {_fmt(sum(r['distance_km'] for r in group), 2)} |")
    lines.extend(["", "| Métrica | Corridas com dado |", "|---|---:|"])
    for field, label in FIELDS:
        lines.append(f"| {label} | {sum(r[field] is not None and r[field] != '' for r in rows)}/{len(rows)} |")
    lines.extend(["", "## Volume e esforço observados por mês", "",
                  "| Mês | Corridas | km | h em movimento | Pace mediano | GAP mediano | FC mediana | RPE mediano | Temp. mediana (°C) | Subida (m) |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for month, group in sorted(by_month.items()):
        km = sum(r["distance_km"] for r in group)
        hours = sum(r["moving_minutes"] or r["duration_min"] or 0 for r in group) / 60
        ascent = sum(r["elevation_gain_m"] or 0 for r in group)
        lines.append(f"| {month} | {len(group)} | {_fmt(km, 1)} | {_fmt(hours, 1)} | "
                     f"{_fmt(_median(group, 'avg_pace_min_km'), 2)} | "
                     f"{_fmt(_median(group, 'gap_pace_min_km'), 2)} | "
                     f"{_fmt(_median(group, 'avg_hr'), 0)} | "
                     f"{_fmt(_median(group, 'rpe'), 1)} | "
                     f"{_fmt(_median(group, 'air_temperature_c'), 1)} | "
                     f"{_fmt(ascent, 0)} |")
    lines.extend(["", "## Dinâmica de corrida por origem", "",
                  "Medianas das sessões com dado. A fonte legada usa mediana dos registros FIT "
                  "em movimento; a fonte Garmin usa os resumos de atividade da sincronização "
                  "para cadência, passo, contato e oscilação. A razão vertical foi extraída do FIT.", "",
                  "| Origem | n com FIT | Cadência (passos/min) | Passo (cm) | Contato (ms) | Oscilação (cm) | Razão vertical (%) |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for source, group in sorted(by_source.items()):
        lines.append(f"| {source} | {sum(r['running_dynamics_source'] is not None for r in group)} | "
                     f"{_fmt(_median(group, 'avg_cadence'), 0)} | "
                     f"{_fmt(_median(group, 'stride_length'), 1)} | "
                     f"{_fmt(_median(group, 'ground_contact_time'), 0)} | "
                     f"{_fmt(_median(group, 'vertical_oscillation'), 2)} | "
                     f"{_fmt(_median(group, 'vertical_ratio'), 2)} |")
    if unresolved:
        dates = ", ".join(row["date"] for row in unresolved)
        lines.extend(["", f"Pendência de reconciliação: {len(unresolved)} atividade(s) "
                      f"legada(s) sem par no export ({dates}); excluída(s) destes totais."])
    lines.extend(["", "A distância, o pace em movimento, a altimetria e a cadência das corridas "
                  "legadas foram reconciliados com o CSV do Strava. Cadência de Strava em "
                  "passadas/min foi convertida para passos/min (×2). Tempo original da planilha "
                  "foi preservado para TRIMP nas atividades legadas já conhecidas; "
                  "`moving_minutes` guarda o tempo do Strava. A corrida incluída somente "
                  "pelo export tem `historical_only=1` e não altera TRIMP ou estados diários. "
                  "Ausência de dado não equivale a zero.", "",
                  "Esta leitura é descritiva. Fonte, percurso, clima, tênis e tipo de sessão "
                  "mudam ao longo do período. Não há diagnóstico nem indicação de treino. "
                  "As baselines e a classificação diária do Farol seguem separadas.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2025-11-24")
    parser.add_argument("--through")
    parser.add_argument("--out", type=Path, default=Path(REPORTS_DIR) / "RUNNING_HISTORY.md")
    args = parser.parse_args()
    with connect_readonly() as conn:
        report = build_report(conn, since=args.since, through=args.through)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Relatório gerado: {args.out}")


if __name__ == "__main__":
    main()
