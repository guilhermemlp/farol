"""Calibracao automatica entre o indice do Farol e o Training Readiness.

Nao usa percepcao inferida, nao muda pesos e nao toca no banco. Para cada dia
com cobertura dos dois lados, compara o indice de recuperacao do Farol com o
Training Readiness do Garmin. Ambos sao classificados contra suas proprias
baselines pessoais, calculadas como eram naquele dia; portanto uma analise
historica nao enxerga dados futuros.
"""
import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from . import calibration, db
from .config import REPORTS_DIR, load_profile
from .metrics import daily_metric
from .recovery import recovery_status

_OUT_PATH = REPORTS_DIR / "CALIBRACAO_AUTOMATICA.md"
_STATUS = ("ok", "atencao", "alerta")
_SEVERITY = {"ok": 0, "atencao": 1, "alerta": 2}


@dataclass(frozen=True)
class Pair:
    date: str
    farol: str
    garmin: str


def paired_days(conn, profile: dict, on_date: str) -> list[Pair]:
    """Pares comparaveis ate ``on_date``, sem estimar cobertura ausente."""
    rows = conn.execute(
        "SELECT date FROM daily WHERE date <= ? AND training_readiness_score IS NOT NULL "
        "ORDER BY date",
        (on_date,),
    ).fetchall()
    pairs = []
    for row in rows:
        day = row["date"]
        index, _, _ = calibration._indice_do_dia(conn, day, profile)
        farol = recovery_status(index)
        garmin = daily_metric(conn, "training_readiness_score", day).status
        if farol in _STATUS and garmin in _STATUS:
            pairs.append(Pair(day, farol, garmin))
    return pairs


def _kappa(pairs: list[Pair]) -> float | None:
    """Kappa de Cohen descritivo; nao produz p-valor para serie diaria."""
    n = len(pairs)
    if n < 2:
        return None
    farol = Counter(pair.farol for pair in pairs)
    garmin = Counter(pair.garmin for pair in pairs)
    observed = sum(pair.farol == pair.garmin for pair in pairs) / n
    expected = sum(farol[status] * garmin[status] for status in _STATUS) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def build_report(conn, profile: dict, on_date: str) -> str:
    pairs = paired_days(conn, profile, on_date)
    availability = conn.execute(
        "SELECT COUNT(*) FROM daily WHERE date <= ? AND training_readiness_score IS NOT NULL",
        (on_date,),
    ).fetchone()[0]
    lines = [
        "# CALIBRACAO AUTOMATICA", "",
        f"Gerada ate {on_date}. Compara o indice de recuperacao do Farol com o "
        "Training Readiness do Garmin somente quando ambos possuem classificacao "
        "contra a propria baseline pessoal daquele dia.", "",
        "## COBERTURA", "",
        f"- Dias com Training Readiness no Garmin: {availability}.",
        f"- Pares comparaveis Farol × Garmin: {len(pairs)}.",
    ]
    if not pairs:
        lines += ["", "Ainda nao ha pares com baseline suficiente dos dois lados. "
                  "Ausencia de par e cobertura ausente, nao concordancia."]
        return "\n".join(lines) + "\n"

    matches = sum(pair.farol == pair.garmin for pair in pairs)
    lines += ["", "## CONCORDANCIA", "",
             f"- Concordancia bruta: {matches}/{len(pairs)} ({matches / len(pairs):.0%})."]
    kappa = _kappa(pairs)
    if kappa is None:
        lines.append("- Kappa: sem variacao de categorias suficiente para ajustar a concordancia ao acaso.")
    else:
        lines.append(f"- Kappa de Cohen (descritivo): {kappa:+.2f}.")

    matrix = {farol: {garmin: 0 for garmin in _STATUS} for farol in _STATUS}
    for pair in pairs:
        matrix[pair.farol][pair.garmin] += 1
    lines += ["", "## MATRIZ DE CLASSIFICACAO", "",
             "| Farol \\ Garmin | ok | atencao | alerta |",
             "|---|---:|---:|---:|"]
    for farol in _STATUS:
        lines.append("| " + farol + " | " + " | ".join(
            str(matrix[farol][garmin]) for garmin in _STATUS) + " |")

    farol_more_cautious = sum(_SEVERITY[pair.farol] > _SEVERITY[pair.garmin] for pair in pairs)
    garmin_more_cautious = sum(_SEVERITY[pair.garmin] > _SEVERITY[pair.farol] for pair in pairs)
    latest = pairs[-1]
    lines += ["", "## DIFERENCAS", "",
             f"- Farol mais cauteloso que Garmin: {farol_more_cautious} dia(s).",
             f"- Garmin mais cauteloso que Farol: {garmin_more_cautious} dia(s).",
             f"- Ultimo par: {latest.date} — Farol {latest.farol}; Garmin {latest.garmin}.",
             "", "## LIMITES", "",
             "- O Garmin e um segundo juiz, nao uma verdade clinica ou um alvo a copiar.",
             "- Os pares sao diarios e autocorrelacionados; concordancia e kappa descrevem, nao provam validade causal.",
             "- Esta rotina nao altera pesos, limiares, baselines, TSB ou classificacoes. Qualquer revisao do modelo exige decisao explicita e testes."]
    return "\n".join(lines) + "\n"


def write_report(conn, profile: dict, on_date: str, path: str | Path | None = None) -> Path:
    target = Path(path or _OUT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(conn, profile, on_date), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera a calibracao automatica Farol x Training Readiness Garmin.")
    parser.add_argument("--date", default=None, help="Data YYYY-MM-DD (padrao: hoje)")
    parser.add_argument("--out", default=str(_OUT_PATH), help="Caminho de saida")
    args = parser.parse_args()
    profile = load_profile()
    on_date = args.date or _date.today().isoformat()
    conn = db.connect_readonly()
    try:
        target = write_report(conn, profile, on_date, args.out)
    finally:
        conn.close()
    print(f"Calibracao automatica ate {on_date} gravada em {target}")


if __name__ == "__main__":
    main()
