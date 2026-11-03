"""Testes de farol.state: classificacao de dominio, convergencia, e geracao
do relatorio a partir de farol_daily_state."""
import sqlite3
import unittest
from datetime import date, timedelta

from farol import db, state
from farol.daily_state_builder import build_daily_state
from farol.metrics import Metric


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _metric(status: str, confidence: str = "alta", has_data: bool = True) -> Metric:
    """Metric sintetica para testar so a logica de agregacao de dominio, sem
    precisar rodar a regressao real de build_metric."""
    return Metric(
        name="x", value=(1.0 if has_data else None), mean_7d=None, mean_30d=None,
        mean_90d=None, sd_90d=None, z=None, trend="indefinido", status=status,
        confidence=confidence, n_7d=0, n_30d=0, n_90d=0, higher_is_better=True,
    )


class TestDomainStatus(unittest.TestCase):
    def test_primario_ignora_as_demais(self):
        metricas = {"tsb": _metric("alerta", "alta"), "ctl": _metric("ok", "alta")}
        status, conf = state.domain_status(metricas, primario="tsb")
        self.assertEqual(status, "alerta")
        self.assertEqual(conf, "alta")

    def test_sem_primario_usa_o_pior_status(self):
        metricas = {"a": _metric("ok"), "b": _metric("atencao"), "c": _metric("ok")}
        status, _ = state.domain_status(metricas)
        self.assertEqual(status, "atencao")

    def test_sem_primario_usa_a_confianca_mais_baixa(self):
        metricas = {"a": _metric("ok", "alta"), "b": _metric("ok", "baixa")}
        _, conf = state.domain_status(metricas)
        self.assertEqual(conf, "baixa")

    def test_sem_dado_em_todas_as_metricas(self):
        metricas = {"a": _metric("sem_dado", has_data=False),
                    "b": _metric("sem_dado", has_data=False)}
        status, _ = state.domain_status(metricas)
        self.assertEqual(status, "sem_dado")

    def test_metrica_sem_dado_nao_conta_para_o_pior(self):
        metricas = {"a": _metric("sem_dado", has_data=False), "b": _metric("alerta")}
        status, _ = state.domain_status(metricas)
        self.assertEqual(status, "alerta")


class TestConvergence(unittest.TestCase):
    def test_um_dominio_fora_nao_e_padrao(self):
        self.assertIsNone(state.convergence({"CARGA": "alerta", "SONO": "ok"}))

    def test_dois_dominios_fora_e_padrao(self):
        msg = state.convergence({"CARGA": "alerta", "SONO": "atencao", "RECUPERACAO": "ok"})
        self.assertIsNotNone(msg)
        self.assertIn("CARGA", msg)
        self.assertIn("SONO", msg)
        self.assertNotIn("RECUPERACAO", msg)


class TestDominiosEvidencias(unittest.TestCase):
    """C2.1 (auditoria de 2026-09-08): _dominios() expoe evidencias/
    determinantes/contextuais/ausentes -- uma unica fonte (evidencias),
    sem mudar status/confianca/thresholds/regra do pior campo."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        # Baseline de 90 dias so' para CARGA (trimp/ctl/atl/tsb/ratio),
        # com pequena variacao ciclica para sd_90d != 0. RECUPERACAO/SONO
        # ficam de proposito sem NENHUM dado (nem em farol_daily_state, nem
        # em daily) -- e' o cenario de "ausentes" testado abaixo.
        for i in range(90, 0, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO farol_daily_state (date, trimp_7d, trimp_28d, "
                "ctl, atl, tsb, atl_ctl_ratio) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (d, 500 + i % 10, 2000 + i % 20, 60 + i % 5, 60 + i % 5,
                 (i % 5) - 2, 1.0 + (i % 5) * 0.02),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _insere_dia_alvo(self, tsb, ctl, atl, atl_ctl_ratio):
        d = self.base_date.isoformat()
        self.conn.execute(
            "INSERT INTO farol_daily_state (date, trimp_7d, trimp_28d, "
            "ctl, atl, tsb, atl_ctl_ratio) VALUES (?, 500, 2000, ?, ?, ?, ?)",
            (d, ctl, atl, tsb, atl_ctl_ratio),
        )
        self.conn.commit()

    def test_carga_so_tem_tsb_como_determinante(self):
        self._insere_dia_alvo(tsb=0, ctl=61, atl=61, atl_ctl_ratio=1.0)
        carga = state._dominios(self.conn, self.base_date.isoformat())["CARGA"]
        self.assertEqual(carga["determinantes"], ["tsb"])
        self.assertEqual(
            set(carga["contextuais"]),
            {"trimp_7d", "trimp_28d", "ctl", "atl", "atl_ctl_ratio"},
        )

    def test_recuperacao_e_sono_tem_todos_os_campos_como_determinantes(self):
        # Sem primario, domain_status() usa o pior status entre TODAS as
        # metricas -- entao TODAS sao evidencia real, nenhuma contextual.
        self._insere_dia_alvo(tsb=0, ctl=61, atl=61, atl_ctl_ratio=1.0)
        dominios = state._dominios(self.conn, self.base_date.isoformat())
        for nome, campos in (("RECUPERACAO", state._RECUPERACAO), ("SONO", state._SONO)):
            d = dominios[nome]
            self.assertEqual(set(d["determinantes"]), set(campos))
            self.assertEqual(d["contextuais"], [])

    def test_status_e_confianca_continuam_vindo_so_de_domain_status(self):
        # Prova que o status/confianca do dominio nao mudou de fonte:
        # comparado diretamente contra domain_status(), chamado a parte.
        self._insere_dia_alvo(tsb=100, ctl=61, atl=61, atl_ctl_ratio=1.0)
        on_date = self.base_date.isoformat()
        metricas = state.domain_metrics(self.conn, on_date, state._CARGA)
        status_esperado, conf_esperada = state.domain_status(metricas, state._CARGA_PRIMARIO)
        carga = state._dominios(self.conn, on_date)["CARGA"]
        self.assertEqual(carga["status"], status_esperado)
        self.assertEqual(carga["confianca"], conf_esperada)

    def test_evidencias_correspondem_a_logica_realmente_usada(self):
        # Caso C02 da auditoria: TSB perto da media (ok), CTL/ATL/ratio bem
        # fora da baseline (alerta INDIVIDUALMENTE) -- CARGA oficial normal.
        self._insere_dia_alvo(tsb=0, ctl=1, atl=1, atl_ctl_ratio=0.01)
        carga = state._dominios(self.conn, self.base_date.isoformat())["CARGA"]
        self.assertEqual(carga["status"], "ok")

        por_campo = {e["campo"]: e for e in carga["evidencias"]}
        self.assertEqual(por_campo["tsb"]["papel"], "determinante")
        self.assertEqual(por_campo["tsb"]["status"], "ok")
        for contextual in ("ctl", "atl", "atl_ctl_ratio"):
            self.assertEqual(por_campo[contextual]["papel"], "contextual")
            # A metrica contextual pode estar individualmente fora do
            # padrao -- isso fica visivel na evidencia, so' nao vira status.
            self.assertIn(por_campo[contextual]["status"], ("atencao", "alerta"))

    def test_metrica_contextual_nunca_aparece_como_determinante(self):
        self._insere_dia_alvo(tsb=0, ctl=1, atl=1, atl_ctl_ratio=0.01)
        carga = state._dominios(self.conn, self.base_date.isoformat())["CARGA"]
        for contextual in ("ctl", "atl", "atl_ctl_ratio", "trimp_7d", "trimp_28d"):
            self.assertNotIn(contextual, carga["determinantes"])

    def test_ausencia_de_metrica_e_explicita(self):
        self._insere_dia_alvo(tsb=0, ctl=61, atl=61, atl_ctl_ratio=1.0)
        recuperacao = state._dominios(self.conn, self.base_date.isoformat())["RECUPERACAO"]
        self.assertEqual(set(recuperacao["ausentes"]), set(state._RECUPERACAO))

    def test_indices_sao_derivados_de_evidencias_sem_divergir(self):
        self._insere_dia_alvo(tsb=0, ctl=1, atl=1, atl_ctl_ratio=0.01)
        carga = state._dominios(self.conn, self.base_date.isoformat())["CARGA"]

        determinantes_via_evidencias = [e["campo"] for e in carga["evidencias"]
                                         if e["papel"] == "determinante"]
        contextuais_via_evidencias = [e["campo"] for e in carga["evidencias"]
                                       if e["papel"] == "contextual"]
        ausentes_via_evidencias = [e["campo"] for e in carga["evidencias"] if not e["presente"]]

        self.assertEqual(carga["determinantes"], determinantes_via_evidencias)
        self.assertEqual(carga["contextuais"], contextuais_via_evidencias)
        self.assertEqual(carga["ausentes"], ausentes_via_evidencias)


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(90, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, 70, 18.0, 45, 30, 82, 420, 40, 80, 60, 55)""",
                (d,),
            )
        self.conn.commit()
        for i in range(90, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            build_daily_state(self.conn, d)

    def tearDown(self):
        self.conn.close()

    def test_data_sem_farol_daily_state_da_erro_claro(self):
        with self.assertRaises(ValueError):
            state.build_report(self.conn, "1970-01-01")

    def test_relatorio_contem_as_5_secoes(self):
        texto = state.build_report(self.conn, self.base_date.isoformat())
        for secao in ("MATRIZ RAPIDA", "1. CARGA", "2. RECUPERACAO", "3. SONO",
                      "4. PERFORMANCE", "5. OBSERVACAO", "CONVERGENCIA"):
            self.assertIn(secao, texto)

    def test_dia_estavel_nao_gera_convergencia(self):
        # Sem atividades, TSB fica em 0 igual ao historico -- sem desvio.
        texto = state.build_report(self.conn, self.base_date.isoformat())
        self.assertIn("Nenhum padrao", texto)

    def test_performance_sem_treino_intervalado(self):
        texto = state.build_report(self.conn, self.base_date.isoformat())
        self.assertIn("sem dado (sem treino intervalado no dia)", texto)

    def test_observacao_lista_os_breakpoints_conhecidos(self):
        texto = state.build_report(self.conn, self.base_date.isoformat())
        self.assertIn('"field": "rhr"', texto)
        self.assertIn('"field": "awakenings"', texto)


if __name__ == "__main__":
    unittest.main()
