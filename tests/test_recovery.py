"""Testes de farol.recovery: score_from/recovery_index/recovery_label --
o calculo mais visivel do relatorio diario (o indice de recuperacao 0-10),
sem teste direto antes desta auditoria (2026-09-04). recovery_status ja
tinha cobertura em test_core.py."""
import unittest

from farol.metrics import Metric
from farol.recovery import PESOS_PADRAO, recovery_index, recovery_label, score_from


def _metric(z, higher_is_better=True, has_value=True):
    return Metric(
        name="x", value=(1.0 if has_value else None), mean_7d=None, mean_30d=None,
        mean_90d=None, sd_90d=None, z=z, trend="indefinido", status="ok",
        confidence="alta", n_7d=0, n_30d=0, n_90d=20, higher_is_better=higher_is_better,
    )


class TestScoreFrom(unittest.TestCase):
    def test_z_none_devolve_none(self):
        self.assertIsNone(score_from(_metric(z=None)))

    def test_z_zero_e_a_ancora_6_5(self):
        self.assertEqual(score_from(_metric(z=0.0)), 6.5)

    def test_z_menos_0_5_e_a_fronteira_6_0(self):
        self.assertEqual(score_from(_metric(z=-0.5)), 6.0)

    def test_z_menos_1_5_e_a_fronteira_3_5(self):
        self.assertEqual(score_from(_metric(z=-1.5)), 3.5)

    def test_nota_nunca_passa_de_10(self):
        self.assertLessEqual(score_from(_metric(z=5.0)), 10.0)

    def test_nota_nunca_fica_abaixo_de_0(self):
        self.assertGreaterEqual(score_from(_metric(z=-6.0)), 0.0)

    def test_higher_is_better_false_inverte_o_sinal(self):
        # RHR (higher_is_better=False): z=+1.0 (RHR ACIMA da media, ruim)
        # deve pontuar igual a z=-1.0 de uma metrica higher_is_better=True.
        nota_rhr_ruim = score_from(_metric(z=1.0, higher_is_better=False))
        nota_equivalente = score_from(_metric(z=-1.0, higher_is_better=True))
        self.assertEqual(nota_rhr_ruim, nota_equivalente)


class TestRecoveryIndex(unittest.TestCase):
    def test_media_ponderada_com_todas_as_metricas(self):
        metricas = {
            "sleep_score": _metric(z=0.0),   # nota 6.5
            "hrv_avg": _metric(z=0.0),       # nota 6.5
            "rhr": _metric(z=0.0, higher_is_better=False),  # nota 6.5
            "tsb": _metric(z=0.0),           # nota 6.5
        }
        indice, notas = recovery_index(metricas, PESOS_PADRAO)
        self.assertEqual(indice, 6.5)
        self.assertEqual(set(notas), set(PESOS_PADRAO))

    def test_metrica_ausente_sai_da_conta_em_vez_de_virar_mediana(self):
        # Sem HRV: o peso dela (30%) nao pode virar nota media escondida --
        # a media deve renormalizar sobre os pesos restantes (70%).
        metricas = {
            "sleep_score": _metric(z=0.0),                    # 6.5
            "rhr": _metric(z=0.0, higher_is_better=False),    # 6.5
            "tsb": _metric(z=-1.5),                            # 3.5
        }
        indice, notas = recovery_index(metricas, PESOS_PADRAO)
        self.assertIsNone(notas["hrv_avg"])
        peso_util = PESOS_PADRAO["sleep_score"] + PESOS_PADRAO["rhr"] + PESOS_PADRAO["tsb"]
        esperado = round((6.5 * 0.30 + 6.5 * 0.20 + 3.5 * 0.20) / peso_util, 1)
        self.assertEqual(indice, esperado)

    def test_nenhuma_metrica_com_dado_devolve_none(self):
        metricas = {k: _metric(z=None) for k in PESOS_PADRAO}
        indice, notas = recovery_index(metricas, PESOS_PADRAO)
        self.assertIsNone(indice)
        self.assertTrue(all(v is None for v in notas.values()))


class TestRecoveryLabel(unittest.TestCase):
    def test_none_e_sem_dado(self):
        self.assertEqual(recovery_label(None), "sem dado suficiente")

    def test_fronteiras_casam_com_score_from(self):
        # Mesmas ancoras de score_from: 8.0, 6.0, 3.5.
        self.assertEqual(recovery_label(8.0), "acima do seu normal")
        self.assertEqual(recovery_label(7.9), "dentro do seu normal")
        self.assertEqual(recovery_label(6.0), "dentro do seu normal")
        self.assertEqual(recovery_label(5.9), "abaixo do seu normal")
        self.assertEqual(recovery_label(3.5), "abaixo do seu normal")
        self.assertEqual(recovery_label(3.4), "bem abaixo do seu normal")


if __name__ == "__main__":
    unittest.main()
