"""Testes de farol.domain.snapshot: retrato diario compartilhado por today/dashboard.

Nao retesta a matematica de farol.core.metrics -- so' confirma que os dois
relatorios consomem o MESMO conjunto ordenado de Metric, com a mesma escolha
de tabela (TSB em training_load) e o mesmo respeito a procedencia de RHR.
"""
import sqlite3
import unittest
from datetime import date, timedelta

from farol.core import db
from farol.domain import snapshot


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestDailyMetrics(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.on_date = "2026-09-04"

    def tearDown(self):
        self.conn.close()

    def test_devolve_exatamente_os_campos_na_ordem_declarada(self):
        metricas = snapshot.daily_metrics(self.conn, self.on_date)
        self.assertEqual(list(metricas), list(snapshot.CAMPOS_DIARIOS))
        # Sem nenhuma linha, todo campo existe e e' "sem dado".
        self.assertTrue(all(not m.has_data for m in metricas.values()))

    def test_tsb_vem_de_training_load_nao_de_daily(self):
        self.conn.execute(
            "INSERT INTO training_load (date, ctl, atl, tsb) VALUES (?, 60, 70, 11.5)",
            (self.on_date,),
        )
        self.conn.commit()
        metricas = snapshot.daily_metrics(self.conn, self.on_date)
        self.assertEqual(metricas["tsb"].value, 11.5)

    def test_rhr_respeita_a_procedencia_da_baseline(self):
        # 30 dias Garmin (~70 bpm) + 60 dias legado (~100 bpm) dentro da mesma
        # janela nominal de 90 dias. A baseline de RHR do dia-alvo (Garmin)
        # nao pode cruzar a fonte: mean_90d tem que refletir so' os 70 bpm.
        base = date.fromisoformat(self.on_date)
        for i in range(1, 31):
            d = (base - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO daily (date, rhr, source) VALUES (?, 70, 'garmin')", (d,))
        for i in range(31, 91):
            d = (base - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO daily (date, rhr, source) VALUES (?, 100, 'legado')", (d,))
        self.conn.execute(
            "INSERT INTO daily (date, rhr, source) VALUES (?, 70, 'garmin')",
            (self.on_date,),
        )
        self.conn.commit()

        rhr = snapshot.daily_metrics(self.conn, self.on_date)["rhr"]
        self.assertEqual(rhr.value, 70.0)
        self.assertEqual(rhr.mean_90d, 70.0)
        self.assertEqual(rhr.n_90d, 30)


class TestVo2max(unittest.TestCase):
    def test_le_o_campo_do_garmin(self):
        conn = _memory_db()
        try:
            conn.execute(
                "INSERT INTO daily (date, vo2max) VALUES ('2026-09-04', 39.4)")
            conn.commit()
            m = snapshot.vo2max_metric(conn, "2026-09-04")
            self.assertEqual(m.value, 39.4)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
