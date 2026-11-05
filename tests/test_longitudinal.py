"""Testes de farol.domain.longitudinal: persistencia de sinal (streak) por dominio
e por convergencia, ao longo de multiplos dias de farol_daily_state."""
import sqlite3
import unittest
from datetime import date, timedelta

from golden_support import skip_unless_real_db
from farol.core import db
from farol.domain import longitudinal
from farol.domain.daily_state_builder import build_daily_state


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestStreak(unittest.TestCase):
    """Baseline estavel de 90 dias (mesmo truque de variacao ciclica de
    tests/test_daily_analysis.py, para sd_90d != 0) + dias mais recentes
    controlados explicitamente para cada teste."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(120, 10, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10),
            )
        self.conn.commit()
        # farol_daily_state precisa existir tambem para os dias de baseline --
        # senao a janela de 90 dias usada por farol.core.metrics fica sem pontos
        # suficientes (n < 8) e toda metrica sai como "sem_dado".
        for i in range(120, 10, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    def _dia_normal(self, d: str):
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d,),
        )

    def _dia_hrv_baixo(self, d: str):
        """HRV bem abaixo do baseline -> RECUPERACAO em alerta; resto normal."""
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 5, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d,),
        )

    def _dia_sleep_score_baixo(self, d: str):
        """Sleep score bem abaixo do baseline -> SONO em alerta; resto normal."""
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 5)""",
            (d,),
        )

    def _build(self, ate_offset_dias: int):
        for i in range(ate_offset_dias, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            build_daily_state(self.conn, d)

    def test_dia_isolado_fora_do_padrao_tem_streak_1(self):
        for i in range(10, 1, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        self._dia_hrv_baixo((self.base_date - timedelta(days=1)).isoformat())
        self._dia_normal(self.base_date.isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(r["dias"], 0)  # hoje esta normal -- sinal de ontem nao conta

        r_ontem = longitudinal.streak(
            self.conn, (self.base_date - timedelta(days=1)).isoformat(), "RECUPERACAO"
        )
        self.assertEqual(r_ontem["dias"], 1)

    def test_cinco_dias_consecutivos_gera_streak_5(self):
        for i in range(10, 5, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        for i in range(4, -1, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(r["dias"], 5)
        self.assertEqual(r["data_inicio"], (self.base_date - timedelta(days=4)).isoformat())
        self.assertEqual(r["data_fim"], self.base_date.isoformat())

    def test_dia_normal_no_meio_quebra_o_streak(self):
        for i in range(10, 4, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        self._dia_normal((self.base_date - timedelta(days=4)).isoformat())
        for i in range(3, -1, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(r["dias"], 4)  # so os 4 dias apos a quebra

    def test_dominio_ok_tem_streak_zero(self):
        for i in range(10, -1, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(r["dias"], 0)
        self.assertIsNone(r["data_inicio"])
        self.assertIsNone(r["data_fim"])

    def test_buraco_de_dado_interrompe_a_contagem(self):
        for i in range(10, 3, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        # sem inserir daily para offset 3 -- buraco proposital
        for i in range(2, -1, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(r["dias"], 3)  # para no buraco, nao atravessa

    def test_max_dias_limita_a_contagem(self):
        for i in range(10, -1, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO", max_dias=4)
        self.assertEqual(r["dias"], 4)

    def test_dominio_invalido_gera_erro(self):
        with self.assertRaises(ValueError):
            longitudinal.streak(self.conn, self.base_date.isoformat(), "PERFORMANCE")

    def test_convergencia_exige_dois_dominios_simultaneos(self):
        for i in range(10, 2, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        # 3 dias com RECUPERACAO e SONO ruins ao mesmo tempo
        for i in range(2, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, 70, 5, 44.5, 30.5, 82, 418, 39, 79, 59, 5)""",
                (d,),
            )
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), None)
        self.assertEqual(r["dias"], 3)
        self.assertEqual(r["dominio"], "CONVERGENCIA")

    def test_convergencia_nao_conta_dominio_isolado(self):
        for i in range(10, 5, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        for i in range(4, -1, -1):
            self._dia_hrv_baixo((self.base_date - timedelta(days=i)).isoformat())  # so RECUPERACAO
        self.conn.commit()
        self._build(10)

        r = longitudinal.streak(self.conn, self.base_date.isoformat(), None)
        self.assertEqual(r["dias"], 0)

    def test_all_streaks_devolve_os_3_dominios_e_convergencia(self):
        for i in range(10, -1, -1):
            self._dia_normal((self.base_date - timedelta(days=i)).isoformat())
        self.conn.commit()
        self._build(10)

        r = longitudinal.all_streaks(self.conn, self.base_date.isoformat())
        self.assertEqual(set(r.keys()), {"CARGA", "RECUPERACAO", "SONO", "CONVERGENCIA"})
        self.assertTrue(all(v["dias"] == 0 for v in r.values()))

    def test_data_sem_farol_daily_state_devolve_streak_zero_sem_erro(self):
        r = longitudinal.streak(self.conn, "1970-01-01", "CARGA")
        self.assertEqual(r["dias"], 0)
        self.assertIsNone(r["data_inicio"])


class TestTrend(unittest.TestCase):
    """Baseline estavel (mesmo padrao ciclico das outras classes deste
    arquivo, para sd_90d != 0) + sequencia recente controlada por teste.

    Sem activities/training_load nesta fixture -> tsb fica sempre None ->
    CARGA sai sempre 'sem_dado'. Isso e aproveitado de proposito no teste de
    dominio sem dado, em vez de simular isso a parte."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(120, 10, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10),
            )
        self.conn.commit()
        for i in range(120, 10, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    # HRV baseline: media 17.75, sd ~0.563 (calculado, nao chutado).
    # 17.2 -> z ~ -0.98 (atencao); 5 -> z ~ -22.6 (alerta); 17.75 -> z 0 (ok).
    def _dia(self, d: str, hrv: float):
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, ?, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d, hrv),
        )

    def _sequencia(self, hrvs: list[float | None], ate_offset: int):
        """`hrvs[0]` = dia mais antigo da janela, `hrvs[-1]` = on_date.
        `None` = pula o dia (buraco de dado proposital)."""
        n = len(hrvs)
        for idx, hrv in enumerate(hrvs):
            offset = ate_offset - idx
            d = (self.base_date - timedelta(days=offset)).isoformat()
            if hrv is not None:
                self._dia(d, hrv)
        self.conn.commit()
        for idx in range(n):
            offset = ate_offset - idx
            if hrvs[idx] is None:
                continue
            d = (self.base_date - timedelta(days=offset)).isoformat()
            build_daily_state(self.conn, d)

    def test_tendencia_de_deterioracao_progressiva(self):
        # NORMAL, NORMAL, NORMAL, BORDERLINE, BORDERLINE, BORDERLINE, ALERTA
        self._sequencia([17.75, 17.75, 17.75, 17.2, 17.2, 17.2, 5], ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "WORSENING")
        self.assertEqual(r["n_pontos"], 7)

    def test_tendencia_de_melhora(self):
        # ALERTA, ALERTA, ALERTA, ALERTA, NORMAL, NORMAL, NORMAL
        self._sequencia([5, 5, 5, 5, 17.75, 17.75, 17.75], ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "IMPROVING")

    def test_sequencia_completamente_estavel(self):
        self._sequencia([17.75] * 7, ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "STABLE")

    def test_oscilacao_nao_vira_tendencia_artificial(self):
        # NORMAL, BORDERLINE, BORDERLINE, ALERTA, BORDERLINE, NORMAL, NORMAL
        self._sequencia([17.75, 17.2, 17.2, 5, 17.2, 17.75, 17.75], ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "STABLE")

    def test_mudanca_recente_de_direcao_e_capturada(self):
        # 4 dias normais, depois vira alerta nos ultimos 3 -- fim != inicio
        self._sequencia([17.75, 17.75, 17.75, 17.75, 5, 5, 5], ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "WORSENING")

    def test_dados_insuficientes_com_um_ponto_so(self):
        self._sequencia([None, None, None, None, None, None, 17.75], ate_offset=6)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "INSUFFICIENT_DATA")
        self.assertEqual(r["n_pontos"], 1)

    def test_dados_insuficientes_sem_nenhum_ponto(self):
        r = longitudinal.trend(self.conn, "1970-01-01", "RECUPERACAO", window=7)
        self.assertEqual(r["direcao"], "INSUFFICIENT_DATA")
        self.assertEqual(r["n_pontos"], 0)

    def test_buraco_no_meio_da_janela_e_omitido_nao_quebra(self):
        # dia 3 (do meio) sem registro -- pontos validos: ok,ok,ok,alerta,alerta (5)
        self._sequencia([17.75, 17.75, None, 17.75, 5, 5], ate_offset=5)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=6)
        self.assertEqual(r["n_pontos"], 5)
        self.assertEqual(r["direcao"], "WORSENING")

    def test_dominio_sem_dado_na_janela_inteira(self):
        # fixture sem activities/training_load -> tsb sempre None -> CARGA sempre sem_dado
        self._sequencia([17.75] * 7, ate_offset=6)  # so pra ter farol_daily_state nos dias
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "CARGA", window=7)
        self.assertEqual(r["direcao"], "INSUFFICIENT_DATA")
        self.assertEqual(r["n_pontos"], 0)

    def test_janela_menor_que_a_solicitada_usa_o_que_existe(self):
        # so 3 dias reais de farol_daily_state a partir de ate_offset=2,
        # mas window pedida e 7 -- nao ha erro, so menos pontos
        self._sequencia([17.75, 17.75, 5], ate_offset=2)
        r = longitudinal.trend(self.conn, self.base_date.isoformat(), "RECUPERACAO", window=7)
        self.assertEqual(r["n_pontos"], 3)
        self.assertEqual(r["direcao"], "WORSENING")

    def test_dominio_invalido_gera_erro(self):
        with self.assertRaises(ValueError):
            longitudinal.trend(self.conn, self.base_date.isoformat(), "PERFORMANCE")

    def test_window_invalida_gera_erro(self):
        with self.assertRaises(ValueError):
            longitudinal.trend(self.conn, self.base_date.isoformat(), "CARGA", window=0)

    def test_convergencia_worsening_quando_dois_dominios_pioram_juntos(self):
        for idx, offset in enumerate(range(6, -1, -1)):
            d = (self.base_date - timedelta(days=offset)).isoformat()
            if idx < 4:
                self.conn.execute(
                    """INSERT INTO daily
                       (date, rhr, hrv_avg, stress_avg, body_battery_max,
                        night_hr_avg, total_sleep_minutes, awake_minutes,
                        deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                       VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
                    (d,),
                )
            else:
                # RECUPERACAO (hrv) e SONO (sleep_score) ruins ao mesmo tempo
                self.conn.execute(
                    """INSERT INTO daily
                       (date, rhr, hrv_avg, stress_avg, body_battery_max,
                        night_hr_avg, total_sleep_minutes, awake_minutes,
                        deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                       VALUES (?, 70, 5, 44.5, 30.5, 82, 418, 39, 79, 59, 5)""",
                    (d,),
                )
        self.conn.commit()
        for offset in range(6, -1, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=offset)).isoformat())

        r = longitudinal.trend(self.conn, self.base_date.isoformat(), None, window=7)
        self.assertEqual(r["dominio"], "CONVERGENCIA")
        self.assertEqual(r["direcao"], "WORSENING")

    def test_all_trends_devolve_os_3_dominios_e_convergencia(self):
        self._sequencia([17.75] * 7, ate_offset=6)
        r = longitudinal.all_trends(self.conn, self.base_date.isoformat())
        self.assertEqual(set(r.keys()), {"CARGA", "RECUPERACAO", "SONO", "CONVERGENCIA"})
        self.assertEqual(r["RECUPERACAO"]["direcao"], "STABLE")

    def test_streak_nao_foi_afetado_pela_adicao_de_trend(self):
        # mesma sequencia de deterioracao -- streak deve continuar contando
        # dias ativos exatamente como antes de trend() existir.
        self._sequencia([17.75, 17.75, 17.75, 17.2, 17.2, 17.2, 5], ate_offset=6)
        s = longitudinal.streak(self.conn, self.base_date.isoformat(), "RECUPERACAO")
        self.assertEqual(s["dias"], 4)  # 17.2,17.2,17.2,5 -- os 4 ultimos dias fora do ok


@skip_unless_real_db()
class TestTrendDadosReais(unittest.TestCase):
    """farol.db real, so leitura -- confirma que trend() roda sobre uma
    janela real e nao altera nada."""

    def test_2026_09_02_vs_2026_09_03_fazem_sentido_com_o_estado_diario(self):
        conn = db.connect_readonly()
        try:
            antes = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())

            t_02 = longitudinal.all_trends(conn, "2026-09-02")
            t_03 = longitudinal.all_trends(conn, "2026-09-03")

            for r in (*t_02.values(), *t_03.values()):
                self.assertIn(r["direcao"], ("IMPROVING", "STABLE", "WORSENING", "INSUFFICIENT_DATA"))

            # 2026-09-02 tinha convergencia ativa ha 7 dias (ver sessao anterior);
            # 2026-09-03 voltou a NORMAL em todos os dominios -- a janela de 7
            # dias terminando em 09-03 tem que mostrar melhora ou estabilidade
            # na convergencia, nunca piora.
            self.assertIn(t_03["CONVERGENCIA"]["direcao"], ("IMPROVING", "STABLE"))

            depois = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())
            self.assertEqual(antes, depois)
            self.assertAlmostEqual(depois["ctl"], 66.59, places=1)
            self.assertAlmostEqual(depois["atl"], 79.78, places=1)
            self.assertAlmostEqual(depois["tsb"], -13.19, places=1)
        finally:
            conn.close()


class TestEpisodes(unittest.TestCase):
    """Baseline estavel (mesmo padrao ciclico das outras classes) + sequencia
    recente controlada por teste, igual a TestTrend."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(120, 10, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10),
            )
        self.conn.commit()
        for i in range(120, 10, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    # HRV baseline: media 17.75, sd ~0.563. 17.2 -> atencao; 5 -> alerta; 17.75 -> ok.
    def _dia(self, offset: int, hrv: float | None, sleep_score: float = 54.5):
        if hrv is None:
            return  # buraco de dado proposital -- nao insere a linha
        d = (self.base_date - timedelta(days=offset)).isoformat()
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, ?, 44.5, 30.5, 82, 418, 39, 79, 59, ?)""",
            (d, hrv, sleep_score),
        )
        self.conn.commit()
        build_daily_state(self.conn, d)

    def _d(self, offset: int) -> str:
        return (self.base_date - timedelta(days=offset)).isoformat()

    def _dia_com_status(self, offset: int, alvo: str) -> str:
        """Insere/reescreve o dia em `offset` ate ele classificar como
        `alvo` (ok/atencao/alerta) dado o estado atual do banco.

        Baselines sao recalculadas ao vivo contra o que ja existe em
        farol_daily_state -- um dia vizinho extremo (ex.: alerta) infla o
        desvio-padrao da janela de 90d o bastante pra empurrar um valor que
        seria "atencao" isolado de volta pra "ok". Por isso resolve
        empiricamente em vez de fixar um hrv por calculo isolado; precisa ser
        chamado em ordem cronologica (offset mais alto primeiro) para os dias
        mais recentes verem a baseline ja com os anteriores desta sequencia."""
        d = self._d(offset)
        for hrv in (17.75, 17.4, 17.2, 17.0, 16.5, 16.0, 15.0, 13.0, 10.0, 7.0, 5.0, 3.0, 1.0):
            self.conn.execute("DELETE FROM daily WHERE date = ?", (d,))
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, 70, ?, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
                (d, hrv),
            )
            self.conn.commit()
            build_daily_state(self.conn, d)
            if longitudinal.domain_status_by_date(self.conn, d).get("RECUPERACAO") == alvo:
                return d
        raise AssertionError(f"nao encontrei hrv que produza status={alvo!r} em {d}")

    def test_nenhum_episodio(self):
        for off in range(6, -1, -1):
            self._dia(off, 17.75)
        r = longitudinal.episodes(self.conn, self._d(6), self._d(0), "RECUPERACAO")
        self.assertEqual(r, [])

    def test_episodio_simples_de_um_dominio_que_termina(self):
        # ok ok alerta alerta alerta ok ok
        seq = [17.75, 17.75, 5, 5, 5, 17.75, 17.75]
        for i, hrv in enumerate(seq):
            self._dia(6 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(6), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 1)
        ep = r[0]
        self.assertEqual(ep["dias"], 3)
        self.assertEqual(ep["data_inicio"], self._d(4))
        self.assertEqual(ep["data_fim"], self._d(2))
        self.assertFalse(ep["em_andamento"])
        self.assertEqual(ep["severidade_maxima"], "alerta")

    def test_episodio_ainda_ativo_no_final_da_janela(self):
        seq = [17.75, 17.75, 17.75, 5, 5, 5, 5]  # termina em alerta, sem retorno
        for i, hrv in enumerate(seq):
            self._dia(6 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(6), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 1)
        ep = r[0]
        self.assertEqual(ep["dias"], 4)
        self.assertEqual(ep["data_fim"], self._d(0))
        self.assertTrue(ep["em_andamento"])

    def test_dia_normal_no_meio_separa_em_dois_episodios(self):
        seq = [5, 5, 17.75, 5, 5]
        for i, hrv in enumerate(seq):
            self._dia(4 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(4), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["dias"], 2)
        self.assertEqual(r[1]["dias"], 2)
        self.assertFalse(r[0]["em_andamento"])
        self.assertTrue(r[1]["em_andamento"])  # o segundo termina no fim da janela

    def test_sem_dado_no_meio_tambem_separa_episodios(self):
        seq = [5, 5, None, 5, 5]
        for i, hrv in enumerate(seq):
            self._dia(4 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(4), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["dias"], 2)
        self.assertEqual(r[1]["dias"], 2)

    def test_oscilacao_entre_atencao_e_alerta_e_um_episodio_so(self):
        # atencao alerta atencao alerta atencao -- nunca volta a ok
        alvos = ["atencao", "alerta", "atencao", "alerta", "atencao"]
        for i, alvo in enumerate(alvos):
            self._dia_com_status(4 - i, alvo)
        r = longitudinal.episodes(self.conn, self._d(4), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["dias"], 5)
        self.assertEqual(r[0]["severidade_maxima"], "alerta")  # pico, mesmo nao sendo o ultimo dia

    def test_ausencia_total_de_dados(self):
        r = longitudinal.episodes(self.conn, "1970-01-01", "1970-01-10", "CARGA")
        self.assertEqual(r, [])

    def test_episodio_iniciado_antes_da_janela_consultada(self):
        # ativo do dia -8 ao dia -3 (6 dias), consulta so comeca no dia -5
        for off in range(8, 2, -1):
            self._dia(off, 5)
        for off in range(2, -1, -1):
            self._dia(off, 17.75)
        r = longitudinal.episodes(self.conn, self._d(5), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 1)
        # streak() enxergou o inicio real (-8), anterior a start_date (-5) da consulta
        self.assertEqual(r[0]["data_inicio"], self._d(8))
        self.assertEqual(r[0]["data_fim"], self._d(3))

    def test_severidade_maxima_dentro_do_mesmo_episodio(self):
        # atencao, alerta, atencao -- pico e alerta, extremos iguais (atencao)
        alvos = ["atencao", "alerta", "atencao"]
        for i, alvo in enumerate(alvos):
            self._dia_com_status(2 - i, alvo)
        r = longitudinal.episodes(self.conn, self._d(2), self._d(0), "RECUPERACAO")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["severidade_maxima"], "alerta")
        self.assertEqual(r[0]["direcao"], "STABLE")  # extremos (atencao, atencao) iguais

    def test_integracao_com_streak(self):
        seq = [17.75, 5, 5, 5]
        for i, hrv in enumerate(seq):
            self._dia(3 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(3), self._d(0), "RECUPERACAO")
        s = longitudinal.streak(self.conn, self._d(0), "RECUPERACAO")
        self.assertEqual(r[-1]["dias"], s["dias"])
        self.assertEqual(r[-1]["data_inicio"], s["data_inicio"])
        self.assertEqual(r[-1]["data_fim"], s["data_fim"])

    def test_integracao_com_trend(self):
        seq = [17.75, 17.75, 17.2, 17.2, 5]
        for i, hrv in enumerate(seq):
            self._dia(4 - i, hrv)
        r = longitudinal.episodes(self.conn, self._d(4), self._d(0), "RECUPERACAO")
        ep = r[-1]
        t = longitudinal.trend(self.conn, ep["data_fim"], "RECUPERACAO", window=ep["dias"])
        self.assertEqual(ep["direcao"], t["direcao"])

    def test_dominio_invalido_gera_erro(self):
        with self.assertRaises(ValueError):
            longitudinal.episodes(self.conn, self._d(3), self._d(0), "PERFORMANCE")

    def test_end_date_antes_de_start_date_gera_erro(self):
        with self.assertRaises(ValueError):
            longitudinal.episodes(self.conn, self._d(0), self._d(3), "CARGA")

    def test_convergencia_envolve_multiplos_dominios(self):
        # RECUP e SONO ruins juntos por 3 dias -- convergencia so existe
        # quando os dois estao ativos ao mesmo tempo (2+ dominios).
        # (Entrada/saida de dominio DURANTE um mesmo episodio de convergencia
        # -- casos 6/7 da especificacao -- fica provado com dado real em
        # TestEpisodesDadosReais: esta fixture so tem RECUP e SONO
        # controlaveis, sem training_load pra CARGA entrar/sair tambem.)
        for off in (2, 1, 0):
            self._dia(off, 5, sleep_score=5)
        r = longitudinal.episodes(self.conn, self._d(2), self._d(0), None)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["dias"], 3)
        self.assertEqual(sorted(r[0]["dominios_envolvidos"]), ["RECUPERACAO", "SONO"])
        self.assertEqual(r[0]["severidade_maxima"], 2)

    def test_all_episodes_devolve_os_3_dominios_e_convergencia(self):
        for off in range(3, -1, -1):
            self._dia(off, 17.75)
        r = longitudinal.all_episodes(self.conn, self._d(3), self._d(0))
        self.assertEqual(set(r.keys()), {"CARGA", "RECUPERACAO", "SONO", "CONVERGENCIA"})

    def test_streak_e_trend_nao_foram_alterados_pela_adicao_de_episodes(self):
        seq = [17.75, 17.2, 17.2, 5]
        for i, hrv in enumerate(seq):
            self._dia(3 - i, hrv)
        s = longitudinal.streak(self.conn, self._d(0), "RECUPERACAO")
        t = longitudinal.trend(self.conn, self._d(0), "RECUPERACAO", window=4)
        self.assertEqual(s["dias"], 3)
        self.assertEqual(t["direcao"], "WORSENING")


@skip_unless_real_db()
class TestEpisodesDadosReais(unittest.TestCase):
    """farol.db real, so leitura -- caso de referencia 2026-08-21 a 2026-09-03
    fornecido na especificacao. Confirma a semantica de episodios contra o
    historico real ja validado por streak()/trend() nas etapas anteriores."""

    def test_caso_real_2026_08_21_a_2026_09_03(self):
        conn = db.connect_readonly()
        try:
            antes = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())

            todos = longitudinal.all_episodes(conn, "2026-08-21", "2026-09-03")

            # CARGA: 3 episodios (22-24, 26, 28-09/02) -- o do meio tem 1 dia so,
            # provando que nao existe piso de duracao.
            self.assertEqual([e["dias"] for e in todos["CARGA"]], [3, 1, 6])
            self.assertEqual(todos["CARGA"][-1]["data_inicio"], "2026-08-28")
            self.assertEqual(todos["CARGA"][-1]["data_fim"], "2026-09-02")
            self.assertFalse(todos["CARGA"][-1]["em_andamento"])  # 09-03 volta a ok, dentro da janela

            # SONO: episodio mais antigo comeca ANTES do inicio da janela pedida (08-21)
            self.assertLess(todos["SONO"][0]["data_inicio"], "2026-08-21")
            self.assertEqual(todos["SONO"][-1]["dias"], 7)
            self.assertEqual(todos["SONO"][-1]["data_inicio"], "2026-08-27")
            self.assertEqual(todos["SONO"][-1]["data_fim"], "2026-09-02")

            # CONVERGENCIA: 2 episodios (22-24 e 27-09/02).
            #
            # Historico deste teste (documentado porque o valor ja mudou duas
            # vezes por razoes opostas): o parecer tecnico de 2026-09-04
            # acrescentou body_battery_min a RECUPERACAO (recomendacao 1),
            # o que fez 26/08 (body_battery_min=5, baseline 90d=22.43,
            # z=-1.17) entrar em RECUPERACAO=atencao e, com CARGA=alerta ja
            # existente, esticar este episodio para 8 dias (26/08-09/02).
            # Rodar o pipeline real de ponta a ponta no mesmo dia mostrou que
            # body_battery_min sozinho marca atencao/alerta em 55% dos dias
            # com dado -- o dobro de qualquer outro campo do dominio (todos
            # entre 16-29%, sd_90d=15.1 sobre media 20.4, CV ~74%) -- um
            # campo genuinamente mais volatil, nao um achado fisiologico
            # novo. Removido de volta de _RECUPERACAO (mesmo tratamento de
            # vo2max/sleep_regularity_min: guardado, exibido em farol.reports.today,
            # fora de domain_status/streak/convergencia), o que devolve este
            # episodio aos 7 dias originais.
            self.assertEqual(len(todos["CONVERGENCIA"]), 2)
            conv2 = todos["CONVERGENCIA"][-1]
            self.assertEqual(conv2["dias"], 7)
            self.assertEqual(conv2["data_inicio"], "2026-08-27")
            self.assertEqual(conv2["data_fim"], "2026-09-02")
            self.assertFalse(conv2["em_andamento"])
            # entrada/saida de dominio: RECUP e CARGA nao estao ativos o tempo
            # todo, mas os dois contribuem em algum dia do episodio
            self.assertEqual(conv2["dominios_envolvidos"], ["CARGA", "RECUPERACAO", "SONO"])

            depois = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())
            self.assertEqual(antes, depois)
            self.assertAlmostEqual(depois["ctl"], 66.59, places=1)
            self.assertAlmostEqual(depois["atl"], 79.78, places=1)
            self.assertAlmostEqual(depois["tsb"], -13.19, places=1)
        finally:
            conn.close()


@skip_unless_real_db()
class TestGoldenSampleNaoAlterado(unittest.TestCase):
    """farol.domain.longitudinal so le farol_daily_state -- confirma que consultar
    o streak do golden sample nao muda CTL/ATL/TSB nem precisa dele mudar."""

    def test_golden_sample_2026_09_02_preservado(self):
        conn = db.connect_readonly()
        try:
            antes = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())
            longitudinal.all_streaks(conn, "2026-09-02")
            depois = dict(conn.execute(
                "SELECT ctl, atl, tsb FROM farol_daily_state WHERE date = '2026-09-02'"
            ).fetchone())
            self.assertEqual(antes, depois)
            self.assertAlmostEqual(depois["ctl"], 66.59, places=1)
            self.assertAlmostEqual(depois["atl"], 79.78, places=1)
            self.assertAlmostEqual(depois["tsb"], -13.19, places=1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
