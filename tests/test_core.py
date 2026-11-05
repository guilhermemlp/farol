"""Testes do núcleo aritmético: TRIMP, carga de treino e a primitiva Metric.

Rodar:  python -m unittest discover -s tests

São as três coisas de que todo o resto depende. A arqueologia dos 12 projetos
anteriores lista a falta de teste como causa recorrente, e o BUGS.md do AthletIQ
registra uma fórmula de biomecânica invertida que sobreviveu meses — exatamente
o tipo de defeito que estes testes pegam em um segundo.
"""
import hashlib
import json
import os
import sqlite3
import unittest
from datetime import date, timedelta

from golden_support import skip_unless_real_db
from farol.core import db
from farol.domain import analysis
from farol.pipeline import sheets
from farol.reports import report_sections, today
from farol.core.config import DB_PATH
from farol.domain.daily_state_builder import build_daily_state
from farol.core.metrics import Metric, build_metric, from_daily
from farol.domain.recovery import recovery_status
from farol.domain.training_load import recompute_training_load, trimp


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _hash_arquivo(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _clone_real_db_em_memoria() -> sqlite3.Connection:
    """Le data/farol.db em modo SOMENTE LEITURA e clona o conteudo para um
    banco em memoria -- os testes abaixo leem dado pessoal real (hrv, sono,
    atividades), mas qualquer escrita de build_daily_state() cai no clone,
    nunca no arquivo.

    Achado de 2026-09-08: antes desta correcao, TestBuildDailyState abria
    data/farol.db direto via db.connect() e chamava build_daily_state() nele
    -- rodar a suite completa alterava farol_daily_state.trimp_28d de
    2026-09-02 no banco pessoal (2012.39 -> 1894.89, a correcao legitima de
    T1.1 acontecendo como efeito colateral de um teste, nao de uma decisao
    deliberada). O valor corrigido foi mantido (e' o derivado correto), mas
    o teste nao pode ter esse poder de novo."""
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(":memory:")
        src.backup(dest)
    finally:
        src.close()
    dest.row_factory = sqlite3.Row
    db.ensure_schema(dest)  # migra o CLONE em memoria, nunca o arquivo real
    return dest


class TestTrimp(unittest.TestCase):
    """TRIMP de Banister: duracao * r * 0.64 * e^(1.92r), r = reserva de FC usada."""

    def test_valor_conhecido(self):
        # r = (150-70)/(190-70) = 0.6667 -> 60 * 0.6667 * 0.64 * e^1.28
        self.assertAlmostEqual(trimp(60, 150, 70, 190), 92.07, places=1)

    def test_cresce_com_intensidade(self):
        leve = trimp(60, 120, 70, 190)
        forte = trimp(60, 170, 70, 190)
        self.assertGreater(forte, leve)

    def test_cresce_com_duracao(self):
        curto = trimp(30, 150, 70, 190)
        longo = trimp(60, 150, 70, 190)
        self.assertAlmostEqual(longo, curto * 2, places=1)

    def test_dado_faltando_nao_inventa_carga(self):
        # Regra do BUGS.md do AthletIQ: metrica ausente nunca vira 0 implicito
        # com significado -- aqui 0.0 quer dizer "nao da para calcular".
        self.assertEqual(trimp(None, 150, 70, 190), 0.0)
        self.assertEqual(trimp(60, None, 70, 190), 0.0)
        self.assertEqual(trimp(60, 150, None, 190), 0.0)

    def test_fc_abaixo_do_repouso_nao_gera_carga_negativa(self):
        self.assertEqual(trimp(60, 60, 70, 190), 0.0)

    def test_fc_acima_da_maxima_satura_em_vez_de_explodir(self):
        no_teto = trimp(60, 190, 70, 190)
        acima = trimp(60, 210, 70, 190)
        self.assertEqual(no_teto, acima)


class TestTrainingLoad(unittest.TestCase):
    """CTL/ATL/TSB por media movel exponencial (tau 42 / 7)."""

    def setUp(self):
        self.conn = _memory_db()
        self.d0 = date(2026, 1, 1)
        for i in range(30):
            d = (self.d0 + timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, rhr) VALUES (?, 70)", (d,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _add_activity(self, day_offset: int, trimp_value: float):
        d = (self.d0 + timedelta(days=day_offset)).isoformat()
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, trimp) VALUES (?, ?, 'running', ?)",
            (900000 + day_offset, d, trimp_value),
        )
        self.conn.commit()

    def test_sem_atividade_a_carga_fica_zerada(self):
        rows = recompute_training_load(self.conn, self.d0.isoformat())
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(r["ctl"] == 0 and r["atl"] == 0 for r in rows))

    def test_atl_reage_mais_rapido_que_ctl(self):
        # tau_atl=7 contra tau_ctl=42: no dia do treino a fadiga aguda sobe mais.
        self._add_activity(0, 100.0)
        rows = recompute_training_load(self.conn, self.d0.isoformat())
        self.assertGreater(rows[0]["atl"], rows[0]["ctl"])

    def test_tsb_negativo_logo_apos_carga_e_positivo_depois_do_descanso(self):
        self._add_activity(0, 100.0)
        rows = recompute_training_load(self.conn, self.d0.isoformat())
        self.assertLess(rows[0]["tsb"], 0)          # carga fresca pesa
        self.assertGreater(rows[-1]["tsb"], 0)      # 29 dias sem treinar, ja recuperou

    def test_tsb_e_sempre_ctl_menos_atl(self):
        self._add_activity(3, 80.0)
        self._add_activity(10, 120.0)
        for r in recompute_training_load(self.conn, self.d0.isoformat()):
            self.assertAlmostEqual(r["tsb"], r["ctl"] - r["atl"], places=2)

    def test_idempotente(self):
        self._add_activity(5, 90.0)
        primeira = recompute_training_load(self.conn, self.d0.isoformat())
        segunda = recompute_training_load(self.conn, self.d0.isoformat())
        self.assertEqual(primeira, segunda)


class TestBuildMetric(unittest.TestCase):
    """Nucleo puro da primitiva: so listas de float, sem banco."""

    def test_media_desvio_e_z(self):
        janela = [10.0] * 10 + [20.0] * 10   # media 15, pstdev 5
        m = build_metric("t", 20.0, janela, janela, janela)
        self.assertEqual(m.mean_90d, 15.0)
        self.assertEqual(m.sd_90d, 5.0)
        self.assertEqual(m.z, 1.0)

    def test_o_dia_alvo_nao_entra_na_propria_baseline(self):
        # Se o valor de hoje entrasse na janela, a media subiria e o z encolheria.
        # build_metric confia que quem chama ja excluiu -- este teste documenta
        # o contrato.
        m = build_metric("t", 30.0, [10.0] * 10, [10.0] * 10, [10.0] * 10)
        self.assertEqual(m.mean_90d, 10.0)

    def test_direcao_inverte_o_status(self):
        janela = [70.0] * 10 + [80.0] * 10   # media 75, sd 5
        alto = 90.0                          # z = +3

        hrv_alto = build_metric("hrv", alto, janela, janela, janela, higher_is_better=True)
        rhr_alto = build_metric("rhr", alto, janela, janela, janela, higher_is_better=False)

        self.assertEqual(hrv_alto.status, "ok")      # HRV alto e bom
        self.assertEqual(rhr_alto.status, "alerta")  # FC de repouso alta nao e

    def test_confianca_vem_da_cobertura_real(self):
        self.assertEqual(build_metric("t", 1.0, [], [], [1.0] * 25).confidence, "alta")
        self.assertEqual(build_metric("t", 1.0, [], [], [1.0] * 10).confidence, "media")
        self.assertEqual(build_metric("t", 1.0, [], [], [1.0] * 3).confidence, "baixa")

    def test_poucos_pontos_nao_produzem_z(self):
        m = build_metric("t", 20.0, [10.0], [10.0], [10.0, 12.0, 11.0])
        self.assertIsNone(m.sd_90d)
        self.assertIsNone(m.z)
        self.assertEqual(m.status, "sem_dado")

    def test_tendencia(self):
        mes = [10.0] * 20 + [20.0] * 20       # media 15, sd 5
        caindo = build_metric("t", 15.0, [5.0] * 7, mes, mes)
        subindo = build_metric("t", 15.0, [25.0] * 7, mes, mes)
        estavel = build_metric("t", 15.0, [15.0] * 7, mes, mes)
        self.assertEqual(caindo.trend, "caindo")
        self.assertEqual(subindo.trend, "subindo")
        self.assertEqual(estavel.trend, "estavel")

    def test_sem_valor_nao_quebra(self):
        m = build_metric("t", None, [], [], [])
        self.assertFalse(m.has_data)
        self.assertIsNone(m.z)
        self.assertIsNone(m.delta_vs_baseline)
        self.assertEqual(m.status, "sem_dado")
        self.assertEqual(m.trend, "indefinido")


class TestFromDaily(unittest.TestCase):
    """Adaptador de banco: janelas com fim exclusivo."""

    def setUp(self):
        self.conn = _memory_db()
        self.hoje = date(2026, 3, 1)
        # 40 dias anteriores com hrv=18, e hoje com hrv=25 (bem acima).
        for i in range(1, 41):
            d = (self.hoje - timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, hrv_avg) VALUES (?, 18.0)", (d,))
        self.conn.execute(
            "INSERT INTO daily (date, hrv_avg) VALUES (?, 25.0)", (self.hoje.isoformat(),)
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_valor_de_hoje_fica_fora_das_janelas(self):
        m = from_daily(self.conn, "hrv_avg", self.hoje.isoformat())
        self.assertEqual(m.value, 25.0)
        self.assertEqual(m.mean_30d, 18.0)   # 25.0 nao contaminou a media
        self.assertEqual(m.n_30d, 30)

    def test_dias_sem_dado_nao_entram_na_contagem(self):
        buraco = (self.hoje - timedelta(days=2)).isoformat()
        self.conn.execute("UPDATE daily SET hrv_avg = NULL WHERE date = ?", (buraco,))
        self.conn.commit()
        m = from_daily(self.conn, "hrv_avg", self.hoje.isoformat())
        self.assertEqual(m.n_7d, 6)          # 7 dias de janela, 6 com dado


class TestProcedencia(unittest.TestCase):
    """A baseline nao pode cruzar fontes de dado.

    No banco real o RHR da um degrau de ~6 bpm em 11/06/2026, quando o pipeline
    mudou. Misturar as duas fontes infla o desvio-padrao e produz um z-score que
    mede troca de origem, nao fisiologia."""

    def setUp(self):
        self.conn = _memory_db()
        self.hoje = date(2026, 6, 20)
        # 31 dias de fonte legada com RHR 70, depois 10 dias de garmin com 76.
        for i in range(41, 10, -1):
            d = (self.hoje - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO daily (date, rhr, source) VALUES (?, 70, 'legado')", (d,))
        for i in range(10, 0, -1):
            d = (self.hoje - timedelta(days=i)).isoformat()
            self.conn.execute(
                "INSERT INTO daily (date, rhr, source) VALUES (?, 76, 'garmin')", (d,))
        self.conn.execute(
            "INSERT INTO daily (date, rhr, source) VALUES (?, 76, 'garmin')",
            (self.hoje.isoformat(),))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_janela_fica_na_fonte_do_dia_alvo(self):
        m = from_daily(self.conn, "rhr", self.hoje.isoformat(), higher_is_better=False)
        self.assertEqual(m.mean_90d, 76.0)   # so os dias 'garmin'
        self.assertEqual(m.n_90d, 10)

    def test_misturar_fontes_inventa_desvio_que_nao_existe(self):
        misturado = from_daily(self.conn, "rhr", self.hoje.isoformat(),
                               higher_is_better=False, respect_source=False)
        separado = from_daily(self.conn, "rhr", self.hoje.isoformat(),
                              higher_is_better=False)
        # Com as duas fontes juntas o desvio vem do degrau, nao da fisiologia:
        # um valor perfeitamente normal passa a parecer um outlier.
        self.assertGreater(misturado.sd_90d, 2.0)
        self.assertEqual(separado.sd_90d, 0.0)
        self.assertGreater(abs(misturado.z or 0), 1.0)
        self.assertIsNone(separado.z)        # sem dispersao nao ha z, e correto


class TestSplitPorData(unittest.TestCase):
    """Quebra de medição por DATA (política split_by_date): a baseline de
    sleep_score não pode cruzar 2026-08-01.

    Mesma atualização da API do Garmin que mudou awakenings (19x) mudou a
    definição do sleep_score -- schema.sql já documentava a descontinuidade,
    mas a execução não respeitava (D1 da auditoria): a janela de 90 dias de um
    dia de setembro misturava scores de junho/julho (definição antiga) com os
    de agosto/setembro (definição nova), e o z-score mediria troca de
    definição, não fisiologia."""

    QUEBRA = "2026-08-01"

    def setUp(self):
        self.conn = _memory_db()
        self.depois = date(2026, 9, 1)
        # 60 dias ANTES da quebra com score 50; 60 dias DEPOIS com score 80.
        for i in range(1, 61):
            d = (date(2026, 8, 1) - timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, sleep_score) VALUES (?, 50)", (d,))
        for i in range(0, 60):
            d = (date(2026, 8, 1) + timedelta(days=i)).isoformat()
            self.conn.execute("INSERT INTO daily (date, sleep_score) VALUES (?, 80)", (d,))
        # 01/09 ja existe no range pos-quebra (80); o dia-alvo entra como 85.
        self.conn.execute("UPDATE daily SET sleep_score = 85 WHERE date = ?",
                          (self.depois.isoformat(),))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_janela_depois_da_quebra_nao_mistura_o_periodo_antigo(self):
        m = from_daily(self.conn, "sleep_score", self.depois.isoformat())
        self.assertEqual(m.value, 85.0)
        # Janela de 90 dias a partir de 01/09 comeca em 03/06; com o split, so
        # os dias do lado novo (01/08..31/08 = 31) entram -- nunca os 50 antigos.
        self.assertEqual(m.mean_90d, 80.0)
        self.assertEqual(m.n_90d, 31)
        # Janela pos-quebra sem dispersao (todos 80) -> sem z, e correto: nao
        # ha o que comparar dentro do lado novo ainda.
        self.assertIsNone(m.z)

    def test_janela_antes_da_quebra_fica_no_periodo_antigo(self):
        antes = date(2026, 7, 15)   # ja existe no range pre-quebra (50)
        self.conn.execute("UPDATE daily SET sleep_score = 55 WHERE date = ?",
                          (antes.isoformat(),))
        self.conn.commit()
        m = from_daily(self.conn, "sleep_score", antes.isoformat())
        self.assertEqual(m.value, 55.0)
        self.assertEqual(m.mean_90d, 50.0)   # nada de agosto entrou
        # 02/06..14/07 dentro da janela de 90 dias que termina em 15/07 = 43 dias
        self.assertEqual(m.n_90d, 43)

    def test_misturar_definicoes_infla_o_desvio_e_enviesa_a_media(self):
        # Sem o split (respect_date_split=False), a media vira a media das DUAS
        # definicoes e o desvio pega o degrau 50->80 -- um z que mede troca de
        # definicao, nao fisiologia.
        m = from_daily(self.conn, "sleep_score", self.depois.isoformat(),
                       respect_date_split=False)
        self.assertEqual(m.n_90d, 90)        # 30 dias pre + 60 pos dentro de 90
        self.assertGreater(m.sd_90d, 10.0)   # o degrau 50->80 infla o desvio
        self.assertLess(m.mean_90d, 70.0)    # puxada para baixo pelos 50 antigos

    def test_sem_dado_na_janela_do_lado_correto_nao_quebra(self):
        # Um dia logo apos a quebra, com poucos dias pos-quebra ainda: a janela
        # usa so o que existe no lado novo -- nunca cai para o lado antigo.
        logo_depois = date(2026, 8, 3)
        m = from_daily(self.conn, "sleep_score", logo_depois.isoformat())
        self.assertEqual(m.n_90d, 2)         # 08-01 e 08-02, so o lado novo
        self.assertEqual(m.mean_90d, 80.0)


class TestLimpezaDeFontes(unittest.TestCase):
    """Regras aplicadas no load: dedupe e marcacao de procedencia."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _act(self, activity_id, dia, source, *, duration=40.0, hr=150,
             kind="running", historical_only=None):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, duration_min, "
            "avg_hr, trimp, source, historical_only) "
            "VALUES (?, ?, ?, ?, ?, 50.0, ?, ?)",
            (activity_id, dia, kind, duration, hr, source, historical_only))

    def test_legada_e_descartada_somente_quando_parece_o_mesmo_treino(self):
        self._act(111, "2026-06-16", "garmin")
        self._act(-999, "2026-06-16", "legado", duration=40.1, hr=151)
        self._act(-997, "2026-06-16", "legado", duration=60, hr=130)
        self._act(-996, "2026-06-16", "legado", historical_only=1)
        self._act(-995, "2026-06-16", "legado", hr=170)
        self._act(-998, "2026-05-01", "legado")   # sem par, deve sobreviver
        self.conn.commit()

        removidas = sheets._drop_superseded_activities(self.conn)
        self.assertEqual(removidas, 1)

        restantes = {r[0] for r in self.conn.execute("SELECT activity_id FROM activities")}
        self.assertEqual(restantes, {111, -995, -996, -997, -998})

    def test_tipo_renomeado_na_migracao_ainda_tem_par_por_duracao_e_fc(self):
        self._act(112, "2026-06-30", "garmin", duration=40.8, hr=115,
                  kind="pilates")
        self._act(-994, "2026-06-30", "legado", duration=40.8, hr=115,
                  kind="training")
        self.assertEqual(sheets._drop_superseded_activities(self.conn), 1)

    def test_marcacao_de_daily_vem_da_evidencia_no_dado(self):
        # Um dia so e 'garmin' se traz algum campo que so o sync atual coleta.
        self.conn.execute("INSERT INTO daily (date, rhr) VALUES ('2026-03-01', 70)")
        self.conn.execute(
            "INSERT INTO daily (date, rhr, hrv_avg) VALUES ('2026-07-01', 76, 18.5)")
        self.conn.commit()

        sheets._tag_daily_source(self.conn)
        fontes = dict(self.conn.execute("SELECT date, source FROM daily").fetchall())
        self.assertEqual(fontes["2026-03-01"], "legado")
        self.assertEqual(fontes["2026-07-01"], "garmin")


class TestAnalysis(unittest.TestCase):
    """Eficiencia aerobica e monotonia de Foster -- so as funcoes puras."""

    def test_regressao_recupera_a_reta_que_gerou_os_dados(self):
        # FC = 100 + 10*pace, exata. A previsao em 6.5 tem que dar 165.
        paces = [5.5, 6.0, 6.5, 7.0, 7.5, 8.0]
        hrs = [100 + 10 * p for p in paces]
        previsto, erro, inclinacao = analysis.fit_hr_at_pace(paces, hrs, 6.5)
        self.assertAlmostEqual(previsto, 165.0, places=1)
        self.assertAlmostEqual(inclinacao, 10.0, places=1)
        self.assertAlmostEqual(erro, 0.0, places=2)   # sem residuo, sem incerteza

    def test_ruido_vira_erro_padrao_maior(self):
        paces = [5.5, 6.0, 6.5, 7.0, 7.5, 8.0]
        limpo = analysis.fit_hr_at_pace(paces, [100 + 10 * p for p in paces], 6.5)
        sujo = analysis.fit_hr_at_pace(paces, [100, 175, 140, 190, 150, 200], 6.5)
        self.assertGreater(sujo[1], limpo[1])

    def test_poucas_corridas_nao_geram_regressao(self):
        self.assertIsNone(analysis.fit_hr_at_pace([6.0, 6.5], [160.0, 165.0], 6.2))

    def test_pace_constante_nao_gera_regressao(self):
        # Sem variacao em x nao da para estimar inclinacao nenhuma.
        self.assertIsNone(analysis.fit_hr_at_pace([6.5] * 8, [165.0] * 8, 6.5))

    def test_veredito_respeita_a_incerteza(self):
        def bloco(rotulo, hr, erro):
            return analysis.Efficiency(rotulo=rotulo, n=19, pace_ref=6.6, valor_ref=hr,
                                       erro_padrao=erro, inclinacao=-9.8,
                                       d0="2026-06-11", d1="2026-07-22")

        # Diferenca menor que a soma dos erros nao e mudanca.
        ruido = analysis.efficiency_verdict([bloco("a", 168.0, 1.0), bloco("b", 167.5, 1.0)])
        self.assertIn("sem mudanca detectavel", ruido)

        # FC menor no mesmo pace = economia melhor.
        melhora = analysis.efficiency_verdict([bloco("a", 172.0, 0.5), bloco("b", 165.0, 0.5)])
        self.assertIn("economia melhorou", melhora)

        piora = analysis.efficiency_verdict([bloco("a", 165.0, 0.5), bloco("b", 172.0, 0.5)])
        self.assertIn("custo subiu", piora)

    def test_monotonia_alta_quando_todos_os_dias_sao_iguais(self):
        # Carga quase constante -> monotonia alta -> risco.
        parelha = analysis.foster_monotony([50, 50, 50, 50, 52, 48, 50])
        variada = analysis.foster_monotony([0, 120, 0, 60, 0, 100, 0])
        self.assertGreater(parelha[0], 2.0)
        self.assertLess(variada[0], 2.0)

    def test_strain_e_carga_total_vezes_monotonia(self):
        cargas = [0, 100, 0, 50, 0, 80, 0]
        monotonia, strain = analysis.foster_monotony(cargas)
        self.assertEqual(strain, round(sum(cargas) * monotonia))

    def test_semana_sem_variacao_nao_tem_monotonia_definida(self):
        # Divisao por zero: todos os dias identicos. Devolve None, nao infinito.
        self.assertIsNone(analysis.foster_monotony([50, 50, 50, 50, 50, 50, 50]))


class TestIntervalEfficiency(unittest.TestCase):
    """`interval_efficiency` usa cada tiro como ponto, ao contrario de
    `aerobic_efficiency` que usa a media da corrida inteira."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _add_session(self, activity_id, dia, reps, source="garmin"):
        splits = {"n_reps": len(reps), "reps": reps}
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, splits, source) "
            "VALUES (?, ?, 'running', ?, ?)",
            (activity_id, dia, json.dumps(splits), source),
        )
        self.conn.commit()

    def test_corrida_continua_de_1_rep_e_ignorada(self):
        # Sessao com so 1 "rep" e corrida continua inteira, nao intervalo real.
        self._add_session(1, "2026-06-01", [{"pace_min_km": 6.5, "avg_hr": 160}])
        self.assertEqual(analysis._interval_reps(self.conn, "2026-06-01"), [])

    def test_fonte_legada_e_descartada(self):
        reps = [{"pace_min_km": 5.0 + i * 0.1, "avg_hr": 170 + i} for i in range(3)]
        self._add_session(1, "2026-06-01", reps, source="legado")
        self.assertEqual(analysis._interval_reps(self.conn, "2026-06-01"), [])

    def test_poucos_tiros_nao_geram_regressao(self):
        reps = [{"pace_min_km": 5.0 + i * 0.1, "avg_hr": 170 + i} for i in range(3)]
        self._add_session(1, "2026-06-01", reps)
        self.assertEqual(analysis.interval_efficiency(self.conn, "2026-06-01"), [])

    def test_usa_tiro_a_tiro_nao_a_media_da_sessao(self):
        # FC = 100 + 10*pace, exata. 4 sessoes de 3 tiros = 12 pontos (o minimo).
        dias = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]
        for i, dia in enumerate(dias):
            reps = [{"pace_min_km": p, "avg_hr": 100 + 10 * p}
                    for p in (5.0 + i * 0.3, 5.3 + i * 0.3, 5.6 + i * 0.3)]
            self._add_session(i + 1, dia, reps)

        blocos = analysis.interval_efficiency(self.conn, "2026-06-22")
        self.assertEqual(len(blocos), 2)
        self.assertEqual(blocos[0].n, 6)
        self.assertEqual(blocos[1].n, 6)
        # A reta e exata -- previsao no pace de referencia bate com a formula
        # que gerou os dados, tiro a tiro (nao com a media de cada sessao).
        for b in blocos:
            self.assertAlmostEqual(b.valor_ref, 100 + 10 * b.pace_ref, places=0)

    def test_reps_sem_dinamica_de_corrida_ficam_none(self):
        # Dispositivo sem pod de dinamica de corrida: so pace e FC existem.
        reps = [{"pace_min_km": 5.0 + i * 0.1, "avg_hr": 170 + i} for i in range(3)]
        self._add_session(1, "2026-06-01", reps)
        tiros = analysis._interval_reps(self.conn, "2026-06-01")
        self.assertEqual(len(tiros), 3)
        for t in tiros:
            self.assertIsNone(t["cadencia"])
            self.assertIsNone(t["gct"])
            self.assertIsNone(t["oscilacao"])


class TestIntervalMechanics(unittest.TestCase):
    """`interval_mechanics` -- economia de corrida pelo lado biomecanico,
    tiro a tiro, mesma base de `interval_efficiency`."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _add_session(self, activity_id, dia, reps, source="garmin"):
        splits = {"n_reps": len(reps), "reps": reps}
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, splits, source) "
            "VALUES (?, ?, 'running', ?, ?)",
            (activity_id, dia, json.dumps(splits), source),
        )
        self.conn.commit()

    def test_usa_tiro_a_tiro_por_campo_mecanico(self):
        # tempo de contato = 300 - 5*pace, exato. 4 sessoes de 3 tiros = 12 pontos.
        dias = ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]
        for i, dia in enumerate(dias):
            reps = [{"pace_min_km": p, "avg_hr": 170,
                     "ground_contact_time": 300 - 5 * p}
                    for p in (5.0 + i * 0.3, 5.3 + i * 0.3, 5.6 + i * 0.3)]
            self._add_session(i + 1, dia, reps)

        blocos = analysis.interval_mechanics(self.conn, "2026-06-22", "gct")
        self.assertEqual(len(blocos), 2)
        for b in blocos:
            self.assertAlmostEqual(b.valor_ref, 300 - 5 * b.pace_ref, places=0)

    def test_campo_ausente_em_todos_os_tiros_nao_gera_regressao(self):
        reps = [{"pace_min_km": 5.0 + i * 0.1, "avg_hr": 170} for i in range(20)]
        self._add_session(1, "2026-06-01", reps)
        self.assertEqual(analysis.interval_mechanics(self.conn, "2026-06-01", "cadencia"), [])

    def test_veredito_direcao_padrao_menor_e_melhor(self):
        def bloco(rotulo, valor, erro):
            return analysis.Efficiency(rotulo=rotulo, n=12, pace_ref=5.3, valor_ref=valor,
                                       erro_padrao=erro, inclinacao=-2.0,
                                       d0="2026-06-01", d1="2026-08-01")

        # GCT caiu -- sinal de economia melhorando (menor_e_melhor=True).
        melhora = analysis.mechanical_verdict(
            [bloco("a", 255.0, 1.0), bloco("b", 248.0, 1.0)], "tempo de contato", "ms",
            menor_e_melhor=True)
        self.assertIn("caiu", melhora)
        self.assertIn("sinal de economia melhorando", melhora)

        # GCT subiu -- sinal de fadiga tecnica, nao um veredito fechado
        # ("piora"): a janela e curta e pode ser fadiga temporaria de carga.
        piora = analysis.mechanical_verdict(
            [bloco("a", 248.0, 1.0), bloco("b", 255.0, 1.0)], "tempo de contato", "ms",
            menor_e_melhor=True)
        self.assertIn("subiu", piora)
        self.assertIn("sinal de fadiga tecnica", piora)

    def test_veredito_direcao_invertida_para_cadencia(self):
        def bloco(rotulo, valor, erro):
            return analysis.Efficiency(rotulo=rotulo, n=12, pace_ref=5.3, valor_ref=valor,
                                       erro_padrao=erro, inclinacao=-2.0,
                                       d0="2026-06-01", d1="2026-08-01")

        # Cadencia caiu -- e' sinal de fadiga tecnica quando menor_e_melhor=False.
        piora = analysis.mechanical_verdict(
            [bloco("a", 176.0, 0.5), bloco("b", 172.0, 0.5)], "cadencia", "spm",
            menor_e_melhor=False)
        self.assertIn("caiu", piora)
        self.assertIn("sinal de fadiga tecnica", piora)


class TestAerobicEfficiency(unittest.TestCase):
    """`aerobic_efficiency` usa a atividade inteira como ponto (corrida
    continua: longao, rodagem), ao contrario de `interval_efficiency`."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _add_run(self, activity_id, dia, pace, hr, source="garmin", distance_km=10.0):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, distance_km, duration_min, "
            "avg_hr, source) VALUES (?, ?, 'running', ?, ?, ?, ?)",
            (activity_id, dia, distance_km, pace * distance_km, hr, source),
        )
        self.conn.commit()

    def test_usa_a_atividade_inteira_como_ponto(self):
        # FC = 100 + 10*pace, exata. 12 corridas (o minimo).
        base = date(2026, 6, 1)
        for i in range(12):
            dia = (base + timedelta(days=i * 3)).isoformat()
            pace = 6.0 + i * 0.05
            self._add_run(i + 1, dia, pace, 100 + 10 * pace)

        blocos = analysis.aerobic_efficiency(self.conn, (base + timedelta(days=40)).isoformat())
        self.assertEqual(len(blocos), 2)
        for b in blocos:
            self.assertAlmostEqual(b.valor_ref, 100 + 10 * b.pace_ref, places=0)

    def test_fonte_legada_e_descartada(self):
        for i in range(12):
            dia = (date(2026, 6, 1) + timedelta(days=i)).isoformat()
            self._add_run(i + 1, dia, 6.5, 165, source="legado")
        self.assertEqual(analysis.aerobic_efficiency(self.conn, "2026-06-20"), [])


class TestContinuousMechanics(unittest.TestCase):
    """`continuous_mechanics` -- economia mecanica em corrida continua
    (longao, rodagem), usando a atividade inteira, nao o tiro de intervalado."""

    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _add_run(self, activity_id, dia, pace, cadencia=None, gct=None, oscilacao=None,
                 distance_km=10.0, source="garmin"):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, distance_km, duration_min, "
            "avg_cadence, ground_contact_time, vertical_oscillation, source) "
            "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)",
            (activity_id, dia, distance_km, pace * distance_km, cadencia, gct, oscilacao, source),
        )
        self.conn.commit()

    def test_usa_atividade_inteira_para_indicador_mecanico(self):
        # tempo de contato = 300 - 5*pace, exato. 12 corridas (o minimo).
        base = date(2026, 6, 1)
        for i in range(12):
            dia = (base + timedelta(days=i * 3)).isoformat()
            pace = 6.0 + i * 0.05
            self._add_run(i + 1, dia, pace, gct=300 - 5 * pace)

        blocos = analysis.continuous_mechanics(
            self.conn, (base + timedelta(days=40)).isoformat(), "gct")
        self.assertEqual(len(blocos), 2)
        for b in blocos:
            self.assertAlmostEqual(b.valor_ref, 300 - 5 * b.pace_ref, places=0)

    def test_corrida_muito_curta_e_descartada(self):
        # Mesmo filtro de _runs (distance_km > 1) -- evita diluir com testes curtos.
        self._add_run(1, "2026-06-01", 6.5, cadencia=170, distance_km=0.5)
        self.assertEqual(analysis._runs(self.conn, "2026-06-01"), [])


class TestHistoricalWeeklyVolume(unittest.TestCase):
    def test_conta_apenas_legado_reconciliado_e_garmin_incluindo_esteira(self):
        conn = _memory_db()
        self.addCleanup(conn.close)
        rows = [
            (-1, "running", 5.0, "legado", "strava_export"),
            (-2, "running", 999.0, "legado", None),
            (3, "treadmill_running", 3.0, "garmin", None),
        ]
        for aid, kind, km, source, history in rows:
            conn.execute(
                "INSERT INTO activities (activity_id, date, type, distance_km, "
                "source, historical_source) VALUES (?, '2026-03-01', ?, ?, ?, ?)",
                (aid, kind, km, source, history))
        result = analysis.weekly_load(conn, "2026-03-01", semanas=1)[0]
        self.assertEqual(result["km"], 8.0)
        self.assertEqual(result["sessoes"], 2)


class TestTiposDeAtividade(unittest.TestCase):
    """O dia pode trazer qualquer coisa: corrida, pilates, forca, respiracao,
    meditacao, nada. Nenhum tipo pode ser assumido e nenhum pode sumir."""

    def test_atividade_sem_distancia_ainda_e_reconhecida(self):
        # Pilates vem com distance_km = 0.0 e breathwork com None. 0.0 e falsy
        # em Python -- foi assim que a duracao dessas sessoes sumia do relatorio.
        pilates = {"type": "pilates", "duration_min": 60.0, "distance_km": 0.0,
                   "avg_hr": 118, "trimp": 35.0}
        respiracao = {"type": "breathwork", "duration_min": 12.0, "distance_km": None,
                      "avg_hr": 70, "trimp": 2.0}
        self.assertEqual(report_sections.classify_workout_demand(pilates), "BAIXA")
        self.assertEqual(report_sections.classify_workout_demand(respiracao), "BAIXA")
        self.assertIn("pilates", report_sections.resumo_atividades([pilates]))
        self.assertIn("breathwork", report_sections.resumo_atividades([respiracao]))

    def test_tipo_desconhecido_nao_quebra(self):
        # Se amanha aparecer um tipo novo no Garmin, o relatorio segue.
        novo = {"type": "meditation", "duration_min": 20.0, "trimp": 1.0}
        sem_tipo = {"type": None, "duration_min": 30.0, "trimp": 10.0}
        self.assertIn("meditation", report_sections.resumo_atividades([novo]))
        self.assertIn("atividade", report_sections.resumo_atividades([sem_tipo]))

    def test_dia_com_mais_de_um_treino_mostra_todos(self):
        corrida = {"type": "running", "trimp": 117.0}
        pilates = {"type": "pilates", "trimp": 35.0}
        linha = report_sections.resumo_atividades([corrida, pilates])
        self.assertIn("running", linha)
        self.assertIn("pilates", linha)

    def test_underscore_do_garmin_vira_espaco(self):
        forca = {"type": "strength_training", "trimp": 72.0}
        self.assertIn("strength training", report_sections.resumo_atividades([forca]))

    def test_dia_sem_treino(self):
        self.assertEqual(report_sections.resumo_atividades([]), "")

    def test_exigencia_sem_trimp_nao_quebra(self):
        self.assertEqual(report_sections.classify_workout_demand({"type": "walking"}), "BAIXA")

    def test_zona_de_fc_fora_das_faixas(self):
        faixas = [{"zone": "Z1", "name": "Recuperacao", "hr_min": 118, "hr_max": 155},
                  {"zone": "Z5", "name": "VO2max", "hr_min": 182, "hr_max": 192}]
        self.assertIn("Z1", report_sections.zone_of(130, faixas))
        self.assertIn("abaixo de Z1", report_sections.zone_of(70, faixas))   # FC de repouso
        self.assertEqual(report_sections.zone_of(None, faixas), "")
        self.assertEqual(report_sections.zone_of(150, []), "")


class TestSparkline(unittest.TestCase):
    """report_sections.sparkline: escala min-max linear em blocos Unicode. Pista
    visual, entao os testes checam forma/monotonicidade, nao blocos exatos."""

    def test_lista_vazia_devolve_string_vazia(self):
        self.assertEqual(report_sections.sparkline([]), "")

    def test_um_ponto_devolve_um_bloco(self):
        self.assertEqual(len(report_sections.sparkline([42.0])), 1)

    def test_valores_iguais_devolve_bloco_do_meio_sem_erro(self):
        resultado = report_sections.sparkline([5.0, 5.0, 5.0])
        self.assertEqual(len(resultado), 3)
        self.assertEqual(len(set(resultado)), 1)  # os 3 blocos sao o mesmo caractere

    def test_minimo_e_maximo_usam_os_blocos_extremos(self):
        resultado = report_sections.sparkline([1.0, 5.0, 10.0])
        self.assertEqual(resultado[0], "▁")   # valor minimo -> bloco mais baixo
        self.assertEqual(resultado[-1], "█")  # valor maximo -> bloco mais alto

    def test_serie_crescente_da_blocos_nao_decrescentes(self):
        blocos = "▁▂▃▄▅▆▇█"
        resultado = report_sections.sparkline([1.0, 2.0, 3.0, 4.0, 5.0])
        indices = [blocos.index(c) for c in resultado]
        self.assertEqual(indices, sorted(indices))

    def test_preserva_o_numero_de_pontos(self):
        valores = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
        self.assertEqual(len(report_sections.sparkline(valores)), len(valores))


class TestRecoveryStatus(unittest.TestCase):
    """recovery_status: bucket ok/atencao/alerta/sem_dado do indice, mesmas
    fronteiras que recovery_label (6.0 e 3.5) -- usado por today.py (colorir
    o indice) e calibration.py (comparar contra o Metric.status do Garmin)."""

    def test_sem_dado_quando_indice_none(self):
        self.assertEqual(recovery_status(None), "sem_dado")

    def test_ok_acima_ou_igual_a_6(self):
        self.assertEqual(recovery_status(6.0), "ok")
        self.assertEqual(recovery_status(8.5), "ok")

    def test_atencao_entre_3_5_e_6(self):
        self.assertEqual(recovery_status(3.5), "atencao")
        self.assertEqual(recovery_status(5.9), "atencao")

    def test_alerta_abaixo_de_3_5(self):
        self.assertEqual(recovery_status(0.0), "alerta")
        self.assertEqual(recovery_status(3.4), "alerta")


class TestDetectPatterns(unittest.TestCase):
    """today.detect_patterns: quantos sinais individuais estao fora do padrao
    no mesmo dia. C3 (VISTORIA_2026-09-04 V2): o texto fala em SINAIS, nunca
    em dominios -- dominio e' a agregacao larga de CARGA/RECUPERACAO/SONO que
    so farol.domain.state classifica (regra 8 = state.convergence). Dois campos do
    mesmo dominio (HRV e RHR, RECUPERACAO) contam como 2 sinais, nao como 2
    dominios."""

    def test_um_sinal_isolado_nao_vira_padrao(self):
        metricas = {"hrv_avg": _m("alerta")}
        self.assertEqual(today.detect_patterns(metricas), [])

    def test_dois_sinais_de_mesmo_dominio_diz_sinais_nao_dominios(self):
        # HRV e RHR sao os dois do dominio RECUPERACAO -- chamar isto de
        # "2 dominios" seria dupla contagem do mesmo dominio (V2).
        metricas = {"hrv_avg": _m("alerta"), "rhr": _m("atencao")}
        padroes = today.detect_patterns(metricas)
        texto = " ".join(padroes)
        self.assertIn("2 sinais simultaneamente fora do padrao", texto)
        self.assertIn("HRV", texto)
        self.assertIn("FC repouso", texto)
        self.assertNotIn("dominio", texto)

    def test_cruza_dominios_diferentes_e_conta_sinais(self):
        # sono (sleep_score) + recuperacao (hrv) + carga tem sinal proprio:
        # tres sinais em dominios distintos -> "3 sinais".
        metricas = {
            "sleep_score": _m("atencao"), "hrv_avg": _m("alerta"),
            "night_hr_avg": _m("atencao"), "total_sleep_minutes": _m("ok"),
        }
        padroes = today.detect_patterns(metricas)
        self.assertTrue(any("3 sinais" in p for p in padroes))

    def test_detalhe_por_alerta_continua_saindo(self):
        metricas = {"hrv_avg": _m("alerta", z=-2.4), "rhr": _m("atencao", z=-1.2)}
        padroes = today.detect_patterns(metricas)
        self.assertIn("2.4 desvios do seu normal", " ".join(padroes))



def _m(status="ok", has_data=True, higher_is_better=True, value=0.0, z=-1.0):
    """Metric sintetica com controle direto sobre status/has_data -- para
    testar today.signature_doenca sem depender de build_metric()."""
    return Metric(
        name="x", value=(value if has_data else None), mean_7d=None, mean_30d=None,
        mean_90d=None, sd_90d=None, z=(z if has_data else None), trend="indefinido",
        status=(status if has_data else "sem_dado"), confidence="alta",
        n_7d=0, n_30d=0, n_90d=20, higher_is_better=higher_is_better,
    )


class TestSignatureDoenca(unittest.TestCase):
    """today.signature_doenca: distingue 2+ sinais agudos fora do padrao SEM
    carga de treino recente elevada (TSB ok) de fadiga de treino (TSB
    carregado explica a mesma convergencia)."""

    def test_menos_de_3_sinais_devolve_none(self):
        # 2 marcados nao basta: 2-de-5 disparava em 17% dos dias reais,
        # frequencia incompativel com "resposta imunologica" ser raro.
        metricas = {
            "hrv_avg": _m("alerta"), "rhr": _m("atencao"),
            "tsb": _m("ok", value=2.0),
        }
        self.assertIsNone(today.signature_doenca(metricas))

    def test_3_sinais_com_tsb_ok_detecta_padrao(self):
        metricas = {
            "hrv_avg": _m("alerta"), "rhr": _m("atencao"), "night_hr_avg": _m("atencao"),
            "tsb": _m("ok", value=3.5),
        }
        resultado = today.signature_doenca(metricas)
        self.assertIsNotNone(resultado)
        self.assertIn("compativel com", resultado)
        self.assertIn("nao e' diagnostico", resultado)
        self.assertNotIn("voce esta doente", resultado.lower())

    def test_body_battery_min_nao_conta_como_sinal_de_doenca(self):
        # C2 (VISTORIA_2026-09-04 V3): body_battery_min marcava 94% dos dias
        # recentes como atencao/alerta (piso volatil do Garmin) e fazia o
        # 3-de-6 disparar em ~20% dos dias. Removido de _SINAIS_DOENCA: sozinho
        # com mais 1 sinal agudo real nao pode fechar a assinatura.
        metricas = {
            "body_battery_min": _m("alerta"), "hrv_avg": _m("atencao"),
            "tsb": _m("ok", value=3.5),
        }
        self.assertIsNone(today.signature_doenca(metricas))

        # e com os 3 sinais agudos reais continua detectando, mesmo com
        # body_battery_min ok (deixou de ser necessario/contar)
        metricas = {
            "body_battery_min": _m("ok"), "hrv_avg": _m("alerta"),
            "rhr": _m("atencao"), "night_hr_avg": _m("atencao"),
            "tsb": _m("ok", value=3.5),
        }
        self.assertIsNotNone(today.signature_doenca(metricas))

    def test_3_sinais_com_tsb_carregado_devolve_none(self):
        # TSB atencao/alerta = fadiga de treino ja explica a convergencia --
        # nao e' o padrao que este sinal descreve.
        metricas = {
            "hrv_avg": _m("alerta"), "rhr": _m("atencao"), "night_hr_avg": _m("atencao"),
            "tsb": _m("alerta", value=-15.0),
        }
        self.assertIsNone(today.signature_doenca(metricas))

    def test_3_sinais_sem_tsb_devolve_none(self):
        # Sem TSB nao da pra descartar fadiga de treino -- nunca "compativel
        # com doenca" por omissao de dado.
        metricas = {
            "hrv_avg": _m("alerta"), "rhr": _m("atencao"), "night_hr_avg": _m("atencao"),
        }
        self.assertIsNone(today.signature_doenca(metricas))

        metricas_tsb_sem_dado = {
            "hrv_avg": _m("alerta"), "rhr": _m("atencao"), "night_hr_avg": _m("atencao"),
            "tsb": _m(has_data=False),
        }
        self.assertIsNone(today.signature_doenca(metricas_tsb_sem_dado))

    def test_sono_e_carga_nao_contam_como_sinal_de_doenca(self):
        # sleep_score/total_sleep_minutes/tsb tem interpretacao propria --
        # nao devem contar para a convergencia de sinais agudos do corpo.
        metricas = {
            "sleep_score": _m("alerta"), "total_sleep_minutes": _m("atencao"),
            "hrv_avg": _m("atencao"),
            "tsb": _m("ok", value=3.5),
        }
        self.assertIsNone(today.signature_doenca(metricas))


@skip_unless_real_db()
class TestBuildDailyState(unittest.TestCase):
    """Test build_daily_state() function: populating farol_daily_state table.

    Le data/farol.db real (clonado em memoria por _clone_real_db_em_memoria)
    -- nenhum metodo desta classe pode escrever no arquivo. setUpClass/
    tearDownClass conferem isso por hash: se algum teste aqui voltar a abrir
    o arquivo real com db.connect() e escrever nele, a classe falha."""

    @classmethod
    def setUpClass(cls):
        cls._hash_antes = _hash_arquivo(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        hash_depois = _hash_arquivo(DB_PATH)
        if hash_depois != cls._hash_antes:
            raise AssertionError(
                "data/farol.db mudou durante TestBuildDailyState -- esta "
                "classe deve escrever so no clone em memoria "
                "(_clone_real_db_em_memoria), nunca no arquivo real."
            )

    def test_golden_sample_2026_09_02(self):
        """Verify build_daily_state produces correct values for 2026-09-02."""
        conn = _clone_real_db_em_memoria()
        try:
            build_daily_state(conn, "2026-09-02")

            row = conn.execute(
                "SELECT * FROM farol_daily_state WHERE date = ?",
                ("2026-09-02",)
            ).fetchone()
            self.assertIsNotNone(row)

            # CARGA domain
            self.assertIsNotNone(row['trimp_7d'])
            self.assertIsNotNone(row['trimp_28d'])
            self.assertAlmostEqual(row['ctl'], 66.59, places=1)
            self.assertAlmostEqual(row['atl'], 79.78, places=1)
            self.assertAlmostEqual(row['tsb'], -13.19, places=1)
            self.assertIsNotNone(row['atl_ctl_ratio'])

            # RECUPERAÇÃO domain
            self.assertEqual(row['hrv_avg'], 18.0)
            self.assertEqual(row['rhr'], 77)
            self.assertEqual(row['night_hr_avg'], 83)
            self.assertEqual(row['stress_avg'], 44)  # Garmin recalculou no resync de 2026-09-03 (era 39)
            self.assertEqual(row['body_battery_max'], 33)
            self.assertEqual(row['body_battery_min'], 5)
            self.assertEqual(row['respiration_avg'], 17.0)
            self.assertEqual(row['spo2_avg'], 97.0)
            self.assertEqual(row['spo2_min'], 89.0)

            # SONO domain
            self.assertEqual(row['sleep_total_min'], 443.0)
            self.assertEqual(row['awake_minutes'], 71.0)
            self.assertEqual(row['deep_sleep_min'], 92.0)
            self.assertEqual(row['rem_sleep_min'], 69.0)
            self.assertEqual(row['sleep_score'], 52)
            self.assertAlmostEqual(row['sleep_regularity_min'], 85.2, places=1)

            # PERFORMANCE: preenchido desde o sync real de 2026-09-03 (farol.pipeline.ingest
            # busca os splits; farol.pipeline.load sozinho nao busca -- ver README).
            self.assertEqual(row['output_preserved'], 'yes')
            self.assertEqual(row['deterioration_detected'], 'yes')

            # OBSERVAÇÃO: lista fixa de descontinuidades conhecidas
            breakpoints = json.loads(row['measurement_breakpoints'])
            campos = {b['field'] for b in breakpoints['breakpoints']}
            self.assertEqual(campos, {'rhr', 'awakenings', 'sleep_score'})
        finally:
            conn.close()

    def test_idempotent(self):
        """Verify calling build_daily_state twice produces identical result."""
        conn = _clone_real_db_em_memoria()
        try:
            build_daily_state(conn, "2026-09-02")
            row1 = conn.execute(
                "SELECT * FROM farol_daily_state WHERE date = ?",
                ("2026-09-02",)
            ).fetchone()

            build_daily_state(conn, "2026-09-02")
            row2 = conn.execute(
                "SELECT * FROM farol_daily_state WHERE date = ?",
                ("2026-09-02",)
            ).fetchone()

            dict1 = dict(row1)
            dict2 = dict(row2)
            self.assertEqual(dict1, dict2)
        finally:
            conn.close()

    def test_skip_no_daily(self):
        """Verify build_daily_state silently skips if no daily record exists."""
        conn = _clone_real_db_em_memoria()
        try:
            build_daily_state(conn, "1970-01-01")

            row = conn.execute(
                "SELECT * FROM farol_daily_state WHERE date = ?",
                ("1970-01-01",)
            ).fetchone()
            self.assertIsNone(row)
        finally:
            conn.close()


class TestPerformanceDomain(unittest.TestCase):
    """Domínio PERFORMANCE (MODEL_V1.0.md): output_preserved e
    deterioration_detected, calculados a partir de activities.splits."""

    def setUp(self):
        self.conn = _memory_db()
        self.today = date(2026, 9, 2)

    def tearDown(self):
        self.conn.close()

    def _insert_daily(self, d: str):
        self.conn.execute("INSERT INTO daily (date, rhr) VALUES (?, 70)", (d,))

    def _insert_session(self, d: str, avg_pace: float, reps: list[dict], activity_id: int = None):
        splits = {
            "n_reps": len(reps),
            "reps": reps,
            "avg_pace_min_km": avg_pace,
        }
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, splits) VALUES (?, ?, 'running', ?)",
            (activity_id or hash(d) % 1_000_000_000, d, json.dumps(splits)),
        )

    def _rep(self, pace=4.5, hr=165, cadence=180, gct=250, osc=8.5):
        return {
            "pace_min_km": pace, "avg_hr": hr, "avg_cadence": cadence,
            "ground_contact_time": gct, "vertical_oscillation": osc,
        }

    def test_sem_treino_intervalado_e_null(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved, deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertIsNone(row['output_preserved'])
        self.assertIsNone(row['deterioration_detected'])

    def test_output_preserved_indeterminate_sem_baseline(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        self._insert_session(d, 4.50, [self._rep(), self._rep()])
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['output_preserved'], "indeterminate")

    def test_output_preserved_yes_dentro_do_limiar(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        for i in range(1, 4):
            past = (self.today - timedelta(days=i * 3)).isoformat()
            self._insert_session(past, 4.50, [self._rep(), self._rep()], activity_id=2000 + i)
        self._insert_session(d, 4.60, [self._rep(), self._rep()])  # 2.2% mais lento
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['output_preserved'], "yes")

    def test_output_preserved_no_queda_acima_de_5_por_cento(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        for i in range(1, 4):
            past = (self.today - timedelta(days=i * 3)).isoformat()
            self._insert_session(past, 4.50, [self._rep(), self._rep()], activity_id=3000 + i)
        self._insert_session(d, 4.80, [self._rep(), self._rep()])  # 6.7% mais lento
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['output_preserved'], "no")

    def test_corrida_continua_de_1_rep_nao_e_sessao_intervalada(self):
        # C1: sessao de 1 rep e' corrida continua tipada como tiro unico por
        # farol.pipeline.splits -- nao e' intervalo real. Nem output_preserved nem
        # deterioration_detected devem opinar (None), em vez de comparar o
        # pace de uma corrida continua contra o padrao de tiros (falso
        # positivo de VISTORIA_2026-09-04 V4). Mesma regra de `_interval_reps`.
        d = self.today.isoformat()
        self._insert_daily(d)
        for i in range(1, 4):  # baseline intervalada real existente
            past = (self.today - timedelta(days=i * 3)).isoformat()
            self._insert_session(past, 4.50, [self._rep(), self._rep()], activity_id=4000 + i)
        self._insert_session(d, 6.28, [self._rep()])  # continua, 1 rep, bem mais lenta
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved, deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertIsNone(row['output_preserved'])
        self.assertIsNone(row['deterioration_detected'])

    def test_deterioration_indeterminate_com_um_tiro_so_nao_ocorre(self):
        # Apos C1, `_work_session` exige >= 2 reps -- 1 rep so nao gera estado
        # PERFORMANCE nenhum (None), nunca 'indeterminate' de sessao invalida.
        d = self.today.isoformat()
        self._insert_daily(d)
        self._insert_session(d, 4.50, [self._rep()])
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT output_preserved, deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertIsNone(row['output_preserved'])
        self.assertIsNone(row['deterioration_detected'])

    def test_deterioration_no_quando_estavel(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        reps = [self._rep(pace=4.50, hr=163, cadence=180, gct=250, osc=8.5),
                self._rep(pace=4.52, hr=165, cadence=179, gct=251, osc=8.6)]
        self._insert_session(d, 4.51, reps)
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['deterioration_detected'], "no")

    def test_deterioration_yes_com_deriva_de_fc(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        reps = [self._rep(pace=4.50, hr=163, cadence=180, gct=250, osc=8.5),
                self._rep(pace=4.50, hr=172, cadence=180, gct=250, osc=8.5)]  # +9 bpm
        self._insert_session(d, 4.50, reps)
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['deterioration_detected'], "yes")

    def test_deterioration_yes_com_queda_de_cadencia(self):
        d = self.today.isoformat()
        self._insert_daily(d)
        reps = [self._rep(pace=4.50, hr=163, cadence=182, gct=250, osc=8.5),
                self._rep(pace=4.50, hr=163, cadence=177, gct=250, osc=8.5)]  # -5 spm
        self._insert_session(d, 4.50, reps)
        self.conn.commit()
        build_daily_state(self.conn, d)
        row = self.conn.execute(
            "SELECT deterioration_detected FROM farol_daily_state WHERE date = ?", (d,)
        ).fetchone()
        self.assertEqual(row['deterioration_detected'], "yes")


class TestObservationDomain(unittest.TestCase):
    """Domínio OBSERVAÇÃO (MODEL_V1.0.md): measurement_breakpoints é uma
    lista fixa e global, igual para qualquer data com farol_daily_state."""

    def test_breakpoints_presentes_e_estaveis_entre_datas(self):
        conn = _memory_db()
        try:
            for d in ("2026-01-15", "2026-09-02"):
                conn.execute("INSERT INTO daily (date, rhr) VALUES (?, 70)", (d,))
            conn.commit()
            build_daily_state(conn, "2026-01-15")
            build_daily_state(conn, "2026-09-02")

            rows = conn.execute(
                "SELECT date, measurement_breakpoints FROM farol_daily_state ORDER BY date"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['measurement_breakpoints'], rows[1]['measurement_breakpoints'])

            parsed = json.loads(rows[0]['measurement_breakpoints'])
            por_campo = {b['field']: b for b in parsed['breakpoints']}
            self.assertEqual(por_campo['rhr']['detected_on'], "2026-06-11")
            self.assertEqual(por_campo['awakenings']['detected_on'], "2026-08-01")
            self.assertEqual(por_campo['sleep_score']['detected_on'], "2026-08-01")
            self.assertEqual(por_campo['sleep_score']['baseline_policy'], "split_by_date")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
