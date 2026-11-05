"""Testes de farol.domain.hrv_variability: CV do HRV em janela movel."""
import sqlite3
import unittest
from datetime import date, timedelta

from farol.core import db
from farol.domain.hrv_variability import hrv_cv


class TestHrvCv(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db.ensure_schema(self.conn)
        self.base_date = date(2026, 9, 2)

    def tearDown(self):
        self.conn.close()

    def _insere(self, dias_atras: int, hrv: float):
        d = (self.base_date - timedelta(days=dias_atras)).isoformat()
        self.conn.execute("INSERT INTO daily (date, hrv_avg) VALUES (?, ?)", (d, hrv))
        self.conn.commit()

    def test_menos_de_4_pontos_na_janela_curta_devolve_none(self):
        for i in [0, 1, 2]:
            self._insere(i, 18.0)
        self.assertIsNone(hrv_cv(self.conn, self.base_date.isoformat()))

    def test_hrv_constante_da_cv_zero(self):
        for i in range(7):
            self._insere(i, 18.0)
        resultado = hrv_cv(self.conn, self.base_date.isoformat())
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["cv_7d"], 0.0)

    def test_hrv_variavel_da_cv_positivo(self):
        valores = [16.0, 20.0, 15.0, 21.0, 17.0, 19.0, 18.0]
        for i, v in enumerate(valores):
            self._insere(i, v)
        resultado = hrv_cv(self.conn, self.base_date.isoformat())
        self.assertGreater(resultado["cv_7d"], 5.0)

    def test_referencia_30d_none_quando_sem_dado_suficiente(self):
        # So os 7 dias curtos preenchidos -- 30d fica abaixo do piso (8).
        for i in range(7):
            self._insere(i, 18.0)
        resultado = hrv_cv(self.conn, self.base_date.isoformat())
        self.assertIsNotNone(resultado)
        self.assertIsNone(resultado["cv_30d"])

    def test_referencia_30d_presente_com_dado_suficiente(self):
        for i in range(20):
            self._insere(i, 16.0 + (i % 5))
        resultado = hrv_cv(self.conn, self.base_date.isoformat())
        self.assertIsNotNone(resultado["cv_30d"])

    def test_ignora_dias_fora_da_janela_de_7(self):
        # Um valor muito diferente, mas so no dia 10 (fora da janela curta),
        # nao pode contaminar o CV de 7 dias.
        self._insere(10, 100.0)
        for i in range(7):
            self._insere(i, 18.0)
        resultado = hrv_cv(self.conn, self.base_date.isoformat())
        self.assertEqual(resultado["cv_7d"], 0.0)


if __name__ == "__main__":
    unittest.main()
