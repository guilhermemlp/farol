"""Reconcilia um export local do Strava com a aba Activities do Farol.

Uso: python -m farol.pipeline.strava_backfill --zip export.zip [--apply]
Sem --apply apenas confere. A planilha continua sendo a fonte de verdade.
"""

import argparse
import csv
import io
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import openpyxl

from ..core.config import SHEET_PATH
from .fit_dynamics import summarize_dynamics
from .sheets import SHEET_ACTIVITIES


MONTHS = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
SOURCE = "strava_export"


def _date(value: str) -> str:
    match = re.match(r"(\d+) de ([a-z]+)\. de (\d{4})", value)
    if not match:
        raise ValueError("Data desconhecida no export do Strava")
    return date(int(match[3]), MONTHS[match[2]], int(match[1])).isoformat()


def _number(value):
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(",", "."))


def _header(ws):
    return {cell.value: cell.column for cell in ws[1] if cell.value}


def _set(ws, header, row, field, value):
    if value is not None:
        ws.cell(row, header[field]).value = value


def _fit_dynamics(archive, item):
    name = item.get("Nome do arquivo", "")
    if not (name.endswith(".fit.gz") or name.endswith(".fit")) or name not in archive.namelist():
        return {}
    return summarize_dynamics(archive.read(name))


def reconcile(archive_path: Path, sheet_path: Path, *, apply: bool = False,
              since: str = "2025-11-24", through: str | None = None) -> dict:
    through = through or date.today().isoformat()
    with zipfile.ZipFile(archive_path) as archive:
        csv_path = next(n for n in archive.namelist() if n.endswith("activities.csv"))
        items = [item for item in csv.DictReader(io.TextIOWrapper(
            archive.open(csv_path), encoding="utf-8-sig"))
            if item.get("Tipo de atividade") == "Corrida"
            and since <= _date(item["Data da atividade"]) <= through]
        workbook = openpyxl.load_workbook(sheet_path)
        ws = workbook[SHEET_ACTIVITIES]
        header = _header(ws)
        for field in ("moving_minutes", "vertical_ratio", "strava_activity_id",
                      "historical_source", "running_dynamics_source", "rpe",
                      "shoe", "air_temperature_c", "relative_humidity_pct",
                      "gap_pace_min_km", "historical_only"):
            if field not in header:
                col = ws.max_column + 1
                ws.cell(1, col).value = field
                header[field] = col

        existing = defaultdict(list)
        by_strava_id = {}
        for row in range(2, ws.max_row + 1):
            if ws.cell(row, header["type"]).value not in ("running", "treadmill_running"):
                continue
            day = str(ws.cell(row, header["date"]).value)[:10]
            existing[day].append(row)
            sid = ws.cell(row, header["strava_activity_id"]).value
            if sid is not None:
                by_strava_id[str(sid)] = row

        matched = added = fit_count = 0
        added_dates = []
        used_rows = set()
        csv_ids = set()
        for item in items:
            sid = item["ID da atividade"]
            if not sid or sid in csv_ids:
                raise ValueError("ID ausente ou duplicado no export")
            csv_ids.add(sid)
            day = _date(item["Data da atividade"])
            distance = _number(item["Distância"])
            moving = _number(item["Tempo de movimentação"])
            elapsed = _number(item["Tempo decorrido"])
            if not distance or distance <= 0 or not moving or moving <= 0:
                raise ValueError(f"Distância ou tempo ausente no export: {day}")
            moving_min = moving / 60
            row = by_strava_id.get(sid)
            if row is None:
                candidates = []
                for candidate in existing[day]:
                    if candidate in used_rows:
                        continue
                    duration = ws.cell(candidate, header["duration_minutes"]).value
                    if duration is None:
                        continue
                    elapsed_min = elapsed / 60 if elapsed is not None else moving_min
                    error = min(abs(float(duration) - moving_min),
                                abs(float(duration) - elapsed_min))
                    if error <= 2:
                        candidates.append((error, candidate))
                candidates.sort()
                if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 0.5:
                    raise ValueError(f"Atividade ambígua em {day}")
                row = candidates[0][1] if candidates else None
            if row is None:
                row = ws.max_row + 1
                ws.cell(row, header["date"]).value = day
                ws.cell(row, header["activity_id"]).value = -int(sid)
                ws.cell(row, header["type"]).value = "running"
                _set(ws, header, row, "name", item.get("Nome da atividade"))
                _set(ws, header, row, "duration_minutes", round(moving_min, 4))
                _set(ws, header, row, "avg_hr", _number(item.get("Frequência cardíaca média")))
                _set(ws, header, row, "max_hr", _number(item.get("Frequência cardíaca máxima")))
                _set(ws, header, row, "calories", _number(item.get("Calorias")))
                existing[day].append(row)
                added += 1
                added_dates.append(day)
            else:
                matched += 1
            used_rows.add(row)
            activity_id = int(ws.cell(row, header["activity_id"]).value)
            if activity_id == -int(sid):
                _set(ws, header, row, "historical_only", 1)
            _set(ws, header, row, "strava_activity_id", sid)
            _set(ws, header, row, "moving_minutes", round(moving_min, 4))
            _set(ws, header, row, "rpe", _number(item.get("Percepção de esforço")))
            _set(ws, header, row, "shoe", item.get("Equipamento da atividade"))
            _set(ws, header, row, "air_temperature_c",
                 _number(item.get("Temperatura atmosférica")))
            humidity = _number(item.get("Umidade"))
            _set(ws, header, row, "relative_humidity_pct",
                 round(humidity * 100, 1) if humidity is not None else None)
            gap_speed = _number(item.get("Ritmo Ajustado à inclinação média"))
            _set(ws, header, row, "gap_pace_min_km",
                 round(1000 / 60 / gap_speed, 5) if gap_speed and gap_speed > 0 else None)

            if activity_id < 0:
                km = distance / 1000
                _set(ws, header, row, "distance_km", round(km, 5))
                _set(ws, header, row, "avg_pace_min_km", round(moving_min / km, 5))
                _set(ws, header, row, "avg_speed_kmh", round(km * 60 / moving_min, 5))
                _set(ws, header, row, "elevation_gain_m", _number(item.get("Ganho de elevação")))
                _set(ws, header, row, "elevation_loss_m", _number(item.get("Perda de elevação")))
                cadence = _number(item.get("Cadência média"))
                _set(ws, header, row, "avg_cadence", cadence * 2 if cadence else None)
                _set(ws, header, row, "historical_source", SOURCE)

            dynamics = _fit_dynamics(archive, item)
            if dynamics:
                for field, value in dynamics.items():
                    if field == "avg_cadence":
                        continue  # CSV: média da sessão; FIT: mediana dos registros.
                    if activity_id < 0 or field == "vertical_ratio":
                        _set(ws, header, row, field, value)
                _set(ws, header, row, "running_dynamics_source", "fit_in_strava_export")
                fit_count += 1

        sheet_only = [str(ws.cell(row, header["date"]).value)[:10]
                      for rows in existing.values() for row in rows
                      if row not in used_rows
                      and since <= str(ws.cell(row, header["date"]).value)[:10] <= through]
        if apply:
            backup = sheet_path.with_name(sheet_path.name + ".backup_strava")
            if not backup.exists():
                shutil.copy2(sheet_path, backup)
            with tempfile.NamedTemporaryFile(suffix=".xlsx", dir=sheet_path.parent,
                                             delete=False) as temp:
                temp_path = Path(temp.name)
            try:
                workbook.save(temp_path)
                os.replace(temp_path, sheet_path)
            finally:
                temp_path.unlink(missing_ok=True)
        workbook.close()
    return {"csv_runs": len(items), "matched": matched, "added": added,
            "added_dates": added_dates,
            "fit_dynamics": fit_count, "sheet_only_dates": sheet_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--sheet", type=Path, default=Path(SHEET_PATH))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = reconcile(args.zip, args.sheet, apply=args.apply)
    print(f"Corridas no export: {result['csv_runs']}; conciliadas: {result['matched']}; "
          f"novas: {result['added']}; FIT com dinâmica: {result['fit_dynamics']}.")
    print(f"Datas só na planilha: {result['sheet_only_dates']}")
    print(f"Datas de corridas novas: {result['added_dates']}")
    print("Planilha atualizada." if args.apply else "Prévia: planilha não alterada.")


if __name__ == "__main__":
    main()
