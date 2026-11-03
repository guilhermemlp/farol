"""CLI: gera e acrescenta a entrada do dia em CALIBRACAO_LOG.txt (Fase 12).

So leitura do banco -- nao escreve em farol_daily_state nem em nenhuma outra
tabela, e nao recalcula nada: reaproveita farol.state.gather() (dominios,
convergencia, streak/trend de farol.longitudinal) e farol.today.recovery_index
(indice unico), exatamente como farol.today e farol.state ja calculam.

Divergencia de CALIBRACAO.md, registrada aqui em vez de reescrever um arquivo
ja congelado: CALIBRACAO.md descreve uma linha por dia com o indice unico do
farol.today -- rotina escrita antes do MODEL_V1.0 (congelado em 2026-09-03) e
de farol.longitudinal existirem. O MODEL_V1.0 e o modelo em vigor hoje, entao
a entrada abaixo registra tambem o estado por dominio e os sinais
longitudinais que DAILY_STATE.md/DAILY_ANALYSIS.md realmente produzem, sem
descartar o indice (mantido por continuidade com a rotina de CALIBRACAO.md).
O principio central de CALIBRACAO.md continua valendo: nada aqui e preenchido
a partir da percepcao do usuario -- PERCEPCAO REAL e BATEU COM A REALIDADE?
ficam em branco, para preenchimento manual, e sao os unicos campos que
importam para uma futura decisao sobre o modelo (so depois de ~14 dias).

Segundo juiz (2026-09): cada entrada tambem compara o indice do farol contra
o training_readiness_score do proprio Garmin (outro algoritmo, mesmo tipo de
sinal de entrada), classificado pela mesma janela pessoal de 90 dias que
qualquer outra metrica do farol usa -- nunca o limiar generico do Garmin.

Uso: python -m farol.calibration [--date YYYY-MM-DD] [--out CAMINHO]
"""
import argparse
import sys
from datetime import date as _date
from pathlib import Path

from . import db, state
from .config import REPORTS_DIR, load_profile
from .metrics import daily_metric, from_daily
from .recovery import PESOS_PADRAO, recovery_index, recovery_label, recovery_status

_LOG_PATH = REPORTS_DIR / "CALIBRACAO_LOG.txt"
_SEP = "=" * 60
_DOMINIOS = ("CARGA", "RECUPERACAO", "SONO")

_CABECALHO = """\
# CALIBRACAO_LOG.txt -- Fase 12 (ver CALIBRACAO.md)
#
# Uma entrada por dia, gerada por 'python -m farol.calibration' depois de
# 'python -m farol.ingest --days 2'. So leitura do banco -- nunca altera
# farol_daily_state nem nenhuma outra tabela.
#
# Divergencia registrada (farol/calibration.py, docstring do modulo):
# CALIBRACAO.md descreve uma linha por dia com o indice unico de farol.today
# -- escrito antes do MODEL_V1.0 (congelado em 2026-09-03) e de
# farol.longitudinal existirem. As entradas abaixo registram tambem o estado
# por dominio e os sinais longitudinais que o modelo congelado produz hoje,
# alem do indice.
#
# Os campos de percepcao real sao preenchidos a mao. BATEU COM A REALIDADE?
# alimenta o resumo manual depois de ~14 entradas; a nota numerica opcional
# permite associacoes exploratorias de subscores depois de 30 dias pareados.
# Nenhum dos dois campos altera pesos automaticamente ou decide o modelo
# a partir de uma entrada isolada.
#
# GARMIN e' um segundo juiz automatico (training_readiness_score, algoritmo
# do proprio Garmin), classificado pela mesma janela pessoal de 90 dias do
# farol -- CONCORDANCIA COM O INDICE FAROL diz so se os dois buckets
# (ok/atencao/alerta) coincidem, nunca se um dos dois esta "certo".

"""


def _indice_do_dia(conn, on_date: str, profile: dict) -> tuple[float | None, str, dict]:
    """Mesmas metricas e mesma chamada de farol.today.main() -- nao
    reimplementa o calculo do indice, so o reusa."""
    metricas = {
        "hrv_avg": daily_metric(conn, "hrv_avg", on_date),
        "rhr": daily_metric(conn, "rhr", on_date),
        "sleep_score": daily_metric(conn, "sleep_score", on_date),
        "night_hr_avg": daily_metric(conn, "night_hr_avg", on_date),
        "total_sleep_minutes": daily_metric(conn, "total_sleep_minutes", on_date),
        "tsb": from_daily(conn, "tsb", on_date, higher_is_better=True, table="training_load"),
    }
    pesos = ((profile.get("recovery_index") or {}).get("weights")) or PESOS_PADRAO
    indice, notas = recovery_index(metricas, pesos)
    return indice, recovery_label(indice), notas


def _concordancia_garmin(status_farol: str, status_garmin: str) -> str:
    """Compara o bucket do indice do farol (recovery_status) com
    Metric.status do training_readiness_score do Garmin -- mesmo vocabulario
    ok/atencao/alerta/sem_dado, os dois vindos de z-score contra a janela
    pessoal de 90 dias (nunca limiar generico do Garmin: seria repetir aqui
    o erro que o resto do farol evita, PROFILE.md regra 10). 'sem dado' de
    qualquer lado impede comparacao -- nao vira 'concorda' por omissao."""
    if status_farol == "sem_dado" or status_garmin == "sem_dado":
        return "sem dado suficiente para comparar"
    if status_farol == status_garmin:
        return "concorda"
    return f"diverge (farol={status_farol}, garmin={status_garmin})"


def _readiness_txt(m) -> str:
    if not m.has_data:
        return "sem dado hoje (campo tem ~40% de cobertura)"
    baseline = f"{m.mean_90d:g}" if m.mean_90d is not None else "sem baseline (n insuficiente)"
    z = f"{m.z:+.2f}" if m.z is not None else "—"
    return f"{m.value:g} | normal {baseline} | z {z} | {m.status}"


def _streak_txt(streak: dict) -> str:
    if streak["dias"] == 0:
        return "sem streak ativo"
    dias = streak["dias"]
    return f"{dias} {'dia' if dias == 1 else 'dias'} consecutivos (desde {streak['data_inicio']})"


def build_entry(conn, on_date: str, profile: dict) -> dict:
    """Le o que o Farol ja calculou para on_date -- indice (farol.today) e
    dominios/convergencia/streak (farol.state.gather, que ja embute
    farol.longitudinal) -- sem recalcular nada e sem escrever no banco.
    ValueError se nao ha farol_daily_state para a data (mesmo erro de
    state.gather)."""
    indice, indice_label, componentes = _indice_do_dia(conn, on_date, profile)
    dados = state.gather(conn, on_date)
    dominios = dados["dominios"]

    op = dados["performance"].get("output_preserved")
    dd = dados["performance"].get("deterioration_detected")
    performance_txt = (f"output_preserved={op}, deterioration_detected={dd}"
                        if op is not None else "sem dado (sem treino intervalado no dia)")

    convergencia_txt = state.convergence({n: d["status"] for n, d in dominios.items()}) \
        or "nenhum padrao (no maximo 1 dominio fora do normal)"

    # Segundo juiz: o training_readiness_score e' calculado pelo proprio
    # Garmin, por outro algoritmo, a partir do mesmo tipo de sinal (HRV, sono,
    # carga aguda). Ate agora o unico "segundo juiz" do indice era a
    # percepcao manual, abaixo. daily_metric() aplica a mesma janela pessoal
    # de 90 dias que qualquer outra metrica do farol -- nao o limiar
    # generico do Garmin.
    readiness = daily_metric(conn, "training_readiness_score", on_date)
    concordancia_garmin = _concordancia_garmin(recovery_status(indice), readiness.status)

    return {
        "date": on_date,
        "indice": indice,
        "indice_label": indice_label,
        "componentes": componentes,
        "dominios": {n: state._LABEL[dominios[n]["status"]][0] for n in _DOMINIOS},
        "performance": performance_txt,
        "convergencia": convergencia_txt,
        "longitudinal": {n: _streak_txt(dominios[n]["streak"]) for n in _DOMINIOS},
        "readiness_txt": _readiness_txt(readiness),
        "concordancia_garmin": concordancia_garmin,
    }


def render_entry(entry: dict) -> str:
    indice_txt = (f"{entry['indice']}/10 -- {entry['indice_label']}"
                  if entry["indice"] is not None else "sem dado suficiente")
    linhas = [
        _SEP, f"DATA: {entry['date']}", _SEP, "",
        "FAROL (state.gather + farol.today)",
        f"- INDICE DE RECUPERACAO: {indice_txt}",
    ]
    linhas.append("- COMPONENTES DO INDICE (0-10):")
    componentes = entry.get("componentes", {})
    for chave, nota in componentes.items():
        linhas.append(f"  - {chave}: {nota if nota is not None else 'sem dado'}")
    linhas += [f"- {n}: {entry['dominios'][n]}" for n in _DOMINIOS]
    linhas.append(f"- PERFORMANCE: {entry['performance']}")
    linhas.append(f"- CONVERGENCIA: {entry['convergencia']}")
    linhas += [
        "",
        "GARMIN (segundo juiz -- daily.training_readiness_score, algoritmo do proprio Garmin)",
        f"- READINESS SCORE: {entry['readiness_txt']}",
        f"- CONCORDANCIA COM O INDICE FAROL: {entry['concordancia_garmin']}",
    ]
    linhas.append("")
    linhas.append("LONGITUDINAL")
    linhas += [f"- {n}: {entry['longitudinal'][n]}" for n in _DOMINIOS]
    linhas += [
        "",
        "PERCEPCAO REAL (preencher manualmente)",
        "- Energia:",
        "- Recuperacao:",
        "- Nota numerica de recuperacao (0-10, opcional):",
        "- Sono:",
        "- Sensacao no corpo:",
        "- Sensacao no treino:",
        "- Humor/motivacao:",
        "",
        "BATEU COM A REALIDADE? (preencher manualmente: sim / nao / parcial)",
        "-",
        "",
        "OBSERVACOES:",
        "-",
        "",
    ]
    return "\n".join(linhas) + "\n"


def append_entry(path: Path, entry: dict) -> bool:
    """Acrescenta a entrada ao final do arquivo (criando-o com o cabecalho se
    ainda nao existir). Nunca sobrescreve: se ja existe uma entrada para essa
    data, nao duplica -- devolve False sem tocar no arquivo."""
    marcador = f"DATA: {entry['date']}"
    if path.exists() and marcador in path.read_text(encoding="utf-8"):
        return False
    if not path.exists():
        path.write_text(_CABECALHO, encoding="utf-8")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + render_entry(entry))
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera e acrescenta a entrada do dia em CALIBRACAO_LOG.txt.")
    parser.add_argument("--date", default=None, help="Data YYYY-MM-DD (padrao: hoje)")
    parser.add_argument("--out", default=str(_LOG_PATH), help="Caminho do log")
    args = parser.parse_args()

    on_date = args.date or _date.today().isoformat()
    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    conn = db.connect_readonly()
    try:
        entry = build_entry(conn, on_date, profile)
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    finally:
        conn.close()

    caminho = Path(args.out)
    if append_entry(caminho, entry):
        print(f"Entrada de {on_date} acrescentada a {caminho}.\n"
              "Preencha PERCEPCAO REAL e BATEU COM A REALIDADE? a mao.")
    else:
        print(f"Ja existe uma entrada para {on_date} em {caminho} -- nada foi alterado.")


if __name__ == "__main__":
    main()
