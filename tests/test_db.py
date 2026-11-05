"""Testes de farol.core.db: ensure_schema() e a migracao de farol_daily_state.

Sem teste dedicado antes desta auditoria (2026-09-04) -- a migracao so era
exercitada implicitamente pelo banco real (data/farol.db), que ja tinha o
schema antigo quando as colunas novas foram adicionadas em 2026-09. Todo
banco criado do zero nos testes ja nasce com o schema atual (CREATE TABLE
IF NOT EXISTS cria tudo de uma vez), entao o caminho ALTER TABLE nunca
era testado de verdade -- so validado manualmente, uma vez, na auditoria.
"""
import sqlite3
import unittest

from farol.core.db import _NOVAS_COLUNAS_ACTIVITIES, _NOVAS_COLUNAS_FAROL_DAILY_STATE, ensure_schema


def _schema_pre_2026_09() -> str:
    """farol_daily_state exatamente como era antes das 5 colunas
    acrescentadas em 2026-09 (respiration_avg, spo2_avg, spo2_min,
    body_battery_min, sleep_regularity_min) -- ver docs/MODEL_V1.0.md."""
    return """
    CREATE TABLE farol_daily_state (
        date TEXT PRIMARY KEY,
        trimp_7d REAL, trimp_28d REAL, ctl REAL, atl REAL, tsb REAL, atl_ctl_ratio REAL,
        hrv_avg REAL, rhr INTEGER, night_hr_avg INTEGER, stress_avg INTEGER,
        body_battery_max INTEGER,
        sleep_total_min REAL, awake_minutes REAL, deep_sleep_min REAL, rem_sleep_min REAL,
        sleep_score INTEGER,
        output_preserved TEXT, deterioration_detected TEXT, measurement_breakpoints TEXT
    );
    """


class TestMigracaoFarolDailyState(unittest.TestCase):
    def test_adiciona_colunas_a_banco_com_schema_antigo(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_schema_pre_2026_09())
            colunas_antes = {r["name"] for r in conn.execute("PRAGMA table_info(farol_daily_state)")}
            self.assertNotIn("respiration_avg", colunas_antes)  # confirma o cenario "banco antigo"

            ensure_schema(conn)

            colunas_depois = {r["name"] for r in conn.execute("PRAGMA table_info(farol_daily_state)")}
            for coluna in _NOVAS_COLUNAS_FAROL_DAILY_STATE:
                self.assertIn(coluna, colunas_depois)
        finally:
            conn.close()

    def test_preserva_dado_existente_ao_migrar(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_schema_pre_2026_09())
            conn.execute(
                "INSERT INTO farol_daily_state (date, hrv_avg, rhr) VALUES (?, ?, ?)",
                ("2026-06-15", 18.5, 65),
            )
            conn.commit()

            ensure_schema(conn)

            row = dict(conn.execute(
                "SELECT * FROM farol_daily_state WHERE date = '2026-06-15'"
            ).fetchone())
            self.assertEqual(row["hrv_avg"], 18.5)
            self.assertEqual(row["rhr"], 65)
            self.assertIsNone(row["respiration_avg"])  # coluna nova, sem valor ainda
        finally:
            conn.close()

    def test_e_idempotente_em_banco_ja_migrado(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_schema_pre_2026_09())
            ensure_schema(conn)
            ensure_schema(conn)  # roda de novo -- nao pode dar "duplicate column"
            colunas = {r["name"] for r in conn.execute("PRAGMA table_info(farol_daily_state)")}
            for coluna in _NOVAS_COLUNAS_FAROL_DAILY_STATE:
                self.assertIn(coluna, colunas)
        finally:
            conn.close()

    def test_banco_novo_ja_nasce_com_as_colunas_sem_precisar_de_alter(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            ensure_schema(conn)  # so o schema atual, sem tabela antiga primeiro
            colunas = {r["name"] for r in conn.execute("PRAGMA table_info(farol_daily_state)")}
            for coluna in _NOVAS_COLUNAS_FAROL_DAILY_STATE:
                self.assertIn(coluna, colunas)
        finally:
            conn.close()


class TestMigracaoActivities(unittest.TestCase):
    def test_adiciona_status_sem_reclassificar_payload_historico(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "CREATE TABLE activities (activity_id INTEGER PRIMARY KEY, date TEXT, type TEXT, source TEXT, splits TEXT)"
            )
            conn.execute("INSERT INTO activities (activity_id, splits) VALUES (1, 'null')")
            conn.commit()

            ensure_schema(conn)

            row = conn.execute("SELECT splits, splits_status FROM activities WHERE activity_id = 1").fetchone()
            self.assertEqual(row["splits"], "null")
            self.assertIsNone(row["splits_status"])
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(activities)")}
            self.assertTrue(set(_NOVAS_COLUNAS_ACTIVITIES) <= columns)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
