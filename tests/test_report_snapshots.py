"""Snapshots dos Markdown e uso de `farol.reports.report_sections` por `farol.reports.today`.

A fixture e' sintetica e 100% deterministica: banco em memoria, valores
gerados por aritmetica inteira e nenhum acesso a dado pessoal. Ela existe
para travar a saida dos relatorios contra regressao silenciosa -- nao para
validar o modelo (isso continua em tests/test_state.py,
tests/test_daily_analysis.py e tests/test_interpretation.py).

Para regravar os snapshots:
    FAROL_UPDATE_SNAPSHOTS=1 .venv/Scripts/python -m unittest discover -s tests
e revise o diff de tests/snapshots/.
"""
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from snapshot_support import assert_snapshot
from farol.core import db
from farol.domain import snapshot, state
from farol.reports import daily_analysis, report_sections, today

FIM = date(2026, 11, 2)
DIA = FIM.isoformat()
INICIO = FIM - timedelta(days=150)
GERADO_EM = "2026-11-02T00:00:00"
PROFILE = {"hr_max": 190, "hr_rest": 50}


class FakeDate(date):
    @classmethod
    def today(cls):
        return cls(FIM.year, FIM.month, FIM.day)


class _ConexaoSemFechar:
    """today.main() fecha a conexao; a fixture continua viva para o tearDown."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nome):
        return getattr(self._conn, nome)

    def close(self):
        pass


def _variacao(i: int, amplitude: int) -> int:
    """Ruido deterministico com amplitude fixa (inteiro, sem aleatoriedade)."""
    return ((i * 37) % (2 * amplitude + 1)) - amplitude


def _novo_banco() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _popular(conn: sqlite3.Connection) -> None:
    for i in range(151):
        d = (INICIO + timedelta(days=i)).isoformat()
        ultimo = d == DIA
        hrv = 34 if ultimo else 45 + _variacao(i, 5)
        rhr = 60 if ultimo else 58 + _variacao(i, 4)
        night = 64 if ultimo else 62 + _variacao(i, 3)
        stress = 24 if ultimo else 22 + _variacao(i, 5)
        bb_min = 22 if ultimo else 25 + _variacao(i, 8)
        resp = 15.4 if ultimo else round(14.2 + _variacao(i, 5) * 0.1, 1)
        spo2 = 96.0 - (i % 2) * 0.3
        total = 405 if ultimo else 430 + _variacao(i, 30)
        score = 74 if ultimo else 80 + _variacao(i, 6)
        tsb = -4.0 if ultimo else 2.0 + _variacao(i, 6)
        # Valores do dia-alvo usados tanto em `daily` quanto em farol_daily_state.
        conn.execute(
            "INSERT INTO daily (date, rhr, hrv_avg, night_hr_avg, sleep_score,"
            " total_sleep_minutes, deep_sleep_minutes, light_sleep_minutes,"
            " rem_sleep_minutes, awakenings, sleep_quality, respiration_avg,"
            " spo2_avg, body_battery_min, stress_avg, avg_sleep_stress,"
            " source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d, rhr, hrv, night, score, total, 90 + _variacao(i, 8), 250 + _variacao(i, 20),
             85 + _variacao(i, 9), 2 + _variacao(i, 2), "Boa", resp,
             spo2, bb_min, stress, 18.0 + _variacao(i, 4), "garmin"),
        )
        conn.execute(
            "INSERT INTO training_load (date, ctl, atl, tsb) VALUES (?,?,?,?)",
            (d, 55.0, 57.0, tsb),
        )
        conn.execute(
            "INSERT INTO farol_daily_state (date, trimp_7d, trimp_28d, ctl, atl, tsb,"
            " atl_ctl_ratio, hrv_avg, rhr, night_hr_avg, stress_avg, body_battery_max,"
            " body_battery_min, respiration_avg, spo2_avg, spo2_min, sleep_total_min,"
            " awake_minutes, deep_sleep_min, rem_sleep_min, sleep_score,"
            " sleep_regularity_min, output_preserved, deterioration_detected)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d, 300 + _variacao(i, 40), 1100 + _variacao(i, 120),
             55.0 + _variacao(i, 4) * 0.4, 57.0 + _variacao(i, 5) * 0.4, tsb,
             1.04 + _variacao(i, 4) * 0.01, hrv, rhr, night, stress,
             70 + _variacao(i, 8), bb_min, resp, spo2, 93.0 + _variacao(i, 2) * 0.3,
             total, 18 + _variacao(i, 6), 90 + _variacao(i, 10), 85 + _variacao(i, 10),
             score, 22 + _variacao(i, 5),
             "yes" if ultimo else None, "no" if ultimo else None),
        )

    reps = [{"pace_min_km": 4.6 + j * 0.03, "avg_hr": 160 + j,
             "max_hr": 168 + j, "avg_cadence": 176 + j} for j in range(4)]
    splits = json.dumps({"n_reps": 4, "avg_distance_km": 0.8, "reps": reps})
    conn.execute(
        "INSERT INTO activities (activity_id, date, type, name, duration_min,"
        " distance_km, avg_hr, max_hr, avg_cadence, ground_contact_time,"
        " vertical_oscillation, trimp, splits, source) VALUES"
        " (1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (DIA, "running", "Rodagem", 50, 10.2, 150, 174, 176, 240, 8.1, 90,
         splits, "garmin"),
    )
    conn.execute(
        "INSERT INTO activities (activity_id, date, type, name, duration_min,"
        " distance_km, trimp, source) VALUES (2,?,?,?,?,?,?,?)",
        (DIA, "strength_training", "Forca", 40, 0.0, 30, "garmin"),
    )
    conn.commit()


def _formatar_estado(conn) -> str:
    # O registro manual de lesao e' pessoal: o snapshot nunca o le.
    with mock.patch("farol.domain.state.injuries.latest_observation", return_value=None):
        return state.build_report(conn, DIA)


def _formatar_analise(conn) -> str:
    with mock.patch("farol.reports.daily_analysis.injuries.latest_observation", return_value=None), \
            mock.patch("farol.reports.daily_analysis.race_log.entry_for_date", return_value=None):
        return daily_analysis.build_analysis(conn, DIA, gerado_em=GERADO_EM)


def _formatar_today(conn, argv: list[str] | None = None) -> str:
    """Saida do relatorio de terminal, sem cor ANSI e sem registro pessoal."""
    with mock.patch.object(today, "date", FakeDate), \
            mock.patch.object(today, "load_profile", return_value=PROFILE), \
            mock.patch("farol.core.db.connect_readonly",
                       return_value=_ConexaoSemFechar(conn)), \
            mock.patch("farol.records.injuries.latest_observation", return_value=None), \
            mock.patch.object(report_sections, "_USAR_COR", False):
        saida = StringIO()
        with redirect_stdout(saida):
            today.main(argv or [])
    return saida.getvalue()


class TestMarkdownSnapshots(unittest.TestCase):
    def setUp(self):
        self.conn = _novo_banco()
        _popular(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_daily_state(self):
        assert_snapshot(self, "DAILY_STATE.md", _formatar_estado(self.conn))

    def test_daily_analysis(self):
        assert_snapshot(self, "DAILY_ANALYSIS.md", _formatar_analise(self.conn))

    def test_today_relatorio_terminal(self):
        assert_snapshot(self, "TODAY.txt", _formatar_today(self.conn))

    def test_today_compact_so_visao_rapida_e_leitura(self):
        texto = _formatar_today(self.conn, ["--compact"])
        self.assertIn("RESUMO RAPIDO", texto)
        self.assertIn("  LEITURA:", texto)
        self.assertIn("EVOLUCAO DOS DOMINIOS", texto)
        # As secoes detalhadas ficam de fora; os rotulos de dominio continuam
        # aparecendo dentro da leitura/tabela, entao a checagem e' pela linha
        # de cabecalho (nome isolado entre as bordas).
        for cabecalho in ("TREINO REALIZADO", "CARGA DE TREINO",
                          "RECUPERACAO", "SONO", "PADROES DETECTADOS"):
            self.assertNotIn(f"\n{cabecalho}\n", texto)

    def test_today_alerts_so_lista_o_que_esta_fora_do_padrao(self):
        texto = _formatar_today(self.conn, ["--alerts"])
        self.assertIn("ALERTAS", texto)
        self.assertIn("Convergencia ativa", texto)
        # Nada das secoes descritivas nem do resumo rapido.
        self.assertNotIn("RESUMO RAPIDO", texto)
        for cabecalho in ("TREINO REALIZADO", "CARGA DE TREINO",
                          "RECUPERACAO", "SONO", "PADROES DETECTADOS"):
            self.assertNotIn(f"\n{cabecalho}\n", texto)


class TestTodayUsaOsModulosCompartilhados(unittest.TestCase):
    """O orquestrador precisa usar a formatacao extraida e o snapshot diario
    -- senao a refatoracao pode ser desfeita sem nenhum teste falhar."""

    def setUp(self):
        self.conn = _novo_banco()
        _popular(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_main_delega_a_formatacao_e_as_metricas_diarias(self):
        with mock.patch.object(today, "date", FakeDate), \
                mock.patch.object(today, "load_profile", return_value=PROFILE), \
                mock.patch("farol.core.db.connect_readonly", return_value=self.conn), \
                mock.patch("farol.records.injuries.latest_observation", return_value=None), \
                mock.patch.object(report_sections, "render_report",
                                  return_value="RELATORIO") as render, \
                mock.patch.object(snapshot, "daily_metrics",
                                  wraps=snapshot.daily_metrics) as metricas:
            saida = StringIO()
            with redirect_stdout(saida):
                today.main([])

        self.assertEqual(saida.getvalue(), "RELATORIO")
        render.assert_called_once()
        metricas.assert_called_once_with(self.conn, DIA)
        # A formatacao vive em report_sections; today so' orquestra.
        self.assertIs(today.report_sections, report_sections)


class TestContextoDeProvaNoResumo(unittest.TestCase):
    """Prova ja realizada nao pode reaparecer como "em -46 dias".

    O bloco de prova carrega previsao, TSB-alvo e nutricao -- todos apontando
    para a proxima prova. Depois da data, o relatorio espera que a proxima
    prova seja declarada em profile.yaml em vez de reciclar a anterior.
    """

    def _ctx(self, **kwargs) -> report_sections.ReportContext:
        base = dict(
            conn=None, hoje=FIM, hoje_str=DIA, profile={}, metricas={},
            vo2max_metric=None, indice=None, notas={}, pesos={}, padroes=[],
            doenca=None, prova=None, tl=None, daily={}, atividades=[],
            dados_estado=None, leitura=None, injury=None, injury_alert=None,
        )
        base.update(kwargs)
        return report_sections.ReportContext(**base)

    def test_prova_realizada_nao_aparece_no_resumo(self):
        ctx = self._ctx(prova={"nome": "Meia-maratona", "dias": -46,
                               "fase": "pos-prova", "distancia_km": 21.1})
        texto = "\n".join(report_sections._secao_resumo(ctx))
        self.assertNotIn("Prova:", texto)
        self.assertNotIn("Previsao da meia", texto)
        self.assertNotIn("Nutricao configurada", texto)

    def test_prova_futura_continua_no_resumo(self):
        ctx = self._ctx(prova={"nome": "Meia-maratona", "dias": 10,
                               "fase": "polimento", "distancia_km": 21.1})
        texto = "\n".join(report_sections._secao_resumo(ctx))
        self.assertIn("Prova: Meia-maratona em 10 dias", texto)


if __name__ == "__main__":
    unittest.main()
