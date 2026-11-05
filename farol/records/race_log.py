"""Registro manual de contexto e percepção pós-prova.

O Garmin é fonte para métricas objetivas de atividade. Este arquivo preserva
o contexto que ele não mede — clima percebido, hidratação, nutrição realmente
consumida, RPE, sintomas e condição do pé — sem transformar relato em métrica
fisiológica, causa ou prescrição.
"""
import argparse
import json
from datetime import date as _date
from pathlib import Path

from ..core.config import RACE_LOG_PATH


def _path(path: str | Path | None = None) -> Path:
    return Path(path or RACE_LOG_PATH)


def load_entries(path: str | Path | None = None) -> list[dict]:
    source = _path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    entries = payload.get("races", [])
    if not isinstance(entries, list):
        raise ValueError("race_log.json: races precisa ser uma lista")
    valid = []
    for item in entries:
        if not isinstance(item, dict) or not item.get("date"):
            continue
        _date.fromisoformat(str(item["date"])[:10])
        valid.append(dict(item))
    return sorted(valid, key=lambda item: str(item["date"]))


def entry_for_date(on_date: str, path: str | Path | None = None) -> dict | None:
    """Registro daquela prova; não usa uma anotação posterior em relatório histórico."""
    return next((entry for entry in load_entries(path) if str(entry["date"])[:10] == on_date), None)


def entry_as_of(race_date: str, on_date: str, path: str | Path | None = None) -> dict | None:
    return entry_for_date(race_date, path) if race_date <= on_date else None


def _optional_number(value: str | None, label: str, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} precisa estar entre {minimum} e {maximum}")
    return number


def _optional_float(value: str | float | None, label: str, minimum: float,
                    maximum: float) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} precisa estar entre {minimum} e {maximum}")
    return number


def add_entry(entry: dict, path: str | Path | None = None) -> dict:
    """Inclui ou substitui o relato de uma mesma data, sem duplicar prova."""
    if not entry.get("date"):
        raise ValueError("data e obrigatoria")
    date = str(entry["date"])[:10]
    _date.fromisoformat(date)
    normalized = {
        "date": date,
        "course": entry.get("course") or None,
        "weather": entry.get("weather") or None,
        "temperature_c": _optional_float(entry.get("temperature_c"), "temperatura", -20, 60),
        "water_ml": _optional_number(entry.get("water_ml"), "agua", 0, 10000),
        "nutrition_taken": entry.get("nutrition_taken") or None,
        "nutrition_carbohydrate_g": _optional_number(
            entry.get("nutrition_carbohydrate_g"), "carboidrato da nutricao", 0, 2000),
        "nutrition_caffeine_mg": _optional_number(
            entry.get("nutrition_caffeine_mg"), "cafeina da nutricao", 0, 1000),
        "rpe": _optional_number(entry.get("rpe"), "RPE", 0, 10),
        "gi_symptoms": entry.get("gi_symptoms") or None,
        "foot_pain": _optional_number(entry.get("foot_pain"), "dor no pe", 0, 10),
        "foot_swelling": entry.get("foot_swelling") or None,
        "shoe": entry.get("shoe") or None,
        "notes": entry.get("notes") or None,
    }
    source = _path(path)
    entries = [item for item in load_entries(source) if str(item["date"])[:10] != date]
    entries.append(normalized)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"races": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def summary(entry: dict | None) -> str:
    if not entry:
        return "Sem contexto manual registrado para esta prova."
    bits = []
    for label, key in (("percurso", "course"), ("clima", "weather"), ("nutricao", "nutrition_taken"),
                       ("GI", "gi_symptoms"), ("tenis", "shoe")):
        if entry.get(key):
            bits.append(f"{label}: {entry[key]}")
    if entry.get("temperature_c") is not None:
        bits.append(f"temperatura: {entry['temperature_c']}°C")
    if entry.get("water_ml") is not None:
        bits.append(f"agua: {entry['water_ml']} ml")
    if entry.get("nutrition_carbohydrate_g") is not None:
        bits.append(f"carboidrato declarado: {entry['nutrition_carbohydrate_g']} g")
    if entry.get("nutrition_caffeine_mg") is not None:
        bits.append(f"cafeina declarada: {entry['nutrition_caffeine_mg']} mg")
    if entry.get("rpe") is not None:
        bits.append(f"RPE: {entry['rpe']}/10")
    if entry.get("foot_pain") is not None:
        bits.append(f"dor no pe: {entry['foot_pain']}/10")
    if entry.get("foot_swelling"):
        bits.append(f"inchaco/roxo: {entry['foot_swelling']}")
    if entry.get("notes"):
        bits.append(f"nota: {entry['notes']}")
    return " | ".join(bits) if bits else "Registro manual sem campos preenchidos."


def main() -> None:
    parser = argparse.ArgumentParser(description="Registra o contexto manual pós-prova no Farol.")
    parser.add_argument("add", nargs="?", help="use 'add' para gravar ou atualizar uma prova")
    parser.add_argument("--date", required=True, help="data da prova (YYYY-MM-DD)")
    parser.add_argument("--course", default=None, help="percurso/altimetria percebida")
    parser.add_argument("--weather", default=None, help="clima percebido")
    parser.add_argument("--temperature-c", default=None, help="temperatura percebida em °C")
    parser.add_argument("--water-ml", default=None, help="agua consumida em ml")
    parser.add_argument("--nutrition", default=None, help="geis, cafe e alimentacao realmente consumidos")
    parser.add_argument("--nutrition-carbs-g", default=None, help="carboidrato total declarado da nutricao")
    parser.add_argument("--nutrition-caffeine-mg", default=None, help="cafeina total declarada da nutricao")
    parser.add_argument("--rpe", default=None, help="esforco percebido de 0 a 10")
    parser.add_argument("--gi", default=None, help="sintomas gastrointestinais ou 'nenhum'")
    parser.add_argument("--foot-pain", default=None, help="dor no pe de 0 a 10")
    parser.add_argument("--foot-swelling", default=None, help="inchaco/roxo observado")
    parser.add_argument("--shoe", default=None, help="tenis usado")
    parser.add_argument("--notes", default=None, help="nota livre")
    parser.add_argument("--file", default=None, help="arquivo do log (padrao: data/race_log.json)")
    args = parser.parse_args()
    if args.add != "add":
        parser.error("use: python -m farol.records.race_log add --date YYYY-MM-DD ...")
    try:
        saved = add_entry({
            "date": args.date, "course": args.course, "weather": args.weather,
            "temperature_c": args.temperature_c, "water_ml": args.water_ml,
            "nutrition_taken": args.nutrition,
            "nutrition_carbohydrate_g": args.nutrition_carbs_g,
            "nutrition_caffeine_mg": args.nutrition_caffeine_mg,
            "rpe": args.rpe, "gi_symptoms": args.gi,
            "foot_pain": args.foot_pain, "foot_swelling": args.foot_swelling,
            "shoe": args.shoe, "notes": args.notes,
        }, args.file)
    except ValueError as exc:
        parser.error(str(exc))
    print("Registro pós-prova salvo: " + summary(saved))


if __name__ == "__main__":
    main()
