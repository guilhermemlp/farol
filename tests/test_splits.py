"""Testes de farol.splits.summarize_work_segments: o parsing da resposta
bruta do Garmin (get_activity_typed_splits) que alimenta o dominio
PERFORMANCE (assess_output_preserved/assess_deterioration_detected). Nenhum
teste existia para esta funcao antes desta auditoria -- a cobertura de
PERFORMANCE em test_core.py insere JSON ja resumido direto em
activities.splits, nunca passa por aqui.
"""
import unittest

from farol.splits import WORK_TYPES, summarize_work_segments


def _segmento(tipo="INTERVAL_ACTIVE", distance=1000, duration=240, **campos):
    """Segmento no formato bruto que get_activity_typed_splits devolve
    (camelCase, como a API do Garmin usa)."""
    base = {"type": tipo, "distance": distance, "duration": duration}
    base.update(campos)
    return base


class TestSummarizeWorkSegments(unittest.TestCase):
    def test_sem_segmentos_de_trabalho_devolve_none(self):
        splits = [_segmento("WARMUP"), _segmento("RECOVERY"), _segmento("COOLDOWN")]
        self.assertIsNone(summarize_work_segments(splits))

    def test_lista_vazia_devolve_none(self):
        self.assertIsNone(summarize_work_segments([]))

    def test_ignora_aquecimento_recuperacao_e_desaceleracao(self):
        splits = [
            _segmento("WARMUP", distance=800, duration=300),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=240, averageHR=170),
            _segmento("RECOVERY", distance=200, duration=90),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=245, averageHR=172),
            _segmento("COOLDOWN", distance=600, duration=280),
        ]
        resultado = summarize_work_segments(splits)
        self.assertEqual(resultado["n_reps"], 2)  # so os 2 INTERVAL_ACTIVE

    def test_calcula_pace_e_medias_dos_tiros(self):
        splits = [
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=240,  # 4 min/km
                      averageHR=170, maxHR=178, averageRunCadence=180,
                      groundContactTime=250, verticalOscillation=8.5, averagePower=300),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=250,  # 250/60=4.1666 min/km
                      averageHR=174, maxHR=180, averageRunCadence=178,
                      groundContactTime=255, verticalOscillation=8.7, averagePower=310),
        ]
        resultado = summarize_work_segments(splits)
        self.assertEqual(resultado["n_reps"], 2)
        self.assertEqual(resultado["reps"][0]["pace_min_km"], 4.0)
        self.assertAlmostEqual(resultado["reps"][1]["pace_min_km"], 250 / 60 / 1, places=2)
        self.assertEqual(resultado["avg_hr"], 172.0)          # (170+174)/2
        self.assertEqual(resultado["max_hr"], 180)             # max dos maxHR
        self.assertAlmostEqual(resultado["avg_cadence"], 179.0, places=1)
        self.assertAlmostEqual(resultado["avg_ground_contact_time"], 252.5, places=1)

    def test_campo_totalmente_ausente_devolve_none_nao_zero(self):
        # Corrigido apos achado na auditoria de 2026-09-04 (_media_dos_presentes):
        # `sum(x for x in reps if x) / n` com todo `x` None somava 0 sobre
        # lista vazia e dividia por n, dando 0.0 -- silenciosamente diferente
        # de "sem dado". Potencia normalmente vem do acelerometro do
        # relogio e esta presente em qualquer corrida (confirmado: 102/102
        # tiros reais do banco tem avg_power) -- este teste nao afirma que
        # ausencia total e' comum, so protege o padrao generico para campos
        # que legitimamente podem faltar (GCT/oscilacao vertical em
        # relogios sem dinamica de corrida).
        splits = [
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=240, averageHR=170),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=245, averageHR=172),
        ]
        resultado = summarize_work_segments(splits)
        self.assertIsNone(resultado["reps"][0]["avg_power"])  # no tiro, corretamente None
        self.assertIsNone(resultado["avg_power"])              # na media, None -- nao 0.0

    def test_campo_ausente_em_parte_dos_tiros_nao_dilui_a_media(self):
        # Segunda parte da mesma correcao: dividir por n_reps (em vez da
        # quantidade de tiros que realmente tem o campo) diluia a media
        # quando so parte dos tiros tinha o dado -- 1 tiro com
        # avg_power=300 e outro sem, dividido por 2, dava 150.0 em vez dos
        # 300.0 reais (media so do que existe).
        splits = [
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=240,
                      averageHR=170, averagePower=300),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=245, averageHR=172),
        ]
        resultado = summarize_work_segments(splits)
        self.assertEqual(resultado["avg_power"], 300.0)

    def test_distancia_zero_no_segmento_nao_quebra_o_calculo(self):
        splits = [
            _segmento("INTERVAL_ACTIVE", distance=0, duration=0, averageHR=170),
            _segmento("INTERVAL_ACTIVE", distance=1000, duration=240, averageHR=172),
        ]
        resultado = summarize_work_segments(splits)
        self.assertIsNone(resultado["reps"][0]["pace_min_km"])
        self.assertIsNotNone(resultado["avg_pace_min_km"])

    def test_campos_conhecidos_do_work_types(self):
        self.assertEqual(WORK_TYPES, {"INTERVAL_ACTIVE"})


if __name__ == "__main__":
    unittest.main()
