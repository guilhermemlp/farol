"""Testes de farol.reports.daily_analysis: DAILY_ANALYSIS.md, o diagnostico completo
gerado a partir da mesma classificacao de dominio de farol.domain.state."""
import json
import sqlite3
import unittest
from datetime import date, timedelta

from golden_support import skip_unless_real_db
from farol.core import db
from farol.reports import daily_analysis
from farol.domain.daily_state_builder import build_daily_state


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestDailyAnalysisBase(unittest.TestCase):
    """Fixture comum: 91 dias estaveis de daily (sem atividade), replicando o
    padrao ja usado em test_integration.py."""

    def setUp(self):
        # Baseline com pequena variacao ciclica (nao um valor fixo repetido)
        # -- com sd_90d = 0 farol.core.metrics nao calcula z (divisao por zero
        # evitada de proposito) e toda metrica sairia como "sem_dado", o que
        # mascararia os testes de agregacao de dominio abaixo. O dia-alvo
        # recebe o valor medio exato do ciclo (calculado, nao chutado), para
        # que o fixture comece "normal" em todas as metricas -- os testes
        # individuais perturbam so o campo que querem investigar.
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(90, 0, -1):
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
        d = self.base_date.isoformat()
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d,),
        )
        self.conn.commit()
        for i in range(90, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            build_daily_state(self.conn, d)

    def tearDown(self):
        self.conn.close()


class TestGeracaoBasica(TestDailyAnalysisBase):
    def test_gera_texto_nao_vazio_com_data_no_cabecalho(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn(f"DIAGNOSTICO DIARIO — {self.base_date.isoformat()}", texto)
        self.assertIn("Data analisada:", texto)
        self.assertIn("Gerado em:", texto)
        self.assertIn("Periodo de referencia:", texto)
        self.assertIn("Fonte:", texto)

    def test_contem_todas_as_secoes_obrigatorias(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        for secao in (
            "RESUMO EXECUTIVO", "3. CARGA", "4. RECUPERACAO", "5. SONO",
            "6. PERFORMANCE", "7. CONVERGENCIA",
            "8. DESCONTINUIDADES / QUALIDADE DOS DADOS",
            "9. INTERPRETACAO", "10. CONCLUSAO", "11. LEITURA",
        ):
            self.assertIn(secao, texto)

    def test_leitura_aparece_com_campos_em_linguagem(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("Estado fisiologico", texto)
        self.assertIn("Sinal dominante", texto)
        self.assertIn("Convergencia", texto)
        self.assertIn("Conclusao", texto)

    def test_ausencia_de_dados_da_erro_claro(self):
        with self.assertRaises(ValueError):
            daily_analysis.build_analysis(self.conn, "1970-01-01")

    def test_nao_prescreve_treino(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("Nao prescreve treino", texto)


class TestCarga(TestDailyAnalysisBase):
    def test_tsb_e_a_unica_evidencia_do_dominio(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("TSB e a unica evidencia usada para o estado do dominio", texto)
        self.assertIn("nao evidencias adicionais", texto)

    def test_ctl_atl_atl_ctl_aparecem_como_contexto_nao_como_alerta_extra(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("CTL, ATL e ATL/CTL sao contexto", texto)

    def test_dia_sem_atividade_carga_estavel(self):
        # fixture sem activities: trimp=0 todo dia, TSB deve ficar em torno de 0
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("Evidencia de carga elevada?** Nao.", texto)


class TestRecuperacao(TestDailyAnalysisBase):
    def test_tabela_individual_das_5_metricas(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        for rotulo in ("HRV", "RHR", "FC noturna", "Stress", "Body Battery"):
            self.assertIn(rotulo, texto)

    def test_estado_agregado_usa_pior_status(self):
        # fixture estavel -> todas as metricas OK -> dominio NORMAL
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("Estado do dominio: NORMAL", texto)
        self.assertIn("Nenhuma metrica do dominio esta fora do padrao", texto)

    def test_rhr_respeita_quebra_de_fonte(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("respect_source=True", texto)


class TestSono(TestDailyAnalysisBase):
    def test_tabela_individual_das_5_metricas(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        for rotulo in ("Duracao do sono", "Tempo acordado", "Sono profundo", "Sono REM", "Sleep score"):
            self.assertIn(rotulo, texto)

    def test_nao_presume_sleep_score_como_unica_medida(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("nao a unica medida de qualidade", texto)

    def test_sinaliza_metrica_que_sustenta_dominio_fora_do_normal(self):
        # empurra so o sleep_score do dia-alvo para baixo, mantendo o resto estavel
        d = self.base_date.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 5 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        texto = daily_analysis.build_analysis(self.conn, d)
        self.assertIn("Estado puxado por: Sleep score", texto)


class TestPerformance(TestDailyAnalysisBase):
    def test_sem_treino_intervalado_sinaliza_sem_dado(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("sem dado (sem treino intervalado reconhecido no dia)", texto)

    def test_com_sessao_intervalada_reporta_os_dois_campos(self):
        d = self.base_date.isoformat()
        splits = {"n_reps": 2, "avg_pace_min_km": 4.5,
                  "reps": [{"pace_min_km": 4.5, "avg_hr": 160}, {"pace_min_km": 4.5, "avg_hr": 161}]}
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, splits) VALUES (1, ?, 'running', ?)",
            (d, json.dumps(splits)),
        )
        self.conn.commit()
        build_daily_state(self.conn, d)
        texto = daily_analysis.build_analysis(self.conn, d)
        self.assertIn("output_preserved**: indeterminate", texto)
        self.assertIn("deterioration_detected**: no", texto)


class TestConvergencia(TestDailyAnalysisBase):
    def test_um_dominio_isolado_nao_sinaliza_convergencia(self):
        d = self.base_date.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 5 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        texto = daily_analysis.build_analysis(self.conn, d)
        self.assertIn("Sem convergencia sistemica", texto)
        self.assertIn("Sinal isolado em SONO", texto)
        self.assertIn("Existe convergencia?** Nao.", texto)

    def test_dois_dominios_fora_sinaliza_convergencia(self):
        d = self.base_date.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 5, hrv_avg = 2 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        texto = daily_analysis.build_analysis(self.conn, d)
        self.assertIn("Convergencia sinalizada", texto)
        self.assertIn("Existe convergencia?** Sim", texto)


class TestCargaContextualNaoContaComoProblema(TestDailyAnalysisBase):
    """C2.3 (auditoria de 2026-09-08): ponta a ponta (state -> interpretation
    -> daily_analysis), CARGA oficial normal (TSB dentro do padrao) nao pode
    ser citada como alterada em NENHUMA secao textual, mesmo com CTL/ATL/
    atl_ctl_ratio individualmente fora do padrao no mesmo dia."""

    def _monta_baseline_de_carga(self):
        # A fixture base (TestDailyAnalysisBase) nao popula training_load --
        # sem isso, tsb/ctl/atl ficam sem historico (baseline vazia) e o
        # dominio sai 'sem_dado', nao 'ok'. Aqui cada campo recebe uma
        # pequena variacao ciclica perto de um centro estavel (sd_90d != 0),
        # e farol_daily_state e' recalculado a partir dela.
        for i in range(90, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO training_load (date, ctl, atl, tsb) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "ctl=excluded.ctl, atl=excluded.atl, tsb=excluded.tsb",
                (d, 60 + i % 5, 60 + i % 5, (i % 5) - 2),
            )
        self.conn.commit()
        for i in range(90, -1, -1):
            d = (self.base_date - timedelta(days=i)).isoformat()
            build_daily_state(self.conn, d)

    def _forca_carga_ok_com_contexto_ruim(self, d):
        # TSB do dia-alvo dentro da baseline (~0, CARGA oficial 'ok');
        # CTL/ATL/ratio bem abaixo da baseline (~60) -- fora do padrao
        # so' INDIVIDUALMENTE (higher_is_better=True: valor baixo e' ruim).
        self.conn.execute(
            "UPDATE farol_daily_state SET tsb = 0, ctl = -500, atl = -500, "
            "atl_ctl_ratio = -500 WHERE date = ?", (d,)
        )
        self.conn.commit()

    def test_carga_normal_sem_citacao_como_problema(self):
        d = self.base_date.isoformat()
        self._monta_baseline_de_carga()
        self._forca_carga_ok_com_contexto_ruim(d)
        texto = daily_analysis.build_analysis(self.conn, d)

        self.assertIn("Evidencia de carga elevada?** Nao.", texto)
        # A contradicao exata da auditoria: "Nao [deterioracao]" seguido de
        # "-- sinal isolado em CARGA" no mesmo paragrafo, contradizendo a
        # linha logo acima ("Evidencia de carga elevada? Nao.").
        self.assertNotIn("sinal isolado em CARGA", texto)
        self.assertIn("- nenhum.", texto)  # "Sinais fora do padrao por dominio: nenhum."
        self.assertIn("Sinal dominante**: nenhum sinal relevante hoje", texto)
        self.assertIn("Convergencia**: sem convergencia (nenhum dominio fora do padrao)", texto)

    def test_dominio_realmente_alterado_e_citado_corretamente(self):
        # Caso inverso: TSB (o determinante de CARGA) genuinamente fora do
        # padrao -- isto TEM que aparecer, sem supressao nenhuma. CTL/ATL/
        # ratio ficam dentro da propria baseline, para isolar o efeito a
        # TSB.
        d = self.base_date.isoformat()
        self._monta_baseline_de_carga()
        self.conn.execute(
            "UPDATE farol_daily_state SET tsb = -500, ctl = 62, atl = 62, "
            "atl_ctl_ratio = 1.0 WHERE date = ?", (d,)
        )
        self.conn.commit()
        texto = daily_analysis.build_analysis(self.conn, d)
        self.assertIn("Evidencia de deterioracao sistemica?** Sim -- dominio em ALERTA.", texto)
        self.assertIn("Evidencia de carga elevada?** Sim (TSB fora do padrao).", texto)
        self.assertIn("Sinal dominante**: sinal em CARGA: TSB", texto)
        self.assertIn("- CARGA: TSB", texto)  # "Sinais fora do padrao por dominio"

    def test_convergencia_nao_conta_metrica_contextual_como_determinante(self):
        # CARGA contextual ruim (como acima) + SONO genuinamente alterado --
        # convergencia tem que continuar "1 dominio", nunca "2".
        d = self.base_date.isoformat()
        self._monta_baseline_de_carga()
        self.conn.execute("UPDATE daily SET sleep_score = 5 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)  # recalcula SONO (e reseta CARGA a partir de training_load)
        self._forca_carga_ok_com_contexto_ruim(d)  # reaplica so' a sobrescrita de CARGA
        texto = daily_analysis.build_analysis(self.conn, d)

        self.assertIn("Sinal isolado em SONO", texto)  # state.convergence (domain-status)
        self.assertIn("Convergencia**: sem convergencia (1 dominio fora do padrao)", texto)
        self.assertNotIn("CARGA", texto[texto.index("## 11. LEITURA"):])


class TestPersistencia(unittest.TestCase):
    """C5 (VISTORIA V7): streak (janela consecutiva) e trend (janela fixa)
    sao recortes diferentes. Episodio que comecou hoje (streak = 1) com
    trend que varre dias anteriores nao pode ser lido como se a direcao
    fosse do episodio."""

    def setUp(self):
        # Serie controlada: 96 dias estaveis de SONO (score 55, arquitetura
        # fixa); 7 dias atras o score cai a 10 (puxa SONO para alerta NAQUELE
        # dia); ontem volta a ok; hoje cai a 35 (alerta de novo). Resultado
        # verificado: SONO hoje = alerta com streak de 1 dia (episodio
        # comecou hoje) e trend WORSENING numa janela de 7 dias que comeca em
        # 27/08 -- o caso exato da vistoria 04/09, so que piorando.
        self.conn = _memory_db()
        self.base = date(2026, 9, 2)
        for i in range(96, -1, -1):
            d = (self.base - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO daily (date, total_sleep_minutes, deep_sleep_minutes, "
                "rem_sleep_minutes, sleep_score) VALUES (?, 400, 80, 60, 55)", (d,))
        self.conn.execute("UPDATE daily SET sleep_score = 10 WHERE date = ?",
                          ((self.base - timedelta(days=7)).isoformat(),))
        self.conn.execute("UPDATE daily SET sleep_score = 35 WHERE date = ?",
                          (self.base.isoformat(),))
        self.conn.commit()
        for i in range(96, -1, -1):
            d = (self.base - timedelta(days=i)).isoformat()
            build_daily_state(self.conn, d)

    def tearDown(self):
        self.conn.close()

    def _texto(self):
        return daily_analysis.build_analysis(self.conn, self.base.isoformat())

    def test_episodio_de_1_dia_distingue_janela_do_episodio(self):
        texto = self._texto()
        self.assertIn("Ativo ha 1 dia consecutivos", texto)
        # a ressalva C5: a direcao vem da janela fixa, que comeca antes do
        # episodio -- nao e' direcao do episodio
        self.assertIn("direcao e do periodo, nao do episodio", texto)
        self.assertIn("comecou hoje", texto)

    def test_frase_antiga_que_amarrava_recortes_nao_existe_mais(self):
        texto = self._texto()
        # streak de 1 dia nunca mais aparece como "tendencia X (janela de 7
        # dias)" sem a distincao de recortes
        self.assertNotRegex(
            texto, r"Ativo ha 1 dia consecutivos \(desde [^)]+\), tendencia "
            r"(melhorando|piorando|estavel) \(janela de 7 dias\)")


class TestDescontinuidades(TestDailyAnalysisBase):
    def test_lista_campo_data_motivo_e_politica(self):
        texto = daily_analysis.build_analysis(self.conn, self.base_date.isoformat())
        self.assertIn("`rhr`", texto)
        self.assertIn("2026-06-11", texto)
        self.assertIn("pipeline_migration", texto)
        self.assertIn("split_by_source", texto)
        self.assertIn("`awakenings`", texto)
        self.assertIn("2026-08-01", texto)
        self.assertIn("split_by_date", texto)


class TestAusenciaDeDados(unittest.TestCase):
    def test_sem_farol_daily_state_da_value_error(self):
        conn = _memory_db()
        try:
            with self.assertRaises(ValueError):
                daily_analysis.build_analysis(conn, "2026-09-02")
        finally:
            conn.close()


@skip_unless_real_db()
class TestGoldenSample(unittest.TestCase):
    """farol.db real, mesma base que farol.domain.state e farol.reports.daily_analysis
    consomem em producao. Confirma que o diagnostico nao introduz nenhum
    numero diferente do golden sample ja validado."""

    def test_golden_sample_2026_09_02(self):
        conn = db.connect_readonly()
        try:
            texto = daily_analysis.build_analysis(conn, "2026-09-02")
            self.assertIn("66.59", texto)   # CTL
            self.assertIn("79.78", texto)   # ATL
            self.assertIn("-13.19", texto)  # TSB
            self.assertIn("BORDERLINE", texto)  # TSB z=-1.46, nao ALERTA
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
