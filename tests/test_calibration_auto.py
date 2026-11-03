"""Testes da calibracao objetiva Farol x Training Readiness Garmin."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from farol import calibration_auto, db


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestCalibrationAuto(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.conn.execute("INSERT INTO daily (date, training_readiness_score) VALUES ('2026-09-01', 50)")
        self.conn.execute("INSERT INTO daily (date, training_readiness_score) VALUES ('2026-09-02', 51)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_respeita_data_de_corte(self):
        with patch("farol.calibration_auto.calibration._indice_do_dia", return_value=(6.5, "", {})), \
             patch("farol.calibration_auto.daily_metric", return_value=SimpleNamespace(status="ok")):
            pairs = calibration_auto.paired_days(self.conn, {}, "2026-09-01")
        self.assertEqual(pairs, [calibration_auto.Pair("2026-09-01", "ok", "ok")])

    def test_relatorio_nao_altera_banco(self):
        before = self.conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        with patch("farol.calibration_auto.calibration._indice_do_dia", return_value=(6.5, "", {})), \
             patch("farol.calibration_auto.daily_metric", return_value=SimpleNamespace(status="ok")):
            report = calibration_auto.build_report(self.conn, {}, "2026-09-02")
        after = self.conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        self.assertEqual(before, after)
        self.assertIn("Concordancia bruta: 2/2 (100%)", report)
        self.assertIn("MATRIZ DE CLASSIFICACAO", report)

    def test_kappa_sem_variacao_nao_finge_estimativa(self):
        pairs = [calibration_auto.Pair("2026-09-01", "ok", "ok"),
                 calibration_auto.Pair("2026-09-02", "ok", "ok")]
        self.assertIsNone(calibration_auto._kappa(pairs))


if __name__ == "__main__":
    unittest.main()
