"""Testes de farol.reports.dashboard: helpers puros (series/tabela markdown->HTML) e
a montagem do contexto/HTML contra um banco minimo em memoria. Nao retesta
logica de dominio ja coberta em outros modulos (recovery_index, weekly_load,
race_prediction, state.gather) -- dashboard.py so orquestra e renderiza."""
import math
import sqlite3
import unittest
from datetime import date, timedelta

from golden_support import skip_unless_real_db
from farol.core import db
from farol.reports import dashboard
from farol.core.config import load_profile


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
        "race_nutrition": {"gel_carbohydrate_g": 27, "gel_caffeine_mg": 75, "interval_minutes": 30,
                           "pre_start_minutes": 15, "caffeine_at_minutes": 60,
                           "last_gel_cutoff_minutes": 15, "coffee_before_race": True},
        "recent_races": [{"date": "2026-08-16", "name": "Prova 10k"}],
    }


def _popula_um_dia(conn, on_date: str = "2026-09-04") -> None:
    conn.execute(
        "INSERT INTO daily (date, hrv_avg, rhr, night_hr_avg, stress_avg, "
        "body_battery_min, respiration_avg, spo2_avg, sleep_score, "
        "total_sleep_minutes, avg_sleep_stress, deep_sleep_minutes, "
        "light_sleep_minutes, rem_sleep_minutes, awakenings, vo2max, source) "
        "VALUES (?, 18, 73, 84, 40, 6, 15, 97, 58, 418.9, 39, 57, 275, 87, 59, 39.4, 'garmin')",
        (on_date,),
    )
    conn.execute(
        "INSERT INTO training_load (date, trimp_day, ctl, atl, tsb) VALUES (?, 59, 65.1, 68.5, -3.35)",
        (on_date,),
    )
    conn.execute(
        "INSERT INTO activities (activity_id, date, type, name, distance_km, "
        "duration_min, avg_hr, max_hr, trimp, source) VALUES (1, ?, 'running', "
        "'Corrida', 4.4, 30.0, 161, 174, 59, 'garmin')",
        (on_date,),
    )
    conn.execute("INSERT INTO farol_daily_state (date) VALUES (?)", (on_date,))
    conn.commit()


class TestSeries(unittest.TestCase):
    def test_devolve_data_e_valor_em_ordem(self):
        conn = _memory_db()
        try:
            for i, v in enumerate([10, 11, 12]):
                conn.execute(
                    "INSERT INTO daily (date, hrv_avg) VALUES (?, ?)",
                    (f"2026-09-0{i + 1}", v),
                )
            conn.commit()
            serie = dashboard._series(conn, "hrv_avg", "2026-09-03", days=30)
            self.assertEqual(serie, [("2026-09-01", 10.0), ("2026-09-02", 11.0), ("2026-09-03", 12.0)])
        finally:
            conn.close()

    def test_ignora_nulos_e_datas_fora_da_janela(self):
        conn = _memory_db()
        try:
            conn.execute("INSERT INTO daily (date, hrv_avg) VALUES ('2026-01-01', 5)")
            conn.execute("INSERT INTO daily (date, hrv_avg) VALUES ('2026-09-01', NULL)")
            conn.execute("INSERT INTO daily (date, hrv_avg) VALUES ('2026-09-02', 20)")
            conn.commit()
            serie = dashboard._series(conn, "hrv_avg", "2026-09-02", days=5)
            self.assertEqual(serie, [("2026-09-02", 20.0)])
        finally:
            conn.close()


class TestMdTableHtml(unittest.TestCase):
    def test_converte_tabela_markdown_em_html(self):
        md = "# titulo\n\n| a | b |\n|---|---|\n| x | 1 |\n| y | 2 |\n"
        out = dashboard._md_table_html(md)
        self.assertIn("<table", out)
        self.assertIn("<th>a</th>", out)
        self.assertIn("<td>x</td>", out)
        self.assertIn('<td class="num">1</td>', out)

    def test_sem_tabela_devolve_fallback(self):
        out = dashboard._md_table_html("so texto, sem pipe nenhum")
        self.assertIn("Sem dado", out)

    def test_escapa_html_das_celulas(self):
        md = "| x |\n|---|\n| <script> |\n"
        out = dashboard._md_table_html(md)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class TestBuildContext(unittest.TestCase):
    def test_monta_contexto_com_um_dia_de_dado(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            self.assertEqual(ctx["on_date"], "2026-09-04")
            self.assertIn("hrv_avg", ctx["metricas"])
            self.assertEqual(ctx["metricas"]["hrv_avg"].value, 18.0)
            self.assertIsNotNone(ctx["state"])
            self.assertIsNotNone(ctx["leitura"])
            self.assertEqual(len(ctx["atividades"]), 1)
            self.assertIsInstance(ctx["semanas"], list)
        finally:
            conn.close()

    def test_sem_farol_daily_state_nao_quebra(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            conn.execute("DELETE FROM farol_daily_state")
            conn.commit()
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            self.assertIsNone(ctx["state"])
            self.assertIsNone(ctx["leitura"])
        finally:
            conn.close()

    def test_sem_prova_observada_predicao_fica_none(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            perfil = _perfil()
            perfil["recent_races"] = []
            ctx = dashboard.build_context(conn, perfil, "2026-09-04")
            self.assertIsNotNone(ctx["race"])
            self.assertIsNone(ctx["race"]["predicao"])
        finally:
            conn.close()

    def test_sem_meta_de_prova_no_perfil(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            perfil = _perfil()
            perfil["race"] = {}
            ctx = dashboard.build_context(conn, perfil, "2026-09-04")
            self.assertIsNone(ctx["race"])
        finally:
            conn.close()


class TestBuildContextAsOf(unittest.TestCase):
    """T1.4 (auditoria de 2026-09-08): build_context so pode usar
    correlacao/changepoint calculados com dado disponivel ate on_date."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _popula_ate_dia(self, conn, base: date, ate_offset: int, stress_por_dia: dict):
        for i in range(ate_offset + 1):
            d = (base + timedelta(days=i)).isoformat()
            sleep_score = 60 - (stress_por_dia.get(i - 1, 50) - 50)
            conn.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score, rhr) VALUES (?, ?, ?, ?)",
                (d, stress_por_dia[i], sleep_score, 70.0),
            )
        conn.commit()

    def test_correlations_e_changepoint_nao_veem_dado_posterior_a_on_date(self):
        base = date(2026, 1, 1)
        ate_offset = 39
        on_date = (base + timedelta(days=ate_offset)).isoformat()
        stress_por_dia = {i: 50 + 20 * math.sin(2 * math.pi * i / 14) for i in range(ate_offset + 1)}

        self._popula_ate_dia(self.conn, base, ate_offset, stress_por_dia)
        # "Futuro" (so depois de on_date): degrau real de rhr + valor extremo
        # de sleep_score -- se o corte vazar, mudam correlations_md/changepoint_md.
        for i in range(ate_offset + 1, ate_offset + 61):
            d = (base + timedelta(days=i)).isoformat()
            sleep_score = -9999 if i == ate_offset + 1 else 50
            self.conn.execute(
                "INSERT INTO daily (date, stress_avg, sleep_score, rhr) VALUES (?, ?, ?, ?)",
                (d, 50, sleep_score, 90.0),
            )
        self.conn.commit()

        ctx = dashboard.build_context(self.conn, _perfil(), on_date)

        conn_sem_futuro = _memory_db()
        self._popula_ate_dia(conn_sem_futuro, base, ate_offset, stress_por_dia)
        ctx_baseline = dashboard.build_context(conn_sem_futuro, _perfil(), on_date)
        conn_sem_futuro.close()

        self.assertEqual(ctx["correlations_md"], ctx_baseline["correlations_md"])
        self.assertEqual(ctx["changepoint_md"], ctx_baseline["changepoint_md"])

    def test_secao_de_analises_rotula_o_corte_no_html(self):
        base = date(2026, 1, 1)
        on_date = (base + timedelta(days=10)).isoformat()
        self._popula_ate_dia(self.conn, base, 10, {i: 50 for i in range(11)})
        ctx = dashboard.build_context(self.conn, _perfil(), on_date)
        out = dashboard.render_html(ctx)
        self.assertIn(f"ate {on_date}", out)


class TestRaceSectionAsOf(unittest.TestCase):
    """T1.4 (auditoria de 2026-09-08): a ancora de previsao de prova nunca
    pode ser uma prova posterior a on_date."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _insere_prova(self, activity_id: int, on_date: str,
                       distance_km: float = 10.0, duration_min: float = 45.0):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, name, distance_km, "
            "duration_min, avg_hr, max_hr, trimp) VALUES "
            "(?, ?, 'running', 'Prova', ?, ?, 160, 175, 50)",
            (activity_id, on_date, distance_km, duration_min),
        )
        self.conn.commit()

    def test_ancora_ignora_prova_posterior_a_on_date(self):
        self._insere_prova(101, "2026-06-01", 10.0, 45.0)  # passado, valida
        self._insere_prova(102, "2026-08-16", 10.0, 40.0)  # futuro relativo a on_date
        perfil = _perfil()
        perfil["recent_races"] = [
            {"date": "2026-06-01", "name": "Prova de junho"},
            {"date": "2026-08-16", "name": "Prova de agosto"},
        ]
        ctx = dashboard.build_context(self.conn, perfil, "2026-07-01")
        self.assertIsNotNone(ctx["race"]["predicao"])
        self.assertEqual(ctx["race"]["ancora"]["date"], "2026-06-01")

    def test_sem_prova_ate_on_date_fica_sem_predicao_sem_inventar_ancora(self):
        self._insere_prova(101, "2026-08-16", 10.0, 40.0)  # so existe prova FUTURA
        perfil = _perfil()
        perfil["recent_races"] = [{"date": "2026-08-16", "name": "Prova de agosto"}]
        ctx = dashboard.build_context(self.conn, perfil, "2026-07-01")
        self.assertIsNotNone(ctx["race"])
        self.assertIsNone(ctx["race"]["predicao"])
        self.assertNotIn("ancora", ctx["race"])


class TestHistoricalPerformancePoints(unittest.TestCase):
    def test_pace_em_movimento_e_fonte_verificada(self):
        conn = _memory_db()
        self.addCleanup(conn.close)
        for aid, history in [(-1, "strava_export"), (-2, None)]:
            conn.execute(
                "INSERT INTO activities (activity_id, date, type, distance_km, "
                "duration_min, moving_minutes, avg_pace_min_km, source, "
                "historical_source) VALUES (?, '2026-03-01', 'running', 5, "
                "35, 30, 6, 'legado', ?)", (aid, history))
        points = dashboard._performance_series(conn, _perfil(), "2026-03-01")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["pace"], 6.0)


class TestRenderHtml(unittest.TestCase):
    def test_html_contem_secoes_essenciais_sem_quebrar(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            out = dashboard.render_html(ctx)
        finally:
            conn.close()
        self.assertIn("<title>Farol</title>", out)
        self.assertIn("Teste da Silva", out)
        self.assertIn("Resumo rapido", out)
        self.assertIn("Carga de treino", out)
        self.assertIn("Recuperacao", out)
        self.assertIn("Previsao de prova", out)
        self.assertIn("Prontidao para a prova", out)
        self.assertIn("Desempenho recente", out)
        self.assertIn("Plano de nutricao da prova", out)
        self.assertIn("Monitoramento de lesoes", out)
        self.assertIn("Calibracao automatica do modelo", out)
        self.assertIn("Registro pos-prova", out)
        self.assertIn("Caminhadas e recuperacoes nao entram", out)
        self.assertIn("Perfil", out)
        self.assertIn("nao prescreve treino", out)

    def test_resumo_rapido_destaca_o_alerta_principal(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            ctx["doenca"] = None
            ctx["padroes"] = ["padrao sintetico de teste"]
            out = dashboard._resumo_html(ctx)
        finally:
            conn.close()
        self.assertIn("Resumo rapido", out)
        self.assertIn("Atencao", out)
        self.assertIn("padrao sintetico de teste", out)

    def test_cartoes_de_dominio_mostram_confianca_streak_e_tendencia(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            carga = ctx["state"]["dominios"]["CARGA"]
            carga["confianca"] = "alta"
            carga["streak"] = {"dominio": "CARGA", "dias": 3,
                               "data_inicio": "2026-09-02", "data_fim": "2026-09-04"}
            carga["trend"] = {"dominio": "CARGA", "direcao": "IMPROVING", "n_pontos": 3}
            out = dashboard._resumo_html(ctx)
        finally:
            conn.close()
        self.assertIn("confianca alta", out)
        self.assertIn("3 dia(s) de streak", out)
        self.assertIn("desde 2026-09-02", out)
        self.assertIn("melhorando", out)

    def test_dominio_sem_tendencia_nao_inventa_direcao(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            out = dashboard._resumo_html(ctx)
        finally:
            conn.close()
        self.assertIn("sem base 7d", out)
        self.assertIn("sem streak ativo", out)
        self.assertIn("confianca baixa", out)

    def test_indice_mostra_contribuintes_do_proprio_dia(self):
        """Notas por campo vem de recovery_index(); o dashboard so' as exibe
        -- teste de formatacao, com valores ja' calculados pelo dominio."""
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            ctx["indice"] = 6.4
            ctx["notas"] = {"sleep_score": 7.0, "hrv_avg": 6.2,
                            "rhr": 5.5, "tsb": 6.9}
            ctx["pesos"] = {"sleep_score": 0.30, "hrv_avg": 0.30,
                            "rhr": 0.20, "tsb": 0.20}
            out = dashboard._resumo_html(ctx)
        finally:
            conn.close()
        self.assertIn('class="contrib"', out)
        for rotulo in ("Sono", "HRV", "FC repouso", "TSB"):
            self.assertIn(f"<b>{rotulo}</b>", out)
        self.assertIn("6.2", out)

    def test_prova_realizada_nao_mostra_prontidao_nem_nutricao(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-10-01")
            out = dashboard.render_html(ctx)
        finally:
            conn.close()
        self.assertIn('<div class="k">Prova em</div><div class="v">pos-prova</div>', out)
        self.assertIn("nenhuma futura declarada", out)
        self.assertNotIn("alvo conceitual p/ prova", out)
        self.assertNotIn("Prontidao para a prova", out)
        self.assertNotIn("Plano de nutricao da prova", out)
        self.assertIn("Registro pos-prova", out)

    def test_nome_do_atleta_e_escapado(self):
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            perfil = _perfil()
            perfil["athlete"]["name"] = "<script>alert(1)</script>"
            ctx = dashboard.build_context(conn, perfil, "2026-09-04")
            out = dashboard.render_html(ctx)
        finally:
            conn.close()
        self.assertNotIn("<script>alert(1)</script>", out)


class TestPmc(unittest.TestCase):
    """Fase 2: serie de 180 dias sem vazamento temporal e anotacoes de contexto."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def test_quebras_conhecidas_entram_uma_vez_por_dia(self):
        _popula_um_dia(self.conn)
        ctx = dashboard.build_context(self.conn, _perfil(), "2026-09-04")
        quebras = [a["date"] for a in ctx["pmc"]["anotacoes"] if a["kind"] == "quebra"]
        self.assertIn("2026-06-11", quebras)
        self.assertEqual(quebras.count("2026-08-01"), 1)

    def test_prova_declarada_dentro_da_janela_vira_anotacao(self):
        _popula_um_dia(self.conn)
        ctx = dashboard.build_context(self.conn, _perfil(), "2026-09-04")
        provas = [a["label"] for a in ctx["pmc"]["anotacoes"] if a["kind"] == "prova"]
        self.assertIn("Prova 10k", provas)

    def test_serie_e_anotacoes_param_em_on_date(self):
        _popula_um_dia(self.conn)
        for dia in ("2026-09-05", "2026-09-06", "2026-09-19"):
            self.conn.execute(
                "INSERT INTO training_load (date, trimp_day, ctl, atl, tsb) "
                "VALUES (?, 50, 80, 90, -10)", (dia,))
        self.conn.commit()
        ctx = dashboard.build_context(self.conn, _perfil(), "2026-09-04")
        self.assertEqual(ctx["pmc"]["series"][-1]["date"], "2026-09-04")
        self.assertTrue(all(a["date"] <= "2026-09-04" for a in ctx["pmc"]["anotacoes"]))
        self.assertNotIn("Meia-maratona", [a["label"] for a in ctx["pmc"]["anotacoes"]])


class TestRenderHtmlEstatico(unittest.TestCase):
    """Fase 1 do redesenho: pagina sem dependencia externa e com os graficos
    ja desenhados no HTML (imprime e abre com JavaScript desligado)."""

    def _html(self) -> str:
        conn = _memory_db()
        try:
            _popula_um_dia(conn)
            ctx = dashboard.build_context(conn, _perfil(), "2026-09-04")
            return dashboard.render_html(ctx)
        finally:
            conn.close()

    def test_nao_depende_de_fonte_ou_recurso_externo(self):
        out = self._html()
        self.assertNotIn("fonts.googleapis.com", out)
        self.assertNotIn("http://", out)
        self.assertNotIn("https://", out)

    def test_graficos_vem_desenhados_no_servidor(self):
        out = self._html()
        self.assertNotIn("FAROL_DATA", out)
        self.assertNotIn("farolSpark", out)
        self.assertNotIn("farolPerformance", out)
        self.assertIn("farolNutrition", out)
        self.assertGreaterEqual(out.count("<svg"), 8)
        self.assertIn('role="img"', out)
        self.assertIn("spark-line", out)
        self.assertIn("bullet-dot", out)
        self.assertIn("Carga de 180 dias (PMC)", out)
        self.assertIn('<svg class="pmc"', out)

    def test_secoes_terciarias_ficam_em_details_fechado(self):
        out = self._html()
        self.assertEqual(out.count('<details class="fold"'), 5)
        self.assertIn("Monitoramento de lesoes", out)
        self.assertIn("Calibracao automatica do modelo", out)
        inicio = out.index('<details class="fold"><summary><span>Perfil')
        fim = out.index("</details>", inicio)
        self.assertIn("data/profile.yaml", out[inicio:fim])

    def test_pagina_cabe_no_orcamento(self):
        self.assertLess(len(self._html()), 150_000)


@skip_unless_real_db()
class TestGoldenSample(unittest.TestCase):
    """farol.db real -- mesmo banco que o comando consome em producao."""

    def test_gera_html_para_2026_09_04_sem_excecao(self):
        conn = db.connect_readonly()
        try:
            profile = load_profile()
            out = dashboard.build_html(conn, profile, "2026-09-04")
        finally:
            conn.close()
        self.assertIn("<title>Farol</title>", out)
        self.assertIn("2026-09-04", out)


if __name__ == "__main__":
    unittest.main()
