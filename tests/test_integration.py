"""Teste de integração: fluxo real de construção do farol_daily_state.

Exercita a sequência:
    daily/activities -> recompute_training_load() -> build_daily_state()

Sem mocking, sem planilha: popula o banco diretamente como sheets.load_into_db
faria, depois executa os mesmos motores que ingest.py/load.py executam.
"""
import sqlite3
import unittest
from datetime import date, timedelta

from farol import db
from farol.daily_state_builder import build_daily_state
from farol.training_load import recompute_training_load, trimp


class TestDailyStateIntegration(unittest.TestCase):
    """Fluxo completo: dados diários + atividades -> training_load -> daily_state."""

    def setUp(self):
        # Banco isolado em memória -- nunca o farol.db de producao. Usar
        # db.connect() aqui apagaria e sobrescreveria o historico real de
        # daily/training_load com esta janela sintetica de 91 dias a cada
        # execucao da suite (era a causa de farol_daily_state cair para 91
        # registros em vez de cobrir os 212 dias reais de daily).
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db.ensure_schema(self.conn)

        self.base_date = date(2026, 9, 2)

        # 90 dias de daily (baseline para CTL)
        for i in range(90, 0, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 70, 18.0, 45, 30, 82, 420, 40, 80, 60, 55),
            )

        # Dia-alvo (2026-09-02)
        d = self.base_date.isoformat()
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d, 77, 18.0, 39, 33, 83, 443, 71, 92, 69, 52),
        )
        self.conn.commit()

        # Atividades: a cada 3 dias, um treino de 60 min a 150 bpm
        for i in range(1, 31):
            act_date = (self.base_date - timedelta(days=i)).isoformat()
            if i % 3 == 0:
                t = trimp(60.0, 150.0, 70.0, 192.0)
                self.conn.execute(
                    """INSERT INTO activities
                       (activity_id, date, type, duration_min, distance_km, avg_hr, trimp)
                       VALUES (?, ?, 'running', ?, ?, ?, ?)""",
                    (1000 + i, act_date, 60.0, 10.0, 150, t),
                )

        # Atividade EXCLUSIVA em date-7: nao deve entrar no trimp_7d
        excl_date = (self.base_date - timedelta(days=7)).isoformat()
        excl_trimp = trimp(60.0, 150.0, 70.0, 192.0)
        self.conn.execute(
            """INSERT INTO activities
               (activity_id, date, type, duration_min, distance_km, avg_hr, trimp)
               VALUES (?, ?, 'running', ?, ?, ?, ?)""",
            (9999, excl_date, 60.0, 10.0, 150, excl_trimp),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _run_pipeline(self):
        """Replica a sequencia de load.py/ingest.py para esta data."""
        earliest = self.conn.execute(
            "SELECT MIN(date) AS d FROM daily"
        ).fetchone()["d"]
        tl_rows = recompute_training_load(self.conn, earliest)
        for row in tl_rows:
            build_daily_state(self.conn, row["date"])
        return tl_rows

    def test_recompute_training_load_executa(self):
        tl_rows = self._run_pipeline()
        self.assertGreater(len(tl_rows), 0)
        self.assertIsNotNone(tl_rows[0]["ctl"])
        self.assertIsNotNone(tl_rows[0]["atl"])
        self.assertIsNotNone(tl_rows[0]["tsb"])

    def test_build_daily_state_criado_apos_training_load(self):
        self._run_pipeline()
        target = self.base_date.isoformat()

        row = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?", (target,)
        ).fetchone()
        self.assertIsNotNone(row, "farol_daily_state nao foi criado")

    def test_campos_carga_preenchidos(self):
        self._run_pipeline()
        row = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),),
        ).fetchone()

        for campo in ("trimp_7d", "trimp_28d", "ctl", "atl", "tsb", "atl_ctl_ratio"):
            self.assertIsNotNone(
                row[campo], f"Campo {campo} deveria estar preenchido"
            )

    def test_valores_coerentes_com_origem(self):
        self._run_pipeline()
        row = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),),
        ).fetchone()

        # RECUPERAÇÃO e SONO refletem o daily inserido no setUp
        self.assertEqual(row["rhr"], 77)
        self.assertEqual(row["hrv_avg"], 18.0)
        self.assertEqual(row["stress_avg"], 39)
        self.assertEqual(row["body_battery_max"], 33)
        self.assertEqual(row["night_hr_avg"], 83)
        self.assertEqual(row["sleep_total_min"], 443.0)
        self.assertEqual(row["awake_minutes"], 71.0)
        self.assertEqual(row["deep_sleep_min"], 92.0)
        self.assertEqual(row["rem_sleep_min"], 69.0)
        self.assertEqual(row["sleep_score"], 52)

        # atl_ctl_ratio == atl / ctl
        expected = row["atl"] / row["ctl"] if row["ctl"] and row["ctl"] > 0 else None
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(row["atl_ctl_ratio"], expected, places=5)

    def test_trimp_7d_janela_inclusiva(self):
        self._run_pipeline()
        row = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),),
        ).fetchone()

        # Janela de 7 dias de calendario: date - 6 ate date, inclusive
        start = (self.base_date - timedelta(days=6)).isoformat()
        end = self.base_date.isoformat()
        expected_trimp = sum(
            r["trimp"] or 0.0
            for r in self.conn.execute(
                "SELECT trimp FROM activities WHERE date >= ? AND date <= ? AND trimp IS NOT NULL",
                (start, end),
            ).fetchall()
        )

        # A atividade inserida em date - 7 NAO deve entrar
        excl_date = (self.base_date - timedelta(days=7)).isoformat()
        excl = self.conn.execute(
            "SELECT trimp FROM activities WHERE date = ?", (excl_date,)
        ).fetchone()
        if excl:
            expected_trimp_excl = expected_trimp + (excl["trimp"] or 0.0)
        else:
            expected_trimp_excl = expected_trimp

        self.assertNotAlmostEqual(
            row["trimp_7d"], expected_trimp_excl, places=2,
            msg="Atividade em date-7 nao deveria entrar no trimp_7d"
        )
        self.assertAlmostEqual(row["trimp_7d"], expected_trimp, places=2)

    def test_idempotencia_fluxo_completo(self):
        # Primeira execução
        self._run_pipeline()
        row1 = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),),
        ).fetchone()

        # Segunda execução (mesmos dados)
        self._run_pipeline()
        row2 = self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),),
        ).fetchone()

        self.assertIsNotNone(row1)
        self.assertIsNotNone(row2)
        self.assertEqual(dict(row1), dict(row2))


if __name__ == "__main__":
    unittest.main()
