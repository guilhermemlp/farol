"""Guarda de regressao contra vazamento temporal (Frente 1 -- integridade
temporal, achados de 2026-09-08).

Nao retesta a matematica de cada funcao (isso ja esta em test_correlations.py,
test_changepoint.py e test_dashboard.py, um por um, cada um confirmado como
regressao real ao ser rodado contra o codigo pre-fix). Este arquivo e' a
propriedade geral, num unico lugar, que qualquer leitura "as-of" tem que
respeitar -- receita fixa, sem framework novo:

    1. construir dados ate uma data D;
    2. calcular a analise com as_of/ate=D;
    3. inserir dados POSTERIORES a D;
    4. recalcular a mesma analise com as_of/ate=D;
    5. os resultados relevantes tem que permanecer iguais.

Cobre, no mesmo dataset, as tres funcoes que ganharam corte temporal nesta
frente: lagged_correlation (T1.2), changepoint.scan (T1.3) e
dashboard.build_context (T1.4 -- correlations_md/changepoint_md). Qualquer
funcao nova que ganhe um `ate`/`as_of` no futuro deveria ganhar o mesmo
teste aqui, seguindo a mesma receita.
"""
import math
import sqlite3
import unittest
from datetime import date, timedelta

from farol import dashboard, db
from farol.changepoint import scan as changepoint_scan
from farol.correlations import lagged_correlation


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _perfil() -> dict:
    return {
        "athlete": {"name": "Teste da Silva", "birth_date": "1991-08-19", "height_cm": 173},
        "hr_max": 192, "hr_rest": 76, "lthr": 182,
        "hr_zone_floors": {"z1": 118, "z2": 155, "z3": 164, "z4": 173},
        "race": {"date": "2026-09-19", "distance_km": 21.1, "name": "Meia-maratona"},
        "recent_races": [],
    }


class TestNaoVazamentoTemporal(unittest.TestCase):
    """Uma unica base de dados ate D, usada pelas tres funcoes -- os passos
    3-5 da receita rodam uma vez para cada uma."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 1, 1)
        self.d_offset = 39  # D = dia 39
        self.d = (self.base_date + timedelta(days=self.d_offset)).isoformat()

    def tearDown(self):
        self.conn.close()

    def _constroi_dados_ate_d(self, conn) -> dict:
        """Passo 1: stress/sleep correlacionados (para lagged_correlation) e
        rhr estavel (para changepoint) ate D, inclusive. Devolve o dict
        stress_por_dia para reuso na extensao 'futura'."""
        stress_por_dia = {
            i: 50 + 20 * math.sin(2 * math.pi * i / 14) for i in range(self.d_offset + 1)
        }
        for i in range(self.d_offset + 1):
            d = (self.base_date + timedelta(days=i)).isoformat()
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            conn.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score, rhr) VALUES (?, ?, ?, ?)",
                (d, stress_por_dia[i], sleep_score, 70.0),
            )
        conn.commit()
        return stress_por_dia

    def _insere_dados_posteriores_a_d(self, conn, stress_por_dia: dict):
        """Passo 3: 60 dias depois de D com tres contaminacoes plantadas de
        proposito -- se qualquer corte vazar, pelo menos uma das tres
        analises muda:
          - dia D+1: sleep_score absurdo (contaminaria lagged_correlation);
          - dias D+1..D+60: degrau real de rhr, 70 -> 78 (contaminaria
            changepoint.scan e o changepoint_md do dashboard);
          - stress_avg constante em 50 no futuro (neutro para a correlacao
            em si, so preenche a serie)."""
        for offset in range(1, 61):
            i = self.d_offset + offset
            d = (self.base_date + timedelta(days=i)).isoformat()
            sleep_score = -9999 if offset == 1 else 50
            conn.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score, rhr) VALUES (?, ?, ?, ?)",
                (d, 50, sleep_score, 78.0),
            )
        conn.commit()

    def test_lagged_correlation_changepoint_e_dashboard_ignoram_dado_futuro(self):
        stress_por_dia = self._constroi_dados_ate_d(self.conn)

        # Passo 2: calcula as tres analises com ate=D, ANTES de qualquer
        # dado futuro existir no banco.
        corr_antes = lagged_correlation(
            self.conn, "stress_avg", "sleep_score", lag_dias=1, ate=self.d
        )
        changepoint_antes = changepoint_scan(self.conn, "rhr", ate=self.d)
        ctx_antes = dashboard.build_context(self.conn, _perfil(), self.d)

        # Passo 3: insere dado posterior a D (degrau real de rhr + valor
        # absurdo de sleep_score em D+1).
        self._insere_dados_posteriores_a_d(self.conn, stress_por_dia)

        # Passo 4: recalcula as MESMAS tres analises, mesmo `ate=D`.
        corr_depois = lagged_correlation(
            self.conn, "stress_avg", "sleep_score", lag_dias=1, ate=self.d
        )
        changepoint_depois = changepoint_scan(self.conn, "rhr", ate=self.d)
        ctx_depois = dashboard.build_context(self.conn, _perfil(), self.d)

        # Passo 5: nada relevante pode ter mudado.
        self.assertIsNotNone(corr_antes)
        self.assertEqual(corr_antes, corr_depois)

        self.assertIsNone(changepoint_antes)  # sem quebra visivel ate D
        self.assertEqual(changepoint_antes, changepoint_depois)

        self.assertEqual(ctx_antes["correlations_md"], ctx_depois["correlations_md"])
        self.assertEqual(ctx_antes["changepoint_md"], ctx_depois["changepoint_md"])


if __name__ == "__main__":
    unittest.main()
