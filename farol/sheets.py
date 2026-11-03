"""Planilha garmin_health.xlsx como etapa intermediária do fluxo:
Garmin -> extração -> ingestão na planilha -> banco de dados.

Reaproveita a estrutura de abas que já existe no arquivo (Daily Health,
Sleep, Activities) em vez de recriar do zero, para o histórico que já está
na planilha continuar válido. Abas que não vêm do Garmin (Atividades_API e
Intervals, de Intervals.icu; Charts, gerado por um pipeline anterior) são
removidas — decisão explícita do usuário, 2026-09-02: só ficam dados que
realmente vêm do Garmin ou são calculados a partir deles.

O banco SQLite é sempre recarregado A PARTIR da planilha, não direto do
Garmin — se você editar um valor na planilha à mão, ele entra no banco na
próxima sincronização.
"""
import sqlite3
from pathlib import Path

import openpyxl

SHEET_DAILY = "Daily Health"
SHEET_SLEEP = "Sleep"
SHEET_ACTIVITIES = "Activities"
SHEET_HRV = "HRV"
SHEET_SAUDE = "Saude"
SHEET_TRAINING_LOAD = "Training Load"

_REMOVE_SHEETS = ["Atividades_API", "Intervals", "Charts"]

_HEADERS = {
    SHEET_DAILY: [
        "date", "steps", "floors", "calories_total", "calories_active",
        "resting_hr", "max_hr", "avg_hr", "stress_avg", "stress_duration_minutes",
        "body_battery_max", "body_battery_min", "spo2_avg", "spo2_min",
        "respiration_avg", "intensity_minutes", "hydration_ml", "hrv_avg",
        "hrv_status", "training_readiness_score", "training_status", "vo2max",
        "errors", "recovery_time_minutes",
    ],
    SHEET_SLEEP: [
        "date", "sleep_start", "sleep_end", "total_sleep_minutes",
        "deep_sleep_minutes", "light_sleep_minutes", "rem_sleep_minutes",
        "awake_minutes", "awakenings", "sleep_score", "sleep_quality",
        "avg_sleep_stress", "night_hr_avg", "spo2_avg_sleep", "spo2_min_sleep",
        "spo2_max_sleep",
    ],
    SHEET_ACTIVITIES: [
        "date", "activity_id", "type", "name", "start_time", "duration_minutes",
        "distance_km", "avg_pace_min_km", "avg_speed_kmh", "max_speed_kmh",
        "calories", "training_effect_aerobic", "training_effect_anaerobic",
        "avg_hr", "max_hr", "avg_cadence", "stride_length", "ground_contact_time",
        "vertical_oscillation", "elevation_gain_m", "elevation_loss_m",
        "avg_power", "max_power", "gps", "splits",
        "moving_minutes", "vertical_ratio", "strava_activity_id",
        "historical_source", "running_dynamics_source",
        "rpe", "shoe", "air_temperature_c", "relative_humidity_pct",
        "gap_pace_min_km", "historical_only",
    ],
    SHEET_HRV: ["date", "hrv_avg", "hrv_status", "weekly_avg", "last_night_5min_high", "baseline", "raw"],
    SHEET_SAUDE: [
        "date", "RestingHR", "Stress", "BodyBattery_Max", "BodyBattery_Min",
        "HRV_Avg", "HRV_Status", "TrainingReadiness", "TrainingStatus",
        "RecoveryTime_Min", "VO2Max", "Sleep_Score", "Sleep_TotalMin",
        "Sleep_DeepMin", "Sleep_LightMin", "Sleep_REMMin", "NightHR", "SleepStress",
    ],
    SHEET_TRAINING_LOAD: ["date", "trimp_day", "ctl", "atl", "tsb"],
}


def open_or_create(path: str) -> openpyxl.Workbook:
    if Path(path).exists():
        wb = openpyxl.load_workbook(path)
        for name in _REMOVE_SHEETS:
            if name in wb.sheetnames:
                del wb[name]
        for name, headers in _HEADERS.items():
            if name not in wb.sheetnames:
                ws = wb.create_sheet(name)
                ws.append(headers)
        return wb
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, headers in _HEADERS.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
    return wb


def _header_index(ws) -> dict:
    return {cell.value: i + 1 for i, cell in enumerate(ws[1]) if cell.value}


def _find_row(ws, key_col_idx: int, key_value) -> int | None:
    for row in ws.iter_rows(min_row=2):
        if row[key_col_idx - 1].value == key_value:
            return row[0].row
    return None


def _upsert(ws, key_field: str, data: dict, clear_when_none: tuple[str, ...] = ()) -> None:
    """Upserta uma linha por `key_field`. Valor None não sobrescreve a célula
    (uma sync que não trouxe o campo não apaga o que já estava lá) — exceto
    campos listados em `clear_when_none`: para eles None é estado válido
    (daily.errors = sem falha) e precisa apagar texto de erro antigo."""
    headers = _header_index(ws)
    if key_field not in headers:
        return
    key_col = headers[key_field]
    key_value = data.get(key_field)
    row_num = _find_row(ws, key_col, key_value) or ws.max_row + 1
    for field, value in data.items():
        if field not in headers:
            continue
        if value is None and field not in clear_when_none:
            continue
        # setter (.value), nao cell(..., value=None): openpyxl so limpa uma
        # celula existente via atribuicao -- o argumento value=None do cell()
        # deixa o valor antigo intacto (descoberto ao testar o F9).
        cell = ws.cell(row=row_num, column=headers[field])
        cell.value = value


def _saude_row(daily_row: dict, sleep_row: dict) -> dict:
    """Saude é uma projeção de Daily Health + Sleep, sem chamada nova ao
    Garmin — mesmos dados, outro layout de coluna (herdado do analise-garmin)."""
    return {
        "date": daily_row["date"],
        "RestingHR": daily_row.get("resting_hr"),
        "Stress": daily_row.get("stress_avg"),
        "BodyBattery_Max": daily_row.get("body_battery_max"),
        "BodyBattery_Min": daily_row.get("body_battery_min"),
        "HRV_Avg": daily_row.get("hrv_avg"),
        "HRV_Status": daily_row.get("hrv_status"),
        "TrainingReadiness": daily_row.get("training_readiness_score"),
        "TrainingStatus": daily_row.get("training_status"),
        "RecoveryTime_Min": daily_row.get("recovery_time_minutes"),
        "VO2Max": daily_row.get("vo2max"),
        "Sleep_Score": sleep_row.get("sleep_score"),
        "Sleep_TotalMin": sleep_row.get("total_sleep_minutes"),
        "Sleep_DeepMin": sleep_row.get("deep_sleep_minutes"),
        "Sleep_LightMin": sleep_row.get("light_sleep_minutes"),
        "Sleep_REMMin": sleep_row.get("rem_sleep_minutes"),
        "NightHR": sleep_row.get("night_hr_avg"),
        "SleepStress": sleep_row.get("avg_sleep_stress"),
    }


def write_day(wb: openpyxl.Workbook, daily_row: dict, sleep_row: dict, hrv_row: dict) -> None:
    """`daily_row`/`sleep_row`/`hrv_row` já vêm no vocabulário das colunas
    das abas (farol.garmin_client.fetch_day) — grava direto, sem remapear
    campo. Saude é derivada de daily_row+sleep_row, sem chamada nova."""
    # errors é status da ÚLTIMA sync, não medição: quando o dia sincronizou
    # sem falha (errors None) a célula precisa ser limpa, senão o texto de um
    # endpoint que já voltou a funcionar continua no relatório (F9).
    _upsert(wb[SHEET_DAILY], "date", daily_row, clear_when_none=("errors",))
    _upsert(wb[SHEET_SLEEP], "date", sleep_row)
    _upsert(wb[SHEET_HRV], "date", hrv_row)
    _upsert(wb[SHEET_SAUDE], "date", _saude_row(daily_row, sleep_row))


def write_activity(wb: openpyxl.Workbook, act: dict) -> None:
    """`act` já no vocabulário da aba Activities (farol.garmin_client.fetch_activities)."""
    _upsert(wb[SHEET_ACTIVITIES], "activity_id", act)


def write_training_load(wb: openpyxl.Workbook, rows: list[dict]) -> None:
    for row in rows:
        _upsert(wb[SHEET_TRAINING_LOAD], "date", row)


def _rows(ws) -> list[dict]:
    headers = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        out.append(dict(zip(headers, row)))
    return out


# Mapeamento coluna-do-banco -> coluna-da-planilha. O banco espelha a planilha
# (decisão do usuário, 2026-09-02): toda coluna extraída do Garmin que chega em
# garmin_health.xlsx tem uma coluna correspondente em schema.sql. Onde os dois
# nomes divergem é por compatibilidade com código anterior — ver schema.sql.
_DAILY_FROM_HEALTH = {
    "steps": "steps",
    "floors": "floors",
    "calories_total": "calories_total",
    "calories_active": "calories_active",
    "rhr": "resting_hr",
    "max_hr": "max_hr",
    "avg_hr": "avg_hr",
    "stress_avg": "stress_avg",
    "stress_duration_minutes": "stress_duration_minutes",
    "body_battery_max": "body_battery_max",
    "body_battery_min": "body_battery_min",
    "spo2_avg": "spo2_avg",
    "spo2_min": "spo2_min",
    "respiration_avg": "respiration_avg",
    "intensity_minutes": "intensity_minutes",
    "hydration_ml": "hydration_ml",
    "hrv_avg": "hrv_avg",
    "hrv_status": "hrv_status",
    "training_readiness_score": "training_readiness_score",
    "training_status": "training_status",
    "vo2max": "vo2max",
    "recovery_time_minutes": "recovery_time_minutes",
    "errors": "errors",
}

_DAILY_FROM_SLEEP = {
    "sleep_start": "sleep_start",
    "sleep_end": "sleep_end",
    "total_sleep_minutes": "total_sleep_minutes",
    "deep_sleep_minutes": "deep_sleep_minutes",
    "light_sleep_minutes": "light_sleep_minutes",
    "rem_sleep_minutes": "rem_sleep_minutes",
    "awake_minutes": "awake_minutes",
    "awakenings": "awakenings",
    "sleep_score": "sleep_score",
    "sleep_quality": "sleep_quality",
    "avg_sleep_stress": "avg_sleep_stress",
    "night_hr_avg": "night_hr_avg",
    "spo2_avg_sleep": "spo2_avg_sleep",
    "spo2_min_sleep": "spo2_min_sleep",
    "spo2_max_sleep": "spo2_max_sleep",
}

# `date` e `trimp` ficam de fora: a data é normalizada e o trimp é calculado.
_ACTIVITY_COLS = {
    "type": "type",
    "name": "name",
    "start_time": "start_time",
    "duration_min": "duration_minutes",
    "distance_km": "distance_km",
    "avg_pace_min_km": "avg_pace_min_km",
    "avg_speed_kmh": "avg_speed_kmh",
    "max_speed_kmh": "max_speed_kmh",
    "calories": "calories",
    "training_effect_aerobic": "training_effect_aerobic",
    "training_effect_anaerobic": "training_effect_anaerobic",
    "avg_hr": "avg_hr",
    "max_hr": "max_hr",
    "avg_cadence": "avg_cadence",
    "stride_length": "stride_length",
    "ground_contact_time": "ground_contact_time",
    "vertical_oscillation": "vertical_oscillation",
    "elevation_gain_m": "elevation_gain_m",
    "elevation_loss_m": "elevation_loss_m",
    "avg_power": "avg_power",
    "max_power": "max_power",
    "gps": "gps",
    "moving_minutes": "moving_minutes",
    "vertical_ratio": "vertical_ratio",
    "strava_activity_id": "strava_activity_id",
    "historical_source": "historical_source",
    "running_dynamics_source": "running_dynamics_source",
    "rpe": "rpe",
    "shoe": "shoe",
    "air_temperature_c": "air_temperature_c",
    "relative_humidity_pct": "relative_humidity_pct",
    "gap_pace_min_km": "gap_pace_min_km",
    "historical_only": "historical_only",
    # A planilha guarda a resposta bruta de get_activity_splits (laps de toda
    # modalidade). No banco, a mesma coluna tem outra semântica: é o resumo
    # apenas dos trechos de trabalho de uma corrida intervalada, calculado em
    # ingest.sync_activity_splits. Por isso não a recarregamos daqui.
}

_HRV_COLS = {
    "hrv_avg": "hrv_avg",
    "hrv_status": "hrv_status",
    "weekly_avg": "weekly_avg",
    "last_night_5min_high": "last_night_5min_high",
    "baseline": "baseline",
    "raw": "raw",
}


def _key(value) -> str:
    """Data normalizada para 'YYYY-MM-DD'. openpyxl devolve datetime numa aba e
    str em outra; sem normalizar dos dois lados, o join Daily Health x Sleep
    falha em silêncio e o sono simplesmente não chega ao banco."""
    return str(value)[:10]


def _db_upsert(conn: sqlite3.Connection, table: str, key_col: str, key_val, values: dict) -> None:
    """INSERT ... ON CONFLICT DO UPDATE preservando o valor antigo quando o novo
    é NULL. O histórico da planilha vem de duas fontes (analise-garmin até
    jun/2026, sync atual a partir de 11/06) e uma sincronização que não trouxe
    dado para um dia não deve apagar o que já estava registrado nele."""
    cols = list(values)
    marks = ", ".join(["?"] * (len(cols) + 1))
    sets = ", ".join("%s=COALESCE(excluded.%s, %s)" % (c, c, c) for c in cols)
    conn.execute(
        "INSERT INTO %s (%s, %s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s"
        % (table, key_col, ", ".join(cols), marks, key_col, sets),
        [key_val] + [values[c] for c in cols],
    )


def load_into_db(conn: sqlite3.Connection, wb: openpyxl.Workbook, hr_max: float) -> tuple[int, int]:
    """Lê Daily Health + Sleep + Activities + HRV da planilha e upserta no
    SQLite, coluna a coluna. O banco é sempre recarregado A PARTIR da planilha —
    é ela a fonte de verdade deste fluxo, não a chamada direta ao Garmin. Saude
    é a única aba que não realimenta o banco: é projeção de Daily Health+Sleep,
    não dado novo."""
    from .training_load import trimp  # import local evita ciclo com este módulo

    sleep_by_date = {_key(r["date"]): r for r in _rows(wb[SHEET_SLEEP])}

    n_daily = 0
    for r in _rows(wb[SHEET_DAILY]):
        d = _key(r["date"])
        sleep = sleep_by_date.get(d, {})
        values = {db: r.get(col) for db, col in _DAILY_FROM_HEALTH.items()}
        values.update({db: sleep.get(col) for db, col in _DAILY_FROM_SLEEP.items()})
        _db_upsert(conn, "daily", "date", d, values)
        # errors é status da última sync, não dado fisiológico: para ele o
        # COALESCE do _db_upsert trabalha ao contrário (preservaria texto de
        # erro obsoleto quando a célula vem vazia). Dia sincronizado sem falha
        # precisa zerar errors no banco — senão o relatório avisa de um
        # endpoint que já voltou a funcionar (F9).
        if not r.get("errors"):
            conn.execute("UPDATE daily SET errors = NULL WHERE date = ?", (d,))
        n_daily += 1
    conn.commit()

    if SHEET_HRV in wb.sheetnames:
        for r in _rows(wb[SHEET_HRV]):
            _db_upsert(conn, "hrv", "date", _key(r["date"]),
                       {db: r.get(col) for db, col in _HRV_COLS.items()})
        conn.commit()

    n_act = 0
    for r in _rows(wb[SHEET_ACTIVITIES]):
        if r.get("activity_id") is None:
            continue
        d = _key(r["date"])
        activity_id = int(r["activity_id"])
        rhr_row = conn.execute("SELECT rhr FROM daily WHERE date = ?", (d,)).fetchone()
        rhr = rhr_row["rhr"] if rhr_row else None

        values = {db: r.get(col) for db, col in _ACTIVITY_COLS.items()}
        values["date"] = d
        values["trimp"] = (None if r.get("historical_only") else
                            trimp(r.get("duration_minutes"), r.get("avg_hr"), rhr, hr_max))
        values["source"] = "garmin" if activity_id > 0 else "legado"

        # Os números de distância antigos têm unidades inconsistentes. Apenas
        # a reconciliação com o export do Strava habilita distância/pace.
        if activity_id <= 0 and r.get("historical_source") != "strava_export":
            values["distance_km"] = None
            values["avg_pace_min_km"] = None

        _db_upsert(conn, "activities", "activity_id", activity_id, values)
        if r.get("historical_only"):
            # Uma corrida conhecida apenas pelo export retrospectivo descreve
            # volume, mas não reescreve estados diários já calibrados.
            conn.execute("UPDATE activities SET trimp = NULL WHERE activity_id = ?",
                         (activity_id,))
        if activity_id <= 0 and r.get("historical_source") != "strava_export":
            # COALESCE não pode preservar uma correção antiga se o marcador
            # de proveniência foi removido da fonte de verdade.
            conn.execute(
                "UPDATE activities SET distance_km = NULL, avg_pace_min_km = NULL, "
                "historical_source = NULL WHERE activity_id = ?", (activity_id,))
        n_act += 1
    conn.commit()

    n_act -= _drop_superseded_activities(conn)
    _tag_daily_source(conn)
    return n_daily, n_act


def _drop_superseded_activities(conn: sqlite3.Connection) -> int:
    """Remove uma linha legada somente quando há um provável par Garmin.

    Data igual, sozinha, não prova duplicação: podem existir duas sessões no
    mesmo dia. Duração próxima e FC média compatível identificam os pares da
    migração de junho; quando a FC falta, exigimos também tipo igual. Uma
    corrida incluída só no export retrospectivo é sempre preservada.
    """
    n = conn.execute(
        "DELETE FROM activities WHERE source = 'legado' "
        "AND COALESCE(historical_only, 0) = 0 "
        "AND EXISTS (SELECT 1 FROM activities AS garmin "
        "WHERE garmin.source = 'garmin' AND garmin.date = activities.date "
        "AND activities.duration_min IS NOT NULL "
        "AND garmin.duration_min IS NOT NULL "
        "AND ABS(garmin.duration_min - activities.duration_min) <= 2 "
        "AND ((activities.avg_hr IS NOT NULL AND garmin.avg_hr IS NOT NULL "
        "AND ABS(garmin.avg_hr - activities.avg_hr) <= 3) "
        "OR ((activities.avg_hr IS NULL OR garmin.avg_hr IS NULL) "
        "AND garmin.type = activities.type)))"
    ).rowcount
    conn.commit()
    return n


# Campos que só o sync atual traz. Um dia que tem qualquer um deles veio do
# Garmin; os demais vieram da planilha herdada. É evidência no próprio dado,
# mais confiável que uma data de corte escrita à mão.
_MARCADORES_GARMIN = (
    "hrv_avg", "training_readiness_score", "stress_avg", "spo2_avg", "sleep_score",
)


def _tag_daily_source(conn: sqlite3.Connection) -> None:
    condicao = " OR ".join("%s IS NOT NULL" % c for c in _MARCADORES_GARMIN)
    conn.execute("UPDATE daily SET source = 'legado'")
    conn.execute("UPDATE daily SET source = 'garmin' WHERE " + condicao)
    conn.commit()
