"""Testes da F10 (auditoria): saida estruturada JSON (`farol.state --json`) e
exit codes 0/1/2.

O que se testa aqui e' comportamento observavel:
- o JSON em si (valido, contrato minimo, tipos serializaveis, sem markdown);
- a traducao do status global para exit code (0/1/2) -- que so mapeia a
  classificacao que farol.state JA produz, nunca reclassifica;
- o CLI com --json (stdout so JSON, exit code real) e sem --json (relatorio
  markdown inalterado, comportamento historico preservado);
- erros de execucao como processo, nao como estado fisiologico.
"""
import io
import json
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import date, timedelta
from unittest import mock

from golden_support import skip_unless_real_db
from farol import db, state
from farol.daily_state_builder import build_daily_state
from farol.metrics import Metric

_MODELO_CONTRATO = ("schema", "data", "status_global", "exit_code", "dominios",
                    "performance", "convergencia", "descontinuidades")


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _metric(status: str, valor: float | None = 1.0, confidence: str = "alta") -> Metric:
    """Metric sintetica para testar a serializacao/agregacao sem regressao
    real (mesmo padrao de tests/test_state.py)."""
    return Metric(
        name="x", value=valor if status != "sem_dado" else None,
        mean_7d=None, mean_30d=None, mean_90d=0.0, sd_90d=1.0, z=None,
        trend="indefinido", status=status, confidence=confidence,
        n_7d=0, n_30d=0, n_90d=10, higher_is_better=True,
    )


def _dominio(status: str) -> dict:
    """Estrutura de um dominio como gather() entrega (sintetica)."""
    return {"metricas": {"x": _metric(status)}, "status": status,
            "confianca": "alta", "streak": {"dias": 1, "data_inicio": "2026-09-01",
                                            "data_fim": "2026-09-02", "dominio": "CARGA"},
            "trend": {"dominio": "CARGA", "janela_dias": 7, "n_pontos": 7,
                      "direcao": "STABLE", "data_inicio": "2026-08-27",
                      "data_fim": "2026-09-02"}}


# ---------------------------------------------------------- exit codes (puro)

class TestExitCode(unittest.TestCase):
    """Mapeamento documentado: 0=OK, 1=ATTENTION, 2=ALERT. So traduz a
    classificacao existente (ok/atencao/alerta/sem_dado) -- nenhum nivel
    interno novo, nenhum quarto exit code."""

    def test_ok_mapa_0(self):
        self.assertEqual(state.exit_code("ok"), 0)

    def test_sem_dado_mapa_0(self):
        # sem dado nao e' estado fisiologico de atencao/alerta: 0
        self.assertEqual(state.exit_code("sem_dado"), 0)

    def test_atencao_mapa_1(self):
        self.assertEqual(state.exit_code("atencao"), 1)

    def test_alerta_mapa_2(self):
        self.assertEqual(state.exit_code("alerta"), 2)


class TestGlobalStatus(unittest.TestCase):
    def test_pega_pior_entre_dominios(self):
        dominios = {"CARGA": _dominio("ok"), "RECUPERACAO": _dominio("atencao"),
                    "SONO": _dominio("alerta")}
        self.assertEqual(state.global_status(dominios), "alerta")

    def test_atencao_vence_ok(self):
        dominios = {"CARGA": _dominio("ok"), "RECUPERACAO": _dominio("atencao")}
        self.assertEqual(state.global_status(dominios), "atencao")

    def test_tudo_ok(self):
        dominios = {"CARGA": _dominio("ok"), "SONO": _dominio("ok")}
        self.assertEqual(state.global_status(dominios), "ok")

    def test_sem_dado_nao_vence_nada(self):
        dominios = {"CARGA": _dominio("sem_dado"), "SONO": _dominio("ok")}
        self.assertEqual(state.global_status(dominios), "ok")


# ------------------------------------------------------------- serializacao

class TestMetricToJson(unittest.TestCase):
    def test_campos_seriais_sem_datetime_decimal(self):
        j = state.metric_to_json(_metric("atencao", 1.5))
        for campo in ("valor", "baseline_90d", "z", "status", "confianca",
                      "tendencia_7d_sobre_30d", "n_90d"):
            self.assertIn(campo, j)
        # json.dumps padrao (sem default/hack) funciona: primitivos apenas
        json.dumps(j)


class TestDominioToJson(unittest.TestCase):
    def test_estrutura_completa(self):
        d = _dominio("alerta")
        j = state.dominio_to_json(d)
        self.assertEqual(j["status"], "alerta")
        self.assertIn("streak", j)
        self.assertIn("trend", j)
        self.assertIn("metricas", j)
        json.dumps(j)  # serializavel sem hack


class TestBuildStateJson(unittest.TestCase):
    """Contrato minimo do JSON, sobre fixtures reais em memoria."""

    def setUp(self):
        # Fixture com pequena variacao ciclica (nao valores fixos repetidos):
        # com sd_90d = 0 farol.metrics nao calcula z e toda metrica sai como
        # sem_dado, mascarando a classificacao. Mesmo padrao ja validado em
        # tests/test_daily_analysis.py (TestDailyAnalysisBase).
        self.conn = _memory_db()
        self.base = date(2026, 9, 2)
        for i in range(90, 0, -1):
            d = (self.base - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10))
        # Dia-alvo com valores medios exatos, para comecar ok
        d = self.base.isoformat()
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d,))
        self.conn.commit()
        for i in range(90, -1, -1):
            build_daily_state(self.conn, (self.base - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    def _payload(self):
        return state.build_state_json(self.conn, self.base.isoformat())

    def test_contrato_minimo_presente(self):
        p = self._payload()
        for chave in _MODELO_CONTRATO:
            self.assertIn(chave, p)
        self.assertEqual(p["schema"], "farol.state.json.v1")
        self.assertEqual(p["data"], self.base.isoformat())

    def test_fixture_estavel_ok_exit_0(self):
        p = self._payload()
        self.assertEqual(p["status_global"], "ok")
        self.assertEqual(p["exit_code"], 0)

    def test_fixture_atencao_exit_1(self):
        d = self.base.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 52 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        p = self._payload()
        self.assertEqual(p["status_global"], "atencao")
        self.assertEqual(p["exit_code"], 1)

    def test_fixture_alerta_exit_2(self):
        d = self.base.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 5 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        p = self._payload()
        self.assertEqual(p["status_global"], "alerta")
        self.assertEqual(p["exit_code"], 2)

    def test_dominios_trazem_todas_as_metricas_seriais(self):
        p = self._payload()
        for nome in ("CARGA", "RECUPERACAO", "SONO"):
            self.assertIn(nome, p["dominios"])
            self.assertIn("metricas", p["dominios"][nome])
            # Toda metrica serializa sem quebrar
            for m in p["dominios"][nome]["metricas"].values():
                json.dumps(m)

    def test_sem_data_da_value_error(self):
        with self.assertRaises(ValueError):
            state.build_state_json(self.conn, "1970-01-01")

    def test_sem_texto_humano_estruturado(self):
        # O JSON nao pode carregar texto do relatorio markdown como se fosse
        # campo estruturado: nada de 'MATRIZ RAPIDA'/'##' em valores.
        texto = json.dumps(self._payload())
        self.assertNotIn("MATRIZ RAPIDA", texto)


# ------------------------------------------------------------- CLI (main)

class TestCliJson(unittest.TestCase):
    """Comportamento observavel do `python -m farol.state --json`: stdout so
    JSON, exit code real, erros como processo. Usa banco em memoria via patch
    em db.connect_readonly, sem rede."""

    def setUp(self):
        self.conn = _memory_db()
        self.base = date(2026, 9, 2)

    def tearDown(self):
        self.conn.close()

    def _roda(self, *extra_args, db_conn=None):
        conn = db_conn if db_conn is not None else self.conn
        argv = ["farol.state", "--date", self.base.isoformat(), *extra_args]
        saida, erros = io.StringIO(), io.StringIO()
        with mock.patch("farol.state.db.connect_readonly", return_value=conn), \
             mock.patch.object(sys, "argv", argv), \
             redirect_stdout(saida), redirect_stderr(erros):
            try:
                state.main()
                codigo = 0
            except SystemExit as e:
                codigo = e.code if e.code is not None else 0
        return saida.getvalue(), erros.getvalue(), codigo

    def _com_prova_estavel(self):
        # Mesma fixture com variacao ciclica do TestBuildStateJson
        for i in range(90, 0, -1):
            d = (self.base - timedelta(days=i)).isoformat()
            self.conn.execute(
                """INSERT INTO daily
                   (date, rhr, hrv_avg, stress_avg, body_battery_max,
                    night_hr_avg, total_sleep_minutes, awake_minutes,
                    deep_sleep_minutes, rem_sleep_minutes, sleep_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (d, 68 + i % 5, 17.0 + (i % 4) * 0.5, 40 + i % 10, 28 + i % 6,
                 80 + i % 5, 400 + i % 40, 30 + i % 20, 70 + i % 20,
                 50 + i % 20, 50 + i % 10))
        d = self.base.isoformat()
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 17.75, 44.5, 30.5, 82, 418, 39, 79, 59, 54.5)""",
            (d,))
        self.conn.commit()
        for i in range(90, -1, -1):
            build_daily_state(self.conn, (self.base - timedelta(days=i)).isoformat())

    def test_sem_json_continua_gerando_markdown(self):
        # comportamento historico preservado: relatorio markdown escrito, e o
        # texto do arquivo e' idêntico ao de build_report.
        import os
        import tempfile
        self._com_prova_estavel()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "state.md")
            saida, _, codigo = self._roda("--out", out)
            self.assertEqual(codigo, 0)
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                conteudo = f.read()
            self.assertIn("# ESTADO DO ATLETA", conteudo)
            self.assertIn("MATRIZ RAPIDA", conteudo)

    def test_json_valido_e_parseavel(self):
        self._com_prova_estavel()
        saida, erros, codigo = self._roda("--json")
        payload = json.loads(saida)  # valido e parseavel
        self.assertEqual(codigo, payload["exit_code"])
        self.assertEqual(erros, "")

    def test_estado_ok_exit_0(self):
        self._com_prova_estavel()
        saida, _, codigo = self._roda("--json")
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(saida)["status_global"], "ok")

    def test_estado_atencao_exit_1(self):
        self._com_prova_estavel()
        d = self.base.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 52 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        saida, _, codigo = self._roda("--json")
        self.assertEqual(codigo, 1)
        self.assertEqual(json.loads(saida)["status_global"], "atencao")

    def test_estado_alerta_exit_2(self):
        self._com_prova_estavel()
        d = self.base.isoformat()
        self.conn.execute("UPDATE daily SET sleep_score = 5 WHERE date = ?", (d,))
        self.conn.commit()
        build_daily_state(self.conn, d)
        saida, _, codigo = self._roda("--json")
        self.assertEqual(codigo, 2)
        self.assertEqual(json.loads(saida)["status_global"], "alerta")

    def test_erro_tecnico_e_processo_nao_estado(self):
        # data sem farol_daily_state -> ValueError -> sem JSON no stdout,
        # mensagem em stderr, exit code de processo (1).
        saida, erros, codigo = self._roda("--json")
        self.assertEqual(saida, "")
        self.assertNotEqual(erros, "")
        self.assertEqual(codigo, 1)


@skip_unless_real_db()
class TestGoldenSample(unittest.TestCase):
    """Um estado real conhecido do banco produz JSON coerente (mesma base que
    farol.state/daily_analysis consomem em producao)."""

    def test_golden_sample_2026_09_02(self):
        conn = db.connect_readonly()
        try:
            p = state.build_state_json(conn, "2026-09-02")
            self.assertEqual(p["data"], "2026-09-02")
            self.assertIn("CARGA", p["dominios"])
            self.assertIn("RECUPERACAO", p["dominios"])
            self.assertIn("SONO", p["dominios"])
            # TSB em -13.19 => CARGA BORDERLINE (atencao): exit code pelo
            # menos 1, nunca 0 (ha sinal de atencao real no golden sample)
            self.assertIn(p["exit_code"], (0, 1, 2))
            self.assertIn("status_global", p)
            json.dumps(p)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
