"""Popula farol_daily_state a partir de daily/activities/training_load.

Vive fora de db.py de proposito: db.py e a camada de persistencia (conexao,
schema) e nao deve depender de analysis.py (logica de dominio). Este modulo e
que combina as duas coisas.
"""
import json
import sqlite3
from datetime import datetime, timedelta

from .analysis import assess_deterioration_detected, assess_output_preserved
from .sleep_regularity import bedtime_regularity

# DOMÍNIO OBSERVAÇÃO (MODEL_V1.0.md): descontinuidades de medição conhecidas.
# Lista fixa e global -- não depende da data da linha, é metadado sobre quais
# campos têm quebra de comparabilidade e a partir de quando.
_MEASUREMENT_BREAKPOINTS = json.dumps({
    "breakpoints": [
        {
            "field": "rhr",
            "detected_on": "2026-06-11",
            "status": "INVALID_COMPARISON",
            "reason": "pipeline_migration",
            "baseline_policy": "split_by_source",
            "magnitude": 5.9,
        },
        {
            "field": "awakenings",
            "detected_on": "2026-08-01",
            "status": "INVALID_COMPARISON",
            "reason": "garmin_api_definition_change",
            "baseline_policy": "split_by_date",
            "note": "19x distribution shift",
        },
        {
            "field": "sleep_score",
            "detected_on": "2026-08-01",
            "status": "INVALID_COMPARISON",
            "reason": "garmin_api_definition_change",
            "baseline_policy": "split_by_date",
            "note": "mesma atualizacao da API do Garmin que mudou awakenings; deslocamento pequeno na media do historico real (59,3 -> 56,6; CUSUM sem candidata >= 1.0 SD), mas definicao mudou -- baseline nao cruza 2026-08-01 (farol.metrics.DATE_SPLITS)",
        },
    ]
})


def build_daily_state(conn: sqlite3.Connection, date: str) -> None:
    """
    Populate farol_daily_state for a given date.

    Fetches 23 fields across 5 domains:
    - CARGA (6): trimp_7d, trimp_28d, ctl, atl, tsb, atl_ctl_ratio
    - RECUPERAÇÃO (9): hrv_avg, rhr, night_hr_avg, stress_avg, body_battery_max,
      body_battery_min, respiration_avg, spo2_avg, spo2_min
    - SONO (6): sleep_total_min, awake_minutes, deep_sleep_min, rem_sleep_min, sleep_score,
      sleep_regularity_min (SD circular de sleep_start, janela de 14 dias -- ver
      farol.sleep_regularity; derivado, como trimp_7d/ctl/atl, não coluna direta de `daily`)
    - PERFORMANCE (2): output_preserved, deterioration_detected (NULL sem treino
      intervalado no dia -- ver farol.analysis)
    - OBSERVAÇÃO (1): measurement_breakpoints (lista fixa de descontinuidades
      conhecidas, igual para todas as datas)

    Uses INSERT ON CONFLICT for idempotency.
    Silently skips if no daily record exists for the date.
    """
    daily = conn.execute("SELECT * FROM daily WHERE date = ?", (date,)).fetchone()
    if not daily:
        return

    # TRIMP 7d and 28d aggregations. Janela fechada [D-(N-1), D] tem
    # exatamente N dias incluindo D -- por isso 7d usa days=6 e 28d usa
    # days=27 (nao 28: off-by-one corrigido, achado da auditoria de
    # 2026-09-08 -- a janela anterior cobria 29 dias). Campo so exibido
    # (state.py: "nao usados para status -- so exibidos"), nao entra em
    # CTL/ATL/TSB nem em nenhuma classificacao de dominio.
    date_obj = datetime.fromisoformat(date)
    date_7d_ago = (date_obj - timedelta(days=6)).date().isoformat()
    date_28d_ago = (date_obj - timedelta(days=27)).date().isoformat()

    trimp_7d = conn.execute(
        "SELECT COALESCE(SUM(trimp), 0) FROM activities WHERE date >= ? AND date <= ? AND trimp IS NOT NULL",
        (date_7d_ago, date)
    ).fetchone()[0]

    trimp_28d = conn.execute(
        "SELECT COALESCE(SUM(trimp), 0) FROM activities WHERE date >= ? AND date <= ? AND trimp IS NOT NULL",
        (date_28d_ago, date)
    ).fetchone()[0]

    # CTL, ATL, TSB from training_load
    tl = conn.execute("SELECT ctl, atl, tsb FROM training_load WHERE date = ?", (date,)).fetchone()
    ctl = tl['ctl'] if tl else None
    atl = tl['atl'] if tl else None
    tsb = tl['tsb'] if tl else None
    atl_ctl_ratio = (atl / ctl) if (ctl and atl and ctl > 0) else None

    # Direct reads from daily (RECUPERAÇÃO + SONO)
    hrv_avg = daily['hrv_avg']
    rhr = daily['rhr']
    night_hr_avg = daily['night_hr_avg']
    stress_avg = daily['stress_avg']
    body_battery_max = daily['body_battery_max']
    body_battery_min = daily['body_battery_min']
    respiration_avg = daily['respiration_avg']
    spo2_avg = daily['spo2_avg']
    spo2_min = daily['spo2_min']
    sleep_total_min = daily['total_sleep_minutes']
    awake_minutes = daily['awake_minutes']
    deep_sleep_min = daily['deep_sleep_minutes']
    rem_sleep_min = daily['rem_sleep_minutes']
    sleep_score = daily['sleep_score']
    sleep_regularity_min = bedtime_regularity(conn, date)

    # PERFORMANCE + OBSERVAÇÃO
    output_preserved = assess_output_preserved(conn, date)
    deterioration_detected = assess_deterioration_detected(conn, date)
    measurement_breakpoints = _MEASUREMENT_BREAKPOINTS

    # INSERT with idempotency
    conn.execute(
        """
        INSERT INTO farol_daily_state (
            date, trimp_7d, trimp_28d, ctl, atl, tsb, atl_ctl_ratio,
            hrv_avg, rhr, night_hr_avg, stress_avg, body_battery_max,
            body_battery_min, respiration_avg, spo2_avg, spo2_min,
            sleep_total_min, awake_minutes, deep_sleep_min, rem_sleep_min, sleep_score,
            sleep_regularity_min,
            output_preserved, deterioration_detected, measurement_breakpoints
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            trimp_7d = excluded.trimp_7d,
            trimp_28d = excluded.trimp_28d,
            ctl = excluded.ctl,
            atl = excluded.atl,
            tsb = excluded.tsb,
            atl_ctl_ratio = excluded.atl_ctl_ratio,
            hrv_avg = excluded.hrv_avg,
            rhr = excluded.rhr,
            night_hr_avg = excluded.night_hr_avg,
            stress_avg = excluded.stress_avg,
            body_battery_max = excluded.body_battery_max,
            body_battery_min = excluded.body_battery_min,
            respiration_avg = excluded.respiration_avg,
            spo2_avg = excluded.spo2_avg,
            spo2_min = excluded.spo2_min,
            sleep_total_min = excluded.sleep_total_min,
            awake_minutes = excluded.awake_minutes,
            deep_sleep_min = excluded.deep_sleep_min,
            rem_sleep_min = excluded.rem_sleep_min,
            sleep_score = excluded.sleep_score,
            sleep_regularity_min = excluded.sleep_regularity_min,
            output_preserved = excluded.output_preserved,
            deterioration_detected = excluded.deterioration_detected,
            measurement_breakpoints = excluded.measurement_breakpoints
        """,
        (date, trimp_7d, trimp_28d, ctl, atl, tsb, atl_ctl_ratio,
         hrv_avg, rhr, night_hr_avg, stress_avg, body_battery_max,
         body_battery_min, respiration_avg, spo2_avg, spo2_min,
         sleep_total_min, awake_minutes, deep_sleep_min, rem_sleep_min, sleep_score,
         sleep_regularity_min,
         output_preserved, deterioration_detected, measurement_breakpoints)
    )
    conn.commit()
