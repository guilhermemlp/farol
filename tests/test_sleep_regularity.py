"""Testes de farol.sleep_regularity: SD circular do horario de dormir.

Funcoes puras -- sem banco, exceto bedtime_regularity() que so le `daily`.
"""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from farol import db
from farol.sleep_regularity import _circular_sd_minutos, _minutos_do_dia_local, bedtime_regularity


def _epoch_ms_local(ano, mes, dia, hora, minuto, offset_horas=-3):
    """Epoch ms (UTC) para um horario local dado -- inverso de
    _minutos_do_dia_local, so para montar fixtures de teste."""
    dt_local = datetime(ano, mes, dia, hora, minuto)
    dt_utc = dt_local - timedelta(hours=offset_horas)
    return int(dt_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


class TestMinutosDoDiaLocal(unittest.TestCase):
    def test_converte_epoch_utc_para_minutos_locais(self):
        # 2026-09-04 00:41 UTC == 2026-09-03 21:41 local (UTC-3, o default)
        ms = _epoch_ms_local(2026, 9, 3, 21, 41)
        self.assertAlmostEqual(_minutos_do_dia_local(ms), 21 * 60 + 41, delta=0.1)

    def test_aceita_string_como_o_banco_guarda(self):
        ms = _epoch_ms_local(2026, 9, 3, 21, 41)
        self.assertAlmostEqual(_minutos_do_dia_local(str(ms)), 21 * 60 + 41, delta=0.1)


class TestCircularSdMinutos(unittest.TestCase):
    """A razao de existir deste modulo: SD linear ingenuo quebra quando o
    horario cruza meia-noite. Cada teste aqui prova um caso que um SD
    ingenuo (statistics.pstdev) acertaria ou erraria."""

    def test_horario_constante_da_sd_proximo_de_zero(self):
        sd = _circular_sd_minutos([23 * 60] * 10)
        self.assertAlmostEqual(sd, 0.0, delta=0.5)

    def test_alternando_ao_redor_da_meia_noite_da_sd_pequeno(self):
        # 23:50 e 00:10 sao 20 minutos de diferenca real -- um SD circular
        # correto fica proximo de 10 (a metade da separacao), nao de ~710
        # (o que um SD linear ingenuo devolveria).
        minutos = [23 * 60 + 50, 10, 23 * 60 + 50, 10, 23 * 60 + 50, 10]
        sd = _circular_sd_minutos(minutos)
        self.assertLess(sd, 15)

    def test_horario_espalhado_pelo_dia_da_sd_grande(self):
        # 22h, 23h, 00h30, 02h -- espalhado por ~4h, deve dar SD de dezenas
        # de minutos, nao proximo de zero.
        sd = _circular_sd_minutos([22 * 60, 23 * 60, 30, 2 * 60])
        self.assertGreater(sd, 60)

    def test_menos_de_2_pontos_devolve_none(self):
        self.assertIsNone(_circular_sd_minutos([23 * 60]))
        self.assertIsNone(_circular_sd_minutos([]))


class TestBedtimeRegularity(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insere_sleep_start(self, data: str, hora: int, minuto: int):
        ms = _epoch_ms_local(*[int(x) for x in data.split("-")], hora, minuto)
        self.conn.execute(
            "INSERT INTO daily (date, sleep_start) VALUES (?, ?)", (data, ms)
        )
        self.conn.commit()

    def test_menos_de_4_dias_com_dado_devolve_none(self):
        for d, h in [("2026-09-01", 23), ("2026-09-02", 23), ("2026-09-03", 23)]:
            self._insere_sleep_start(d, h, 0)
        self.assertIsNone(bedtime_regularity(self.conn, "2026-09-03"))

    def test_horario_regular_da_sd_baixo(self):
        for i, d in enumerate(["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30",
                                "2026-08-31", "2026-09-01", "2026-09-02"]):
            self._insere_sleep_start(d, 22, 30 + (i % 2))  # 22:30 ou 22:31
        sd = bedtime_regularity(self.conn, "2026-09-02")
        self.assertIsNotNone(sd)
        self.assertLess(sd, 5)

    def test_ignora_dias_fora_da_janela(self):
        # Um unico dia muito fora da janela de 14 dias nao deve entrar --
        # senao regularidade recente ficaria contaminada por historico velho.
        self._insere_sleep_start("2026-07-01", 4, 0)  # bem fora da janela
        for d in ["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30"]:
            self._insere_sleep_start(d, 22, 30)
        sd = bedtime_regularity(self.conn, "2026-08-30", window_days=14)
        self.assertLess(sd, 5)


if __name__ == "__main__":
    unittest.main()
