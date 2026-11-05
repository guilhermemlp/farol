"""Testes de farol.insights.race_prediction: as tres formulas contra valores
publicados, a leitura de provas observadas do banco, o relatorio com as
secoes OBSERVADO/ESTIMATIVA/A VALIDAR, e o caso real com farol.core.db."""
import sqlite3
import unittest

from golden_support import skip_unless_real_db
from farol.core import db
from farol.insights import race_prediction


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


# ------------------------------------------------------------- formula Riegel

class TestRiegel(unittest.TestCase):
    def test_expoente_padrao_1_06(self):
        # T2 = T1 x (D2/D1)^1.06 -- 5k 20min -> 10k deve ser ~2.08x o tempo,
        # nao 2x (fadiga adiciona ~6% acima do linear na duplicacao).
        t = race_prediction.riegel_time(5, 20, 10)
        self.assertAlmostEqual(t, 20 * 2 ** 1.06, places=6)
        self.assertGreater(t, 40)  # mais lento que linear


# ------------------------------------------------------------- formula Cameron

class TestCameron(unittest.TestCase):
    def test_duplica_distancia_e_mais_lento_que_linear(self):
        t = race_prediction.cameron_time(5, 20, 10)
        self.assertGreater(t, 40)
        # Cameron tende a ser conservador em distancias longas: 5k -> 10k
        # deve ficar proximo de Riegel, e normalmente acima do linear.
        r = race_prediction.riegel_time(5, 20, 10)
        self.assertAlmostEqual(t, r, delta=2)

    def test_identidade(self):
        self.assertAlmostEqual(
            race_prediction.cameron_time(10, 60, 10), 60, places=6)


# ------------------------------------------------------------------- VDOT

class TestVdot(unittest.TestCase):
    def test_5k_20min_da_vdot_referencia_publicada(self):
        # 5k em 20:00 -> VDOT ~49.8 (validado contra tabelas publicas).
        vd = race_prediction.vdot_from_time(5, 20)
        self.assertAlmostEqual(vd, 49.8, delta=0.3)

    def test_10k_40min_da_vdot_alta(self):
        vd = race_prediction.vdot_from_time(10, 40)
        self.assertGreater(vd, 50)

    def test_tempo_volta_ao_ponto_de_partida(self):
        # round-trip: VDOT de uma prova, aplicado de volta na mesma distancia,
        # reproduz o tempo original (dentro do erro da busca binaria).
        vd = race_prediction.vdot_from_time(10.033, 59.4)
        t = race_prediction.vdot_time(vd, 10.033)
        self.assertAlmostEqual(t, 59.4, delta=0.5)

    def test_mais_lento_tem_vdot_menor(self):
        vd_fast = race_prediction.vdot_from_time(10, 50)
        vd_slow = race_prediction.vdot_from_time(10, 70)
        self.assertGreater(vd_fast, vd_slow)


# ------------------------------------------------------------ predict (3 modelos)

class TestPredict(unittest.TestCase):
    def test_tem_as_tres_chaves_e_faixa_contem_todas(self):
        p = race_prediction.predict(10.033, 59.4, 21.1)
        for k in ("riegel", "cameron", "vdot", "min", "max"):
            self.assertIn(k, p)
        self.assertLessEqual(p["min"], min(p["riegel"], p["cameron"], p["vdot"]))
        self.assertGreaterEqual(p["max"], max(p["riegel"], p["cameron"], p["vdot"]))

    def test_caso_real_10k_para_meia_dentro_de_faixa_esperada(self):
        # Caso real: 10,033 km em 59,4 min (prova de 16/08) -> meia ~2:11
        # (Riegel/Cameron/VDOT convergem em 2:10:30-2:11:30).
        p = race_prediction.predict(10.033, 59.4, 21.1)
        self.assertGreater(p["max"], 130)   # ~2:11:00
        self.assertLess(p["min"], 132)
        for t in (p["riegel"], p["cameron"], p["vdot"]):
            self.assertGreater(t, 128)
            self.assertLess(t, 134)


# ---------------------------------------------------- leitura do banco / relatorio

class TestObservedPerformances(unittest.TestCase):
    def test_le_atividade_real_do_banco(self):
        conn = _memory_db()
        try:
            conn.execute(
                "INSERT INTO activities (activity_id, date, type, name, "
                "distance_km, duration_min, avg_hr, max_hr) "
                "VALUES (1, '2026-08-16', 'running', 'Prova 10k', "
                "10.033, 59.4, 182, 192)")
            conn.commit()
            provas = race_prediction.observed_performances(conn, ["2026-08-16"])
            self.assertEqual(len(provas), 1)
            p = provas[0]
            self.assertAlmostEqual(p["distance_km"], 10.033)
            self.assertAlmostEqual(p["duration_min"], 59.4)
            self.assertEqual(p["avg_hr"], 182)
        finally:
            conn.close()

    def test_ignora_data_sem_corrida(self):
        conn = _memory_db()
        try:
            provas = race_prediction.observed_performances(conn, ["2026-08-16"])
            self.assertEqual(provas, [])
        finally:
            conn.close()

    def test_tem_campo_de_treino_nao_conta_como_corrida_por_tipo(self):
        conn = _memory_db()
        try:
            conn.execute(
                "INSERT INTO activities (activity_id, date, type, name, "
                "distance_km, duration_min) VALUES (1, '2026-08-16', "
                "'strength_training', 'Forca', 0.0, 40)")
            conn.commit()
            provas = race_prediction.observed_performances(conn, ["2026-08-16"])
            self.assertEqual(provas, [])
        finally:
            conn.close()


class TestReport(unittest.TestCase):
    def _conn_com_prova(self):
        conn = _memory_db()
        conn.execute(
            "INSERT INTO activities (activity_id, date, type, name, "
            "distance_km, duration_min, avg_hr, max_hr) "
            "VALUES (1, '2026-08-16', 'running', 'Recife - Track & Field "
            "Run Series - RioMar Recife (10 km)', 10.033, 59.4, 182, 192)")
        conn.execute("INSERT INTO training_load (date, trimp_day, ctl, atl, tsb) "
                     "VALUES ('2026-09-04', 0, 60, 70, -10)")
        conn.commit()
        return conn

    def _perfil(self):
        return {
            "race": {"date": "2026-09-19", "distance_km": 21.1,
                     "name": "Meia-maratona"},
            "recent_races": [{"date": "2026-08-16",
                              "name": "Track & Field Run Series"}],
        }

    def test_contem_as_tres_secoes(self):
        conn = self._conn_com_prova()
        try:
            texto = race_prediction.build_report(conn, self._perfil(),
                                                 on_date="2026-09-04")
            self.assertIn("## O QUE E DADO OBSERVADO", texto)
            self.assertIn("## ESTIMATIVA", texto)
            self.assertIn("## A VALIDAR", texto)
        finally:
            conn.close()

    def test_mostra_prova_observada_e_tabela_de_modelos(self):
        conn = self._conn_com_prova()
        try:
            texto = race_prediction.build_report(conn, self._perfil(),
                                                 on_date="2026-09-04")
            self.assertIn("10.03 km", texto)
            self.assertIn("Track & Field Run Series", texto)
            for modelo in ("Riegel", "Cameron", "VDOT (Daniels)", "Faixa"):
                self.assertIn(modelo, texto)
            self.assertIn("TSB", texto)
        finally:
            conn.close()

    def test_mostra_alvo_e_dias(self):
        conn = self._conn_com_prova()
        try:
            texto = race_prediction.build_report(conn, self._perfil(),
                                                 on_date="2026-09-04")
            self.assertIn("Meia-maratona", texto)
            self.assertIn("15 dias", texto)
        finally:
            conn.close()

    def test_sem_prova_observada_nao_chuta_tempo(self):
        conn = _memory_db()
        try:
            texto = race_prediction.build_report(conn, self._perfil(),
                                                 on_date="2026-09-04")
            self.assertIn("nao ha predicao", texto)
            self.assertNotIn("| Riegel |", texto)
        finally:
            conn.close()

    def test_nao_prescreve_treino(self):
        conn = self._conn_com_prova()
        try:
            texto = race_prediction.build_report(conn, self._perfil(),
                                                 on_date="2026-09-04")
            self.assertIn("do treinador", texto)
            self.assertIn("nao prescreve treino", texto)
        finally:
            conn.close()


@skip_unless_real_db()
class TestGoldenSample(unittest.TestCase):
    """farol.db real -- mesma base que a predicao consome em producao."""

    def test_caso_real_2026_08_16(self):
        conn = db.connect_readonly()
        try:
            provas = race_prediction.observed_performances(conn, ["2026-08-16"])
            self.assertEqual(len(provas), 1)
            p = provas[0]
            self.assertAlmostEqual(p["distance_km"], 10.033, places=2)
            p_ = race_prediction.predict(p["distance_km"], p["duration_min"], 21.1)
            # meia predita da prova de 16/08: ~2:11
            for t in (p_["riegel"], p_["cameron"], p_["vdot"]):
                self.assertGreater(t, 127)
                self.assertLess(t, 135)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
