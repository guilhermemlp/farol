"""Testes de farol.decoupling: decoupling aerobico classico (Pa:Hr) dentro
de uma unica corrida continua.

Recomendacao 6 do parecer tecnico de 2026-09-04, destravada apos confirmar
que get_activity_splits devolve laps reais por km para corridas continuas
(nao so treino intervalado, que e' tudo que get_activity_typed_splits
cobre). O teste mais importante aqui e' o golden sample: os 16 laps reais
da corrida "Longo 15 Km" de 2026-08-22 (activity_id 24070320249), buscados
ao vivo do Garmin uma unica vez para validar a implementacao contra dado
real -- ver farol/decoupling.py e tests/test_decoupling.py::_LAPS_REAIS.
"""
import unittest

from farol.decoupling import Decoupling, compute_decoupling

# lapDTOs reais de get_activity_splits (activity_id 24070320249,
# "Serra Talhada - 22/08 Sab - Corrida Longo 15 Km"), simplificados aos
# campos que compute_decoupling usa. Buscado ao vivo em 2026-09-04 para
# validar a implementacao -- ver docstring do modulo.
_LAPS_REAIS = [
    {"distance": 1000.0, "duration": 405.015, "averageHR": 156.0},
    {"distance": 1000.0, "duration": 389.289, "averageHR": 170.0},
    {"distance": 1000.0, "duration": 397.300, "averageHR": 166.0},
    {"distance": 1000.0, "duration": 396.699, "averageHR": 174.0},
    {"distance": 1000.0, "duration": 379.180, "averageHR": 167.0},
    {"distance": 1000.0, "duration": 399.136, "averageHR": 173.0},
    {"distance": 1000.0, "duration": 348.385, "averageHR": 174.0},
    {"distance": 1000.0, "duration": 351.282, "averageHR": 181.0},
    {"distance": 1000.0, "duration": 341.004, "averageHR": 180.0},
    {"distance": 1000.0, "duration": 384.091, "averageHR": 178.0},
    {"distance": 1000.0, "duration": 411.353, "averageHR": 168.0},
    {"distance": 1000.0, "duration": 396.305, "averageHR": 166.0},
    {"distance": 1000.0, "duration": 369.950, "averageHR": 173.0},
    {"distance": 1000.0, "duration": 380.892, "averageHR": 174.0},
    {"distance": 1000.0, "duration": 359.903, "averageHR": 174.0},
    {"distance": 6.05, "duration": 1.645, "averageHR": 180.0},  # lap final parcial
]


def _lap(distance=1000.0, duration=400.0, hr=170.0):
    return {"distance": distance, "duration": duration, "averageHR": hr}


class TestComputeDecouplingGoldenSample(unittest.TestCase):
    """Corrida real de 15km, 2026-08-22 -- ver _LAPS_REAIS."""

    def setUp(self):
        self.resultado = compute_decoupling(_LAPS_REAIS)

    def test_devolve_resultado(self):
        self.assertIsInstance(self.resultado, Decoupling)

    def test_lap_final_parcial_e_excluido(self):
        # 6.05m fica abaixo de _DISTANCIA_MIN_LAP_M (200m) -- os 15 laps de
        # 1000m dividem em 8 (primeira metade) + 7 (segunda), nao 8+8.
        self.assertEqual(self.resultado.n_laps_primeira, 8)
        self.assertEqual(self.resultado.n_laps_segunda, 7)

    def test_distancia_total_ignora_o_lap_parcial(self):
        self.assertEqual(self.resultado.dist_total_km, 15.0)

    def test_efficiency_factor_e_decoupling_batem_com_o_calculo_real(self):
        # Valores impressos por 'python -m farol.decoupling --activity-id
        # 24070320249' em 2026-09-04, contra o Garmin ao vivo.
        self.assertAlmostEqual(self.resultado.ef_primeira_metade, 0.01534, places=5)
        self.assertAlmostEqual(self.resultado.ef_segunda_metade, 0.01528, places=5)
        self.assertAlmostEqual(self.resultado.decoupling_pct, 0.4, places=1)


class TestComputeDecouplingCasosSinteticos(unittest.TestCase):
    def test_poucos_laps_devolve_none(self):
        laps = [_lap() for _ in range(3)]
        self.assertIsNone(compute_decoupling(laps))

    def test_fc_ausente_e_tratado_como_invalido(self):
        laps = [_lap(hr=None) for _ in range(6)]
        self.assertIsNone(compute_decoupling(laps))

    def test_lap_curto_demais_e_excluido(self):
        laps = [_lap() for _ in range(6)] + [_lap(distance=50.0, duration=15.0)]
        resultado = compute_decoupling(laps)
        self.assertEqual(resultado.n_laps_primeira + resultado.n_laps_segunda, 6)

    def test_fc_estavel_no_mesmo_pace_da_decoupling_proximo_de_zero(self):
        laps = [_lap(hr=160.0) for _ in range(10)]
        resultado = compute_decoupling(laps)
        self.assertAlmostEqual(resultado.decoupling_pct, 0.0, places=1)

    def test_fc_subindo_no_mesmo_pace_da_decoupling_positivo(self):
        # Mesma velocidade nas duas metades, FC mais alta na segunda ->
        # EF cai -> decoupling positivo (pior).
        primeira = [_lap(hr=160.0) for _ in range(5)]
        segunda = [_lap(hr=175.0) for _ in range(5)]
        resultado = compute_decoupling(primeira + segunda)
        self.assertGreater(resultado.decoupling_pct, 0)

    def test_fc_caindo_no_mesmo_pace_da_decoupling_negativo(self):
        primeira = [_lap(hr=175.0) for _ in range(5)]
        segunda = [_lap(hr=160.0) for _ in range(5)]
        resultado = compute_decoupling(primeira + segunda)
        self.assertLess(resultado.decoupling_pct, 0)


if __name__ == "__main__":
    unittest.main()
