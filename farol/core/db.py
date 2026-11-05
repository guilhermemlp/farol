import os
import sqlite3
import sys
from pathlib import Path

from .config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _is_personal_database(db_path: str | Path) -> bool:
    if str(db_path) == ":memory:":
        return False
    return Path(db_path).resolve() == Path(DB_PATH).resolve()


def _is_test_process() -> bool:
    return os.environ.get("FAROL_TESTING") == "1" or "unittest" in sys.modules


def connect_write(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Abre banco mutável e aplica schema/migrations deliberadamente."""
    if _is_personal_database(db_path) and _is_test_process():
        raise RuntimeError(
            "Testes não podem abrir data/farol.db de forma mutável; "
            "use :memory:, tempfile ou uma conexão read-only."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def connect_readonly(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Abre um SQLite existente sem qualquer caminho de mutação.

    Diferentemente de :func:`connect`, esta função não inicializa schema,
    não migra e não ajusta PRAGMAs. ``mode=ro`` é imposto pelo próprio SQLite,
    portanto INSERT/UPDATE/DDL falham mesmo se um chamador tentar executá-los.
    """
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Compatibilidade segura para consumidores antigos.

    O banco pessoal passa a abrir exclusivamente em modo leitura. Caminhos
    explícitos de teste continuam mutáveis para preservar ``:memory:`` e
    tempfiles; produção que escreve deve usar ``connect_write``.
    """
    if _is_personal_database(db_path):
        return connect_readonly(db_path)
    return connect_write(db_path)


# Colunas acrescentadas a farol_daily_state depois da criação original da
# tabela (parecer técnico de 2026-09-04: campos já coletados pelo Garmin,
# nunca antes expostos). CREATE TABLE IF NOT EXISTS, em schema.sql, não
# adiciona coluna a uma tabela que já existe -- por isso a migração roda
# aqui, via PRAGMA table_info, em vez de só no script SQL.
_NOVAS_COLUNAS_FAROL_DAILY_STATE = {
    "body_battery_min": "INTEGER",
    "respiration_avg": "REAL",
    "spo2_avg": "REAL",
    "spo2_min": "REAL",
    "sleep_regularity_min": "REAL",
}


# Não há backfill automático: o payload histórico "null" não permite
# concluir se a última tentativa falhou ou se a resposta válida estava vazia.
_NOVAS_COLUNAS_ACTIVITIES = {
    "splits_status": "TEXT",
    "moving_minutes": "REAL",
    "vertical_ratio": "REAL",
    "strava_activity_id": "TEXT",
    "historical_source": "TEXT",
    "running_dynamics_source": "TEXT",
    "rpe": "REAL",
    "shoe": "TEXT",
    "air_temperature_c": "REAL",
    "relative_humidity_pct": "REAL",
    "gap_pace_min_km": "REAL",
    "historical_only": "INTEGER",
}


def _migrate_farol_daily_state(conn: sqlite3.Connection) -> None:
    existentes = {row["name"] for row in conn.execute("PRAGMA table_info(farol_daily_state)")}
    for coluna, tipo in _NOVAS_COLUNAS_FAROL_DAILY_STATE.items():
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE farol_daily_state ADD COLUMN {coluna} {tipo}")


def _migrate_activities(conn: sqlite3.Connection) -> None:
    existentes = {row["name"] for row in conn.execute("PRAGMA table_info(activities)")}
    for coluna, tipo in _NOVAS_COLUNAS_ACTIVITIES.items():
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {coluna} {tipo}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate_farol_daily_state(conn)
    _migrate_activities(conn)
    conn.commit()
