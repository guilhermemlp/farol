"""CLI: sincroniza os últimos N dias do Garmin.

Fluxo: Garmin -> extração -> ingestão na planilha -> banco de dados -> carga de treino.
Uso: python -m farol.pipeline.ingest --days 7
"""
import argparse
import json
import sys
from datetime import date, timedelta

from . import sheets
from ..core import db
from ..insights import calibration_auto
from ..core.config import SHEET_PATH, load_profile
from ..domain.daily_state_builder import build_daily_state
from .garmin_client import fetch_activities, fetch_day, login
from .splits import fetch_typed_splits, split_retry_candidates, summarize_work_segments
from ..domain.training_load import recompute_training_load


def sync_activity_splits(conn, g) -> int:
    """Guarda o resumo dos tiros e o estado explícito de sua coleta.

    Antes disso, `farol.reports.today` fazia login no Garmin toda vez que rodava só para
    montar essa parte do relatório. Buscar uma vez por atividade aqui é menos
    rede, não mais — e deixa o relatório funcionar offline.

    Falha de transporte vira ``error`` e continua retryable. Resposta válida
    sem trabalho intervalado vira ``empty``. O JSON ``\"null\"`` legado é
    ``unknown`` sem backfill e nunca é tratado como prova de erro."""
    faltando = split_retry_candidates(conn)

    preenchidas = 0
    for activity_id in faltando:
        try:
            resumo = summarize_work_segments(fetch_typed_splits(g, activity_id))
        except Exception:
            conn.execute("UPDATE activities SET splits_status = 'error' WHERE activity_id = ?", (activity_id,))
            continue
        if resumo is None:
            conn.execute(
                "UPDATE activities SET splits = NULL, splits_status = 'empty' WHERE activity_id = ?",
                (activity_id,),
            )
        else:
            conn.execute(
                "UPDATE activities SET splits = ?, splits_status = 'success' WHERE activity_id = ?",
                (json.dumps(resumo), activity_id),
            )
            preenchidas += 1
    conn.commit()
    return preenchidas


def run(days: int) -> None:
    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)
    hr_max = profile["hr_max"]

    conn = db.connect_write()
    g = login()
    wb = sheets.open_or_create(SHEET_PATH)

    end = date.today()
    start = end - timedelta(days=days)

    print(f"1) Extração do Garmin: {start} a {end}...")
    for i in range((end - start).days + 1):
        d = (start + timedelta(days=i)).isoformat()
        day = fetch_day(g, d)
        # F9: o erro de endpoint que ja foi para daily.errors tambem aparece
        # aqui na sincronizacao, para a falha nao passar despercebida no dia
        # em que acontece (nada pior que um dia sem SpO2 descoberto dias
        # depois no relatorio).
        erros = day["daily"].get("errors")
        aviso = f"  AVISO: sync incompleta ({erros})" if erros else ""
        sheets.write_day(wb, day["daily"], day["sleep"], day["hrv"])
        print(
            f"   {d}: HRV={day['daily']['hrv_avg']} RHR={day['daily']['resting_hr']} "
            f"sono={day['sleep']['sleep_score']} passos={day['daily']['steps']}{aviso}"
        )

    activities = fetch_activities(g, start.isoformat(), end.isoformat())
    for act in activities:
        sheets.write_activity(wb, act)
    print(f"   {len(activities)} atividade(s) extraída(s).")

    print("2) Ingestão na planilha...")
    # A planilha e' a fonte de verdade: guarda o estado anterior a esta
    # gravacao. Uma copia por dia; a primeira do dia nunca e' sobrescrita.
    backup = sheets.backup_sheet(SHEET_PATH, end)
    if backup:
        print(f"   Backup da planilha: {backup}")
    wb.save(SHEET_PATH)
    print(f"   Planilha atualizada: {SHEET_PATH}")

    print("3) Banco de dados (recarregado a partir da planilha)...")
    n_daily, n_act = sheets.load_into_db(conn, wb, hr_max)
    print(f"   {n_daily} dia(s), {n_act} atividade(s) no banco.")

    n_splits = sync_activity_splits(conn, g)
    if n_splits:
        print(f"   Tiros analisados em {n_splits} corrida(s) intervalada(s).")

    # Sempre recalcula a partir do primeiro dia disponível no banco, não só
    # da janela recém-sincronizada: CTL usa uma média móvel de 42 dias, então
    # um recálculo parcial dá TSB menos preciso que um recálculo completo
    # (achado da calibração contra o histórico real, 2026-09-02).
    earliest = conn.execute("SELECT MIN(date) AS d FROM daily").fetchone()["d"]
    tl_rows = recompute_training_load(conn, earliest or start.isoformat())
    print(f"   Carga de treino (CTL/ATL/TSB) recalculada para {len(tl_rows)} dia(s).")

    print("4) Preenchendo farol_daily_state...")
    for row in tl_rows:
        build_daily_state(conn, row['date'])
    print(f"   farol_daily_state preenchido para {len(tl_rows)} dia(s).")

    sheets.write_training_load(wb, tl_rows)
    wb.save(SHEET_PATH)
    print(f"   Planilha final salva em {SHEET_PATH}.")

    # Saida derivada: a planilha-fonte ja esta salva antes de tentar gerar o
    # relatorio, para que uma falha de visualizacao nunca interrompa o ingest.
    calibration_auto.write_report(conn, profile, end.isoformat())
    print("   Calibracao automatica Farol x Garmin atualizada.")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza dados do Garmin para o Farol.")
    parser.add_argument("--days", type=int, default=7, help="Quantos dias para trás sincronizar (padrão: 7)")
    args = parser.parse_args()
    run(args.days)


if __name__ == "__main__":
    main()
