"""Reconciliação permanente e estritamente read-only dos dados do Farol.

Compara o SQLite atual com a planilha e recalcula apenas materializações que
podem ser obtidas sem tocar no arquivo pessoal. O resultado é JSON estruturado
para inspeção humana; este módulo nunca limpa, atualiza ou faz backfill.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date as today
from pathlib import Path
from typing import Any

import openpyxl

from . import db, sheets
from .config import DB_PATH, SHEET_PATH, load_profile
from .daily_state_builder import build_daily_state
from .training_load import calculate_training_load_rows, trimp


_FIXTURE_CONSTANTS = {
    "hrv_avg": 18.0,
    "stress_avg": 45,
    "body_battery_max": 30,
    "sleep_score": 55,
}


@dataclass(frozen=True)
class Difference:
    table: str
    key: str
    field: str
    db_value: Any
    source_value: Any
    expected_origin: str
    status: str
    reconstructible: bool
    observation: str | None = None
    evidence_level: str = "inconclusivo"


@dataclass
class ReconciliationReport:
    differences: list[Difference] = field(default_factory=list)

    def add(self, **values: Any) -> None:
        self.differences.append(Difference(**values))

    def to_dict(self) -> dict[str, Any]:
        by_table: dict[str, Counter] = defaultdict(Counter)
        by_status: Counter = Counter()
        for item in self.differences:
            by_table[item.table][item.status] += 1
            by_status[item.status] += 1
        return {
            "schema": "farol.reconciliation.v1",
            "summary": {
                "by_table": {table: dict(sorted(counts.items())) for table, counts in sorted(by_table.items())},
                "by_status": dict(sorted(by_status.items())),
            },
            "differences": [asdict(item) for item in self.differences],
        }


def _rows_by_key(ws, key: str) -> dict[str, dict[str, Any]]:
    headers = [cell.value for cell in ws[1]]
    rows = {}
    for values in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(headers, values))
        value = row.get(key)
        if value is not None:
            rows[str(value)[:10]] = row
    return rows


def _load_sheet(path: str) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        health = _rows_by_key(workbook[sheets.SHEET_DAILY], "date")
        sleep = _rows_by_key(workbook[sheets.SHEET_SLEEP], "date")
        hrv = _rows_by_key(workbook[sheets.SHEET_HRV], "date") if sheets.SHEET_HRV in workbook.sheetnames else {}
        activities = _rows_by_key(workbook[sheets.SHEET_ACTIVITIES], "activity_id")
    finally:
        workbook.close()

    daily = {}
    for key in health.keys() | sleep.keys():
        source = {}
        health_row, sleep_row = health.get(key, {}), sleep.get(key, {})
        source.update({db_col: health_row.get(sheet_col) for db_col, sheet_col in sheets._DAILY_FROM_HEALTH.items()})
        source.update({db_col: sleep_row.get(sheet_col) for db_col, sheet_col in sheets._DAILY_FROM_SLEEP.items()})
        daily[key] = source

    activity_rows = {}
    for activity_id, row in activities.items():
        source = {db_col: row.get(sheet_col) for db_col, sheet_col in sheets._ACTIVITY_COLS.items()}
        source["date"] = str(row.get("date"))[:10] if row.get("date") is not None else None
        activity_rows[activity_id] = source

    hrv_rows = {
        key: {db_col: row.get(sheet_col) for db_col, sheet_col in sheets._HRV_COLS.items()}
        for key, row in hrv.items()
    }
    return daily, activity_rows, hrv_rows


def _fixture_evidence(field: str, db_value: Any, source_value: Any) -> tuple[str, str | None]:
    expected = _FIXTURE_CONSTANTS.get(field)
    if source_value is None and expected is not None and db_value == expected:
        return (
            "forte evidência",
            "Valor ausente na planilha coincide com a constante da fixture TestBuildReport em tests/test_state.py; "
            "a coincidência não prova a origem.",
        )
    return "inconclusivo", None


def _add_comparison(
    report: ReconciliationReport,
    *,
    table: str,
    key: str,
    field: str,
    db_value: Any,
    source_value: Any,
    expected_origin: str,
    reconstructible: bool,
    derived: bool = False,
) -> None:
    if db_value == source_value:
        status, evidence, observation = "match", "inconclusivo", None
    else:
        evidence, observation = _fixture_evidence(field, db_value, source_value)
        if observation:
            status = "suspect"
        elif derived:
            status = "derived_difference"
        else:
            status = "different"
    report.add(
        table=table, key=str(key), field=field, db_value=db_value, source_value=source_value,
        expected_origin=expected_origin, status=status, reconstructible=reconstructible,
        observation=observation, evidence_level=evidence,
    )


def _compare_source_table(
    report: ReconciliationReport,
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    source_rows: dict[str, dict],
    origins: dict[str, str],
    reconstructible: bool = True,
) -> None:
    db_rows = {str(row[key_column]): dict(row) for row in conn.execute(f"SELECT * FROM {table}")}
    fields = set(origins)
    for key in sorted(db_rows.keys() | source_rows.keys()):
        db_row, source_row = db_rows.get(key), source_rows.get(key)
        if db_row is None:
            for name in sorted(fields):
                if source_row.get(name) is not None:
                    report.add(table=table, key=key, field=name, db_value=None, source_value=source_row[name],
                               expected_origin=origins[name], status="source_only", reconstructible=reconstructible)
            continue
        if source_row is None:
            for name in sorted(fields):
                value = db_row.get(name)
                if value is None:
                    continue
                evidence, observation = _fixture_evidence(name, value, None)
                report.add(table=table, key=key, field=name, db_value=value, source_value=None,
                           expected_origin=origins[name], status="suspect" if observation else "db_only",
                           reconstructible=reconstructible, observation=observation, evidence_level=evidence)
            continue
        for name in sorted(fields):
            _add_comparison(report, table=table, key=key, field=name, db_value=db_row.get(name),
                            source_value=source_row.get(name), expected_origin=origins[name],
                            reconstructible=reconstructible)


def _compare_activities(
    report: ReconciliationReport,
    conn: sqlite3.Connection,
    source_rows: dict[str, dict],
    daily_source: dict[str, dict],
    hr_max: float | None,
) -> None:
    origins = {name: "imported:planilha.Activities" for name in sheets._ACTIVITY_COLS}
    origins.update({"date": "imported:planilha.Activities", "trimp": "derived:Banister", "source": "derived:activity_id"})
    adjusted = {}
    for key, row in source_rows.items():
        value = dict(row)
        activity_id = int(key)
        if activity_id <= 0:
            value["distance_km"] = None
            value["avg_pace_min_km"] = None
        value["source"] = "garmin" if activity_id > 0 else "legado"
        if hr_max is not None:
            rhr = daily_source.get(value.get("date"), {}).get("rhr")
            value["trimp"] = trimp(value.get("duration_min"), value.get("avg_hr"), rhr, hr_max)
        adjusted[key] = value
    _compare_source_table(report, conn, "activities", "activity_id", adjusted, origins)

    for row in conn.execute("SELECT activity_id, splits FROM activities WHERE splits IS NOT NULL"):
        report.add(
            table="activities", key=str(row["activity_id"]), field="splits", db_value=row["splits"], source_value=None,
            expected_origin="cache:Garmin typed splits", status="unknown", reconstructible=False,
            observation="A planilha guarda laps brutos, enquanto o banco guarda um resumo de trechos; os formatos não são comparáveis.",
        )


def _compare_training_load(report: ReconciliationReport, conn: sqlite3.Connection) -> None:
    first = conn.execute("SELECT MIN(date) AS date FROM daily").fetchone()["date"]
    if first is None:
        return
    end = conn.execute("SELECT MAX(date) AS date FROM daily").fetchone()["date"]
    end = min(end, today.today().isoformat())
    trimp_by_date = {
        row["date"]: row["trimp"]
        for row in conn.execute(
            "SELECT date, COALESCE(SUM(trimp), 0) AS trimp FROM activities WHERE date >= ? AND date <= ? GROUP BY date",
            (first, end),
        )
    }
    expected = {row["date"]: row for row in calculate_training_load_rows(first, end, trimp_by_date)}
    actual = {row["date"]: dict(row) for row in conn.execute("SELECT * FROM training_load")}
    for key in sorted(actual.keys() | expected.keys()):
        if key not in actual:
            for name, value in expected[key].items():
                if name != "date":
                    report.add(table="training_load", key=key, field=name, db_value=None, source_value=value,
                               expected_origin="derived:activities.trimp", status="source_only", reconstructible=True)
        elif key not in expected:
            for name, value in actual[key].items():
                if name != "date" and value is not None:
                    report.add(table="training_load", key=key, field=name, db_value=value, source_value=None,
                               expected_origin="derived:activities.trimp", status="db_only", reconstructible=True)
        else:
            for name in ("trimp_day", "ctl", "atl", "tsb"):
                _add_comparison(report, table="training_load", key=key, field=name,
                                db_value=actual[key][name], source_value=expected[key][name],
                                expected_origin="derived:activities.trimp", reconstructible=True, derived=True)


def _compare_daily_state(report: ReconciliationReport, conn: sqlite3.Connection) -> None:
    rebuilt = sqlite3.connect(":memory:")
    rebuilt.row_factory = sqlite3.Row
    try:
        conn.backup(rebuilt)
        dates = [row["date"] for row in rebuilt.execute("SELECT date FROM daily ORDER BY date")]
        for on_date in dates:
            build_daily_state(rebuilt, on_date)
        expected = {row["date"]: dict(row) for row in rebuilt.execute("SELECT * FROM farol_daily_state")}
    finally:
        rebuilt.close()
    actual = {row["date"]: dict(row) for row in conn.execute("SELECT * FROM farol_daily_state")}
    for key in sorted(actual.keys() | expected.keys()):
        if key not in actual or key not in expected:
            status = "source_only" if key not in actual else "db_only"
            row = expected.get(key, actual.get(key, {}))
            for name, value in row.items():
                if name != "date" and value is not None:
                    report.add(table="farol_daily_state", key=key, field=name,
                               db_value=None if key not in actual else value,
                               source_value=value if key not in actual else None,
                               expected_origin="cache:derived daily state", status=status, reconstructible=True)
            continue
        for name in sorted(set(actual[key]) - {"date"}):
            _add_comparison(report, table="farol_daily_state", key=key, field=name,
                            db_value=actual[key][name], source_value=expected[key][name],
                            expected_origin="cache:derived daily state", reconstructible=True, derived=True)


def reconcile(db_path: str = DB_PATH, sheet_path: str = SHEET_PATH, hr_max: float | None = None) -> ReconciliationReport:
    """Cria um relatório sem modificar ``db_path`` nem a planilha."""
    conn = db.connect_readonly(db_path)
    try:
        daily_source, activities_source, hrv_source = _load_sheet(sheet_path)
        report = ReconciliationReport()
        daily_origins = {
            **{name: "imported:planilha.Daily Health" for name in sheets._DAILY_FROM_HEALTH},
            **{name: "imported:planilha.Sleep" for name in sheets._DAILY_FROM_SLEEP},
        }
        _compare_source_table(report, conn, "daily", "date", daily_source, daily_origins)
        _compare_activities(report, conn, activities_source, daily_source, hr_max)
        _compare_source_table(report, conn, "hrv", "date", hrv_source,
                              {name: "imported:planilha.HRV" for name in sheets._HRV_COLS})
        _compare_training_load(report, conn)
        _compare_daily_state(report, conn)
        return report
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara fontes do Farol sem modificar o banco.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--sheet", default=SHEET_PATH)
    parser.add_argument("--output", type=Path, help="Arquivo JSON opcional; sem ele, escreve no stdout.")
    args = parser.parse_args()
    hr_max = load_profile().get("hr_max")
    payload = json.dumps(reconcile(args.db, args.sheet, hr_max).to_dict(), ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
