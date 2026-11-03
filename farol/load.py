"""CLI: carrega TODOS os dados da planilha pro banco (sem sincronizar Garmin).
Uso: python -m farol.load
"""
import sys

from . import db, sheets
from .config import SHEET_PATH, load_profile
from .daily_state_builder import build_daily_state
from .training_load import recompute_training_load

import openpyxl


def main() -> None:
    try:
        profile = load_profile()
    except FileNotFoundError as exc:
        print(exc)
        sys.exit(1)

    print("Carregando TODOS os dados da planilha para o banco...")
    conn = db.connect_write()
    wb = openpyxl.load_workbook(SHEET_PATH)

    n_daily, n_act = sheets.load_into_db(conn, wb, profile["hr_max"])
    print(f"  {n_daily} dia(s), {n_act} atividade(s) carregados.")

    # Sem isto a tabela training_load fica vazia e o relatório perde CTL/ATL/TSB.
    # Recalcula desde o primeiro dia: CTL usa média móvel de 42 dias, então um
    # recálculo parcial dá TSB menos preciso que um completo (mesma razão que
    # levou ingest.py a sempre partir da data mais antiga).
    earliest = conn.execute("SELECT MIN(date) AS d FROM daily").fetchone()["d"]
    if earliest:
        rows = recompute_training_load(conn, earliest)
        print(f"  Carga de treino (CTL/ATL/TSB) recalculada para {len(rows)} dia(s).")

        print("  Preenchendo farol_daily_state...")
        for row in rows:
            build_daily_state(conn, row['date'])
        print(f"  farol_daily_state preenchido para {len(rows)} dia(s).")

    conn.close()


if __name__ == "__main__":
    main()
