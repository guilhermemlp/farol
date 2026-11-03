"""Testes de farol.changepoint: estimativa CUSUM de quebra de nivel.

O teste mais importante nao esta aqui, e sim documentado no docstring do
modulo e no commit: rodar scan() contra o data/farol.db real acha as duas
quebras de medicao ja conhecidas e documentadas (rhr ~2026-06-04/11,
awakenings 2026-08-01), a poucos dias da data real. Os testes abaixo
validam a matematica com series sinteticas onde o ponto de quebra e'
conhecido exatamente.
"""
import sqlite3
import unittest
from datetime import date, timedelta

from farol import db
from farol.changepoint import CampoInvalido, _cusum_peak, render_report, scan


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestCusumPeak(unittest.TestCase):
    def test_serie_constante_devolve_none(self):
        self.assertIsNone(_cusum_peak([50.0] * 40))

    def test_degrau_limpo_acha_o_ponto_exato(self):
        # 40 pontos em 50, depois 40 pontos em 60 -- a quebra e' exatamente
        # entre o indice 39 e 40.
        valores = [50.0] * 40 + [60.0] * 40
        idx = _cusum_peak(valores)
        self.assertIn(idx, (39, 40))

    def test_degrau_no_meio_de_serie_maior(self):
        valores = [30.0] * 60 + [45.0] * 60
        idx = _cusum_peak(valores)
        self.assertTrue(58 <= idx <= 61)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 1, 1)

    def tearDown(self):
        self.conn.close()

    def _insere_serie(self, valores: list[float]):
        for i, v in enumerate(valores):
            d = (self.base_date + timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, rhr) VALUES (?, ?)", (d, v))
        self.conn.commit()

    def test_pouco_dado_devolve_none(self):
        self._insere_serie([70.0] * 10)
        self.assertIsNone(scan(self.conn, "rhr"))

    def test_serie_estavel_sem_quebra_devolve_none(self):
        # Ruido pequeno em torno de uma media constante -- nao deve gerar
        # falso positivo.
        valores = [70.0 + (i % 5 - 2) * 0.5 for i in range(100)]
        self._insere_serie(valores)
        self.assertIsNone(scan(self.conn, "rhr"))

    def test_degrau_real_e_detectado_com_data_correta(self):
        valores = [70.0] * 60 + [78.0] * 60
        self._insere_serie(valores)
        resultado = scan(self.conn, "rhr")
        self.assertIsNotNone(resultado)
        data_esperada = (self.base_date + timedelta(days=60)).isoformat()
        # Tolerancia de alguns dias -- mesmo padrao de precisao observado
        # contra o historico real (achou a 2-5 dias da data documentada).
        delta_dias = abs(
            (date.fromisoformat(resultado.data) - date.fromisoformat(data_esperada)).days
        )
        self.assertLessEqual(delta_dias, 3)
        self.assertGreater(resultado.shift_sd, 1.0)
        self.assertAlmostEqual(resultado.media_antes, 70.0, delta=0.5)
        self.assertAlmostEqual(resultado.media_depois, 78.0, delta=0.5)

    def test_degrau_pequeno_demais_devolve_none(self):
        # Deslocamento real, mas pequeno demais frente ao ruido para passar
        # do piso de 1.0 SD -- confirma que o piso funciona, nao so o CUSUM.
        import random
        rnd = random.Random(42)
        valores = [70.0 + rnd.uniform(-4, 4) for _ in range(60)]
        valores += [70.5 + rnd.uniform(-4, 4) for _ in range(60)]
        self._insere_serie(valores)
        resultado = scan(self.conn, "rhr")
        self.assertIsNone(resultado)


class TestAteCorteTemporal(unittest.TestCase):
    """T1.3 (auditoria de 2026-09-08): _serie/scan/render_report aceitam
    `ate` opcional -- uma leitura as-of nao pode estimar quebra usando
    pontos posteriores ao corte."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 1, 1)

    def tearDown(self):
        self.conn.close()

    def _insere_serie(self, valores: list[float]):
        for i, v in enumerate(valores):
            d = (self.base_date + timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, rhr) VALUES (?, ?)", (d, v))
        self.conn.commit()

    def test_ate_esconde_quebra_que_so_acontece_depois_do_corte(self):
        # Degrau real em torno do dia 60 (70 -> 78), mas `ate` corta a serie
        # no dia 40 -- antes do corte so existe patamar estavel, entao
        # scan(ate=...) nao pode enxergar uma quebra que so existe DEPOIS.
        valores = [70.0] * 60 + [78.0] * 60
        self._insere_serie(valores)
        ate = (self.base_date + timedelta(days=40)).isoformat()

        sem_corte = scan(self.conn, "rhr")
        self.assertIsNotNone(sem_corte)  # a quebra existe na serie completa

        com_corte = scan(self.conn, "rhr", ate=ate)
        self.assertIsNone(com_corte)  # mas nao e' visivel antes do corte

    def test_ate_none_preserva_comportamento_atual(self):
        valores = [70.0] * 60 + [78.0] * 60
        self._insere_serie(valores)
        max_date = (self.base_date + timedelta(days=len(valores) - 1)).isoformat()

        sem_ate = scan(self.conn, "rhr")
        com_ate_explicito = scan(self.conn, "rhr", ate=max_date)
        self.assertEqual(sem_ate, com_ate_explicito)

    def test_render_report_respeita_ate(self):
        valores = [70.0] * 60 + [78.0] * 60
        self._insere_serie(valores)
        ate = (self.base_date + timedelta(days=40)).isoformat()

        texto_sem_corte = render_report(self.conn, ["rhr"])
        texto_com_corte = render_report(self.conn, ["rhr"], ate=ate)

        self.assertIn("78", texto_sem_corte)  # media_depois da quebra real
        self.assertNotIn("78", texto_com_corte)  # nao visivel antes do corte
        self.assertIn("nenhuma candidata", texto_com_corte)


class TestValidacaoDeCampo(unittest.TestCase):
    """Achado da auditoria de 2026-09-04: --campo entrava direto numa
    f-string SQL sem checagem, produzindo sqlite3.OperationalError cru para
    qualquer valor invalido. scan()/render_report() agora recusam antes de
    montar a query."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def test_campo_inexistente_levanta_campo_invalido(self):
        with self.assertRaises(CampoInvalido):
            scan(self.conn, "rhr); DROP TABLE daily;--")

    def test_tabela_inexistente_levanta_campo_invalido(self):
        with self.assertRaises(CampoInvalido):
            scan(self.conn, "rhr", tabela="tabela_que_nao_existe")

    def test_campo_valido_nao_levanta(self):
        try:
            scan(self.conn, "rhr")
        except CampoInvalido:
            self.fail("scan() recusou um campo valido")

    def test_render_report_reporta_erro_sem_quebrar_os_outros_campos(self):
        # Um --campo invalido nao pode impedir o relatorio dos outros --campo
        # validos passados na mesma chamada.
        texto = render_report(self.conn, ["campo_invalido", "rhr"])
        self.assertIn("ERRO", texto)
        self.assertIn("campo_invalido", texto)


if __name__ == "__main__":
    unittest.main()
