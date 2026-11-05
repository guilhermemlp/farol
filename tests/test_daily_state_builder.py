"""Teste de fronteira para as janelas trimp_7d/trimp_28d de
farol.domain.daily_state_builder.build_daily_state.

Cada dia recebe um TRIMP unico e identificavel (date_index + 1) para que a
soma agregada revele exatamente quais datas entraram na janela -- em vez de
so checar "nao e' None" (o golden real, tests/test_core.py, faz isso hoje e
nao teria pego o off-by-one de 29 dias)."""
import sqlite3
import unittest
from datetime import date, timedelta

from farol.core import db
from farol.domain.daily_state_builder import build_daily_state


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestTrimpWindows(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.d = date(2026, 9, 2)  # dia D avaliado

    def tearDown(self):
        self.conn.close()

    def _insere_atividade(self, activity_id: int, on_date: date, trimp: float):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, trimp, source) "
            "VALUES (?, ?, 'running', ?, 'garmin')",
            (activity_id, on_date.isoformat(), trimp),
        )

    def test_trimp_28d_cobre_exatamente_28_datas_d27_a_d(self):
        # Um TRIMP por dia de D-30 a D (31 dias), valor = indice (0..30) do
        # dia a partir de D-30 -- assim a soma esperada de qualquer sub-janela
        # e' calculavel exatamente a partir de quais indices entraram.
        trimp_por_offset = {}  # offset (dias antes de D, 0 = D) -> trimp
        for offset in range(31):  # offsets 0 (=D) ate 30 (=D-30)
            dia = self.d - timedelta(days=offset)
            trimp = 1000 + offset  # valor unico e identificavel por offset
            trimp_por_offset[offset] = trimp
            self._insere_atividade(offset + 1, dia, trimp)
        self.conn.execute(
            "INSERT INTO daily (date, rhr) VALUES (?, 70)", (self.d.isoformat(),)
        )
        self.conn.commit()

        build_daily_state(self.conn, self.d.isoformat())
        row = self.conn.execute(
            "SELECT trimp_7d, trimp_28d FROM farol_daily_state WHERE date = ?",
            (self.d.isoformat(),),
        ).fetchone()

        # 7 dias fechados = offsets 0..6 (D-6 .. D).
        esperado_7d = sum(trimp_por_offset[o] for o in range(0, 7))
        self.assertAlmostEqual(row["trimp_7d"], esperado_7d, places=6)

        # 28 dias fechados = offsets 0..27 (D-27 .. D) -- NAO inclui offset 28
        # (D-28). Este e' o off-by-one corrigido: a versao anterior somava
        # offsets 0..28 (29 datas).
        esperado_28d = sum(trimp_por_offset[o] for o in range(0, 28))
        self.assertAlmostEqual(row["trimp_28d"], esperado_28d, places=6)

        # Prova direta de que D-28 (offset 28) ficou de fora: removendo-o da
        # soma esperada o resultado bate; incluindo-o, nao bateria.
        soma_com_d28 = esperado_28d + trimp_por_offset[28]
        self.assertNotAlmostEqual(row["trimp_28d"], soma_com_d28, places=6)

    def test_trimp_28d_exclui_atividade_exatamente_em_d28(self):
        # Caso isolado, mais legivel que o teste acima: uma unica atividade
        # de TRIMP muito alto plantada em D-28 nao pode aparecer na soma.
        self._insere_atividade(1, self.d - timedelta(days=27), trimp=50.0)
        self._insere_atividade(2, self.d - timedelta(days=28), trimp=100000.0)
        self.conn.execute(
            "INSERT INTO daily (date, rhr) VALUES (?, 70)", (self.d.isoformat(),)
        )
        self.conn.commit()

        build_daily_state(self.conn, self.d.isoformat())
        row = self.conn.execute(
            "SELECT trimp_28d FROM farol_daily_state WHERE date = ?",
            (self.d.isoformat(),),
        ).fetchone()

        self.assertAlmostEqual(row["trimp_28d"], 50.0, places=6)


if __name__ == "__main__":
    unittest.main()
