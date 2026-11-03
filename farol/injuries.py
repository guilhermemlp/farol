"""Registro manual, cronologico e separado de observacoes de lesao.

Este modulo nao diagnostica e nao participa dos dominios fisiologicos do
Farol. Ele apenas preserva o que o atleta registrou (dor, edema/roxo,
capacidade funcional e tenis) para que dashboard e relatorios possam mostrar
um alerta factual quando a propria observacao indicar piora.
"""
import argparse
import json
from datetime import date as _date
from pathlib import Path

from .config import INJURY_LOG_PATH

_SWELLING = ("nenhum", "leve", "moderado", "intenso")


def _path(path: str | Path | None = None) -> Path:
    return Path(path or INJURY_LOG_PATH)


def _yes_no(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("sim", "s", "yes", "y", "true", "1"):
        return True
    if normalized in ("nao", "não", "n", "no", "false", "0"):
        return False
    raise ValueError("use sim ou nao")


def load_observations(path: str | Path | None = None) -> list[dict]:
    """Le o log local; registros invalidos nunca sao inferidos ou corrigidos."""
    source = _path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    observations = payload.get("observations", [])
    if not isinstance(observations, list):
        raise ValueError("injury_log.json: observations precisa ser uma lista")
    valid = []
    for item in observations:
        if not isinstance(item, dict) or not item.get("date"):
            continue
        _date.fromisoformat(str(item["date"])[:10])
        valid.append(dict(item))
    return sorted(valid, key=lambda item: str(item["date"]))


def observations_as_of(on_date: str, path: str | Path | None = None) -> list[dict]:
    """Observacoes existentes ate a data do relatorio, sem vazamento temporal."""
    return [item for item in load_observations(path) if str(item["date"])[:10] <= on_date]


def latest_observation(on_date: str, path: str | Path | None = None) -> dict | None:
    entries = observations_as_of(on_date, path)
    return entries[-1] if entries else None


def observation_alert(entry: dict | None) -> str | None:
    """Alerta somente o que o proprio registro permite afirmar.

    Sem escala clinica escondida: piora marcada pelo atleta ou incapacidade
    funcional registrada sao os unicos gatilhos exibidos.
    """
    if not entry:
        return None
    if entry.get("worse"):
        return "piora marcada no registro; nao correr enquanto a piora persistir."
    if entry.get("can_run") is False:
        return "capacidade de correr marcada como nao; nao correr ate reavaliar."
    if entry.get("can_walk") is False:
        return "capacidade de caminhar marcada como nao; interrompa a corrida e procure avaliacao."
    return None


def observation_text(entry: dict | None) -> str:
    if not entry:
        return "Sem observacao manual registrada."
    bits = [str(entry.get("area") or "area nao informada")]
    if entry.get("pain") is not None:
        bits.append(f"dor {entry['pain']}/10")
    if entry.get("swelling"):
        bits.append(f"inchaco/roxo: {entry['swelling']}")
    if entry.get("shoe"):
        bits.append(f"tenis: {entry['shoe']}")
    if entry.get("can_walk") is not None:
        bits.append("caminha: sim" if entry["can_walk"] else "caminha: nao")
    if entry.get("can_run") is not None:
        bits.append("corre: sim" if entry["can_run"] else "corre: nao")
    return " | ".join(bits)


def add_observation(entry: dict, path: str | Path | None = None) -> dict:
    """Acrescenta um registro; a CLI e a fonte gravavel do painel estatico."""
    if not entry.get("area"):
        raise ValueError("area e obrigatoria")
    _date.fromisoformat(str(entry["date"])[:10])
    pain = entry.get("pain")
    if pain is not None and not 0 <= int(pain) <= 10:
        raise ValueError("dor precisa estar entre 0 e 10")
    if entry.get("swelling") not in _SWELLING:
        raise ValueError("inchaco/roxo precisa ser: " + ", ".join(_SWELLING))
    source = _path(path)
    entries = load_observations(source)
    normalized = {
        "date": str(entry["date"])[:10], "area": str(entry["area"]),
        "pain": int(pain) if pain is not None else None,
        "swelling": entry["swelling"], "shoe": entry.get("shoe") or None,
        "can_walk": bool(entry["can_walk"]), "can_run": bool(entry["can_run"]),
        "worse": bool(entry.get("worse", False)), "note": entry.get("note") or None,
    }
    entries.append(normalized)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"observations": entries}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Registra uma observacao manual de lesao no Farol.")
    parser.add_argument("add", nargs="?", help="use 'add' para gravar uma observacao")
    parser.add_argument("--date", default=_date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--area", required=True, help="area ou sintoma observado")
    parser.add_argument("--pain", type=int, required=True, help="dor de 0 a 10")
    parser.add_argument("--swelling", default="nenhum", choices=_SWELLING, help="inchaco ou roxo")
    parser.add_argument("--shoe", default=None, help="tenis usado")
    parser.add_argument("--walk", default="sim", help="consegue caminhar? sim/nao")
    parser.add_argument("--run", default="sim", help="consegue correr? sim/nao")
    parser.add_argument("--worse", action="store_true", help="marque se piorou desde a observacao anterior")
    parser.add_argument("--note", default=None, help="nota livre")
    parser.add_argument("--file", default=None, help="arquivo do log (padrao: data/injury_log.json)")
    args = parser.parse_args()
    if args.add != "add":
        parser.error("use: python -m farol.injuries add --area ...")
    try:
        entry = add_observation({
            "date": args.date, "area": args.area, "pain": args.pain,
            "swelling": args.swelling, "shoe": args.shoe,
            "can_walk": _yes_no(args.walk), "can_run": _yes_no(args.run),
            "worse": args.worse, "note": args.note,
        }, args.file)
    except ValueError as exc:
        parser.error(str(exc))
    print("Observacao registrada: " + observation_text(entry))


if __name__ == "__main__":
    main()
