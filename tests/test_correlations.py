"""Testes de farol.correlations: correlacao defasada entre deltas contra a
propria baseline de 90 dias."""
import math
import sqlite3
import unittest
from datetime import date, timedelta

from farol import db
from farol.correlations import Correlacao, lagged_correlation, render_report


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestLaggedCorrelation(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 1, 1)

    def tearDown(self):
        self.conn.close()

    def _insere(self, i: int, stress: float, sleep_score: float):
        d = (self.base_date + timedelta(days=i)).isoformat()
        self.conn.execute(
            "INSERT INTO daily (date, stress_avg, sleep_score) VALUES (?, ?, ?)",
            (d, stress, sleep_score),
        )

    def test_poucos_dias_devolve_none(self):
        for i in range(10):
            self._insere(i, 50, 60)
        self.conn.commit()
        self.assertIsNone(
            lagged_correlation(self.conn, "stress_avg", "sleep_score", lag_dias=1)
        )

    def test_relacao_negativa_forte_e_detectada_com_lag(self):
        # sleep_score[dia i] = 60 - (stress_avg[dia i-1] - 50): sono da noite
        # de amanha responde (inversamente) ao stress de hoje, defasado 1 dia.
        n_dias = 150
        stress_por_dia = {}
        for i in range(n_dias):
            stress = 50 + 20 * math.sin(2 * math.pi * i / 14)
            stress_por_dia[i] = stress
        for i in range(n_dias):
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            self._insere(i, stress_por_dia[i], sleep_score)
        self.conn.commit()

        resultado = lagged_correlation(self.conn, "stress_avg", "sleep_score", lag_dias=1)
        self.assertIsInstance(resultado, Correlacao)
        self.assertLess(resultado.r, -0.7)
        self.assertGreaterEqual(resultado.n, 30)

    def test_lag_errado_nao_encontra_a_relacao(self):
        # Mesma fixture do teste acima, mas comparando o mesmo dia (lag=0) em
        # vez do dia seguinte -- a relacao construida so existe com lag=1,
        # entao lag=0 deve dar uma correlacao bem mais fraca.
        n_dias = 150
        stress_por_dia = {}
        for i in range(n_dias):
            stress_por_dia[i] = 50 + 20 * math.sin(2 * math.pi * i / 14)
        for i in range(n_dias):
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            self._insere(i, stress_por_dia[i], sleep_score)
        self.conn.commit()

        com_lag = lagged_correlation(self.conn, "stress_avg", "sleep_score", lag_dias=1)
        sem_lag = lagged_correlation(self.conn, "stress_avg", "sleep_score", lag_dias=0)
        self.assertIsNotNone(com_lag)
        self.assertIsNotNone(sem_lag)
        self.assertLess(abs(sem_lag.r), abs(com_lag.r))

    def test_ate_com_lag_1_nao_acessa_data_posterior_ao_corte(self):
        # T1.2 (auditoria de 2026-09-08): com lag_dias=1, o Y de `ate` e'
        # `ate+1` -- data POSTERIOR ao corte. A versao anterior buscava e
        # usava esse dia sem checar `ate`, entao um valor plantado
        # exatamente em ate+1 mudava o resultado de uma leitura "as-of".
        #
        # Prova por comparacao: o mesmo dado ate `ate`, calculado (a) num
        # banco que nao tem NENHUMA linha depois de `ate` e (b) num banco
        # que tem linhas depois de `ate` incluindo um valor absurdo
        # plantado exatamente em ate+1, tem que dar o MESMO resultado --
        # sem isso, (b) vazaria o valor absurdo para dentro do par de
        # `ate`.
        n_dias_com_futuro = 60
        stress_por_dia = {
            i: 50 + 20 * math.sin(2 * math.pi * i / 14) for i in range(n_dias_com_futuro)
        }

        def sleep_score_de(i):
            return 60 - (stress_por_dia.get(i - 1, 50) - 50)

        for i in range(n_dias_com_futuro):
            self._insere(i, stress_por_dia[i], sleep_score_de(i))
        self.conn.commit()

        ate = (self.base_date + timedelta(days=39)).isoformat()  # corte = dia 39
        dia_alem_do_corte = (self.base_date + timedelta(days=40)).isoformat()  # ate + 1

        # Banco sem futuro: so existem dias 0..39, o mesmo dado ate o corte.
        conn_sem_futuro = _memory_db()
        for i in range(40):
            d = (self.base_date + timedelta(days=i)).isoformat()
            conn_sem_futuro.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score) VALUES (?, ?, ?)",
                (d, stress_por_dia[i], sleep_score_de(i)),
            )
        conn_sem_futuro.commit()

        baseline = lagged_correlation(
            conn_sem_futuro, "stress_avg", "sleep_score", lag_dias=1, ate=ate
        )
        conn_sem_futuro.close()

        # Planta um valor absurdo exatamente em ate+1, no banco COM futuro.
        # Se o corte vazar, este e' o valor que vazaria para o par de `ate`.
        self.conn.execute(
            "UPDATE daily SET sleep_score = -9999 WHERE date = ?", (dia_alem_do_corte,)
        )
        self.conn.commit()

        com_corte = lagged_correlation(
            self.conn, "stress_avg", "sleep_score", lag_dias=1, ate=ate
        )

        self.assertIsNotNone(baseline)
        self.assertIsNotNone(com_corte)
        self.assertEqual(com_corte.n, baseline.n)
        self.assertAlmostEqual(com_corte.r, baseline.r, places=6)

    def test_ate_none_preserva_comportamento_atual(self):
        # ate=None resolve para MAX(date) real -- nao existe linha alem
        # dele na tabela, entao o filtro novo do lado Y e' um no-op: o
        # resultado tem que ser identico ao mesmo calculo passando
        # ate=MAX(date) explicitamente.
        n_dias = 150
        stress_por_dia = {}
        for i in range(n_dias):
            stress_por_dia[i] = 50 + 20 * math.sin(2 * math.pi * i / 14)
        for i in range(n_dias):
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            self._insere(i, stress_por_dia[i], sleep_score)
        self.conn.commit()

        max_date = (self.base_date + timedelta(days=n_dias - 1)).isoformat()

        sem_ate = lagged_correlation(self.conn, "stress_avg", "sleep_score", lag_dias=1)
        com_ate_explicito = lagged_correlation(
            self.conn, "stress_avg", "sleep_score", lag_dias=1, ate=max_date
        )

        self.assertIsNotNone(sem_ate)
        self.assertEqual(sem_ate, com_ate_explicito)

    def test_tabela_diferente_para_x_e_y(self):
        # tsb mora em training_load, nao em daily -- confirma que o cross-table
        # funciona (mesmo padrao que state.py ja usa para TSB).
        n_dias = 100
        for i in range(n_dias):
            d = (self.base_date + timedelta(days=i)).isoformat()
            tsb = 10 * math.sin(2 * math.pi * i / 14)
            self.conn.execute(
                "INSERT INTO training_load (date, tsb) VALUES (?, ?)", (d, tsb)
            )
            self.conn.execute(
                "INSERT INTO daily (date, stress_avg) VALUES (?, ?)",
                (d, 50 - tsb),
            )
        self.conn.commit()

        resultado = lagged_correlation(
            self.conn, "tsb", "stress_avg", lag_dias=0,
            tabela_x="training_load", tabela_y="daily",
        )
        self.assertIsNotNone(resultado)
        self.assertLess(resultado.r, -0.7)


class TestRenderReportAte(unittest.TestCase):
    """T1.3 (auditoria de 2026-09-08): render_report propaga `ate` para
    lagged_correlation sem mudar o comportamento default."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 1, 1)

    def tearDown(self):
        self.conn.close()

    def _popula(self, conn, n_dias, stress_por_dia):
        for i in range(n_dias):
            d = (self.base_date + timedelta(days=i)).isoformat()
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            conn.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score) VALUES (?, ?, ?)",
                (d, stress_por_dia[i], sleep_score),
            )
        conn.commit()

    def test_render_report_sem_ate_preserva_comportamento(self):
        n_dias = 150
        stress_por_dia = {i: 50 + 20 * math.sin(2 * math.pi * i / 14) for i in range(n_dias)}
        self._popula(self.conn, n_dias, stress_por_dia)
        max_date = (self.base_date + timedelta(days=n_dias - 1)).isoformat()

        sem_ate = render_report(self.conn)
        com_ate_explicito = render_report(self.conn, ate=max_date)
        self.assertEqual(sem_ate, com_ate_explicito)

    def test_render_report_com_ate_nao_observa_dado_posterior(self):
        # Mesmo padrao de prova de T1.2 (comparacao com/sem futuro), agora
        # exercitando render_report() de ponta a ponta para confirmar que a
        # propagacao de `ate` ate' lagged_correlation esta ligada de verdade.
        n_dias_com_futuro = 60
        stress_por_dia = {
            i: 50 + 20 * math.sin(2 * math.pi * i / 14) for i in range(n_dias_com_futuro)
        }
        self._popula(self.conn, n_dias_com_futuro, stress_por_dia)

        ate = (self.base_date + timedelta(days=39)).isoformat()
        dia_alem_do_corte = (self.base_date + timedelta(days=40)).isoformat()

        conn_sem_futuro = _memory_db()
        self._popula(conn_sem_futuro, 40, stress_por_dia)
        baseline = render_report(conn_sem_futuro, ate=ate)
        conn_sem_futuro.close()

        self.conn.execute(
            "UPDATE daily SET sleep_score = -9999 WHERE date = ?", (dia_alem_do_corte,)
        )
        self.conn.commit()
        com_corte = render_report(self.conn, ate=ate)

        self.assertEqual(baseline, com_corte)


if __name__ == "__main__":
    unittest.main()
