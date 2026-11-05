"""Testes de farol.insights.calibration: geracao da entrada diaria (build_entry) e
persistencia append-only do log (append_entry). Fase 12 -- CALIBRACAO.md."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from farol.core import db
from farol.insights import calibration
from farol.domain.daily_state_builder import build_daily_state

_PROFILE = {"recovery_index": {"weights": {
    "sleep_score": 0.30, "hrv_avg": 0.30, "rhr": 0.20, "tsb": 0.20,
}}}


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class TestBuildEntry(unittest.TestCase):
    """Baseline com variacao ciclica (mesmo truque de test_longitudinal.py,
    para sd_90d != 0 e o indice/z sairem calculados, nao 'sem dado').
    Sem activities/training_load de proposito: CARGA fica sem_dado, o mesmo
    comportamento ja coberto por farol.domain.state e farol.domain.longitudinal -- este
    teste so confirma que farol.insights.calibration LE esse resultado corretamente,
    nao recalcula nada."""

    def setUp(self):
        self.conn = _memory_db()
        self.base_date = date(2026, 9, 2)
        for i in range(100, 0, -1):
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
        self.conn.execute(
            """INSERT INTO daily
               (date, rhr, hrv_avg, stress_avg, body_battery_max,
                night_hr_avg, total_sleep_minutes, awake_minutes,
                deep_sleep_minutes, rem_sleep_minutes, sleep_score)
               VALUES (?, 70, 18.0, 44, 30, 82, 418, 39, 79, 59, 55)""",
            (self.base_date.isoformat(),),
        )
        self.conn.commit()
        for i in range(100, -1, -1):
            build_daily_state(self.conn, (self.base_date - timedelta(days=i)).isoformat())

    def tearDown(self):
        self.conn.close()

    def test_data_sem_farol_daily_state_da_erro_claro(self):
        with self.assertRaises(ValueError):
            calibration.build_entry(self.conn, "1970-01-01", _PROFILE)

    def test_entrada_registra_os_3_dominios(self):
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        self.assertEqual(set(entry["dominios"]), {"CARGA", "RECUPERACAO", "SONO"})

    def test_carga_sem_training_load_fica_sem_dado(self):
        # Nenhuma linha em training_load nesta fixture -- mesmo comportamento
        # de farol.domain.state, so confirmando que build_entry nao inventa dado.
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        self.assertEqual(entry["dominios"]["CARGA"], "SEM DADO")

    def test_indice_calculado_quando_ha_variancia_na_baseline(self):
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        self.assertIsNotNone(entry["indice"])

    def test_longitudinal_presente_para_os_3_dominios(self):
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        self.assertEqual(set(entry["longitudinal"]), {"CARGA", "RECUPERACAO", "SONO"})
        for txt in entry["longitudinal"].values():
            self.assertIsInstance(txt, str)

    def test_nao_altera_o_banco(self):
        antes_linhas = self.conn.execute(
            "SELECT COUNT(*) FROM farol_daily_state").fetchone()[0]
        antes_row = dict(self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),)).fetchone())

        calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)

        depois_linhas = self.conn.execute(
            "SELECT COUNT(*) FROM farol_daily_state").fetchone()[0]
        depois_row = dict(self.conn.execute(
            "SELECT * FROM farol_daily_state WHERE date = ?",
            (self.base_date.isoformat(),)).fetchone())
        self.assertEqual(antes_linhas, depois_linhas)
        self.assertEqual(antes_row, depois_row)

    def test_render_entry_contem_todas_as_secoes(self):
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        texto = calibration.render_entry(entry)
        for secao in ("DATA:", "FAROL (state.gather + farol.reports.today)", "GARMIN (segundo juiz",
                      "LONGITUDINAL", "PERCEPCAO REAL", "BATEU COM A REALIDADE?", "OBSERVACOES:"):
            self.assertIn(secao, texto)

    def test_readiness_sem_dado_na_fixture(self):
        # Fixture nao insere training_readiness_score -- confirma que
        # build_entry nao inventa dado e nao quebra sem ele.
        entry = calibration.build_entry(self.conn, self.base_date.isoformat(), _PROFILE)
        self.assertIn("sem dado", entry["readiness_txt"])
        self.assertEqual(entry["concordancia_garmin"], "sem dado suficiente para comparar")


class TestConcordanciaGarmin(unittest.TestCase):
    """_concordancia_garmin: comparacao no mesmo vocabulario ok/atencao/alerta
    -- nunca 'concorda' quando falta dado de um dos lados."""

    def test_concorda_quando_buckets_iguais(self):
        self.assertEqual(calibration._concordancia_garmin("ok", "ok"), "concorda")

    def test_diverge_quando_buckets_diferentes(self):
        resultado = calibration._concordancia_garmin("ok", "alerta")
        self.assertIn("diverge", resultado)
        self.assertIn("farol=ok", resultado)
        self.assertIn("garmin=alerta", resultado)

    def test_sem_dado_de_um_lado_nao_concorda(self):
        self.assertEqual(calibration._concordancia_garmin("sem_dado", "ok"),
                          "sem dado suficiente para comparar")
        self.assertEqual(calibration._concordancia_garmin("ok", "sem_dado"),
                          "sem dado suficiente para comparar")


class TestAppendEntry(unittest.TestCase):
    """append_entry() opera so sobre arquivo texto -- entry sintetico, sem
    banco, para isolar a logica de persistencia append-only."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "CALIBRACAO_LOG.txt"
        self.entry_02 = {
            "date": "2026-09-02", "indice": 6.5, "indice_label": "na sua media",
            "dominios": {"CARGA": "NORMAL", "RECUPERACAO": "NORMAL", "SONO": "NORMAL"},
            "performance": "sem dado (sem treino intervalado no dia)",
            "convergencia": "nenhum padrao (no maximo 1 dominio fora do normal)",
            "longitudinal": {"CARGA": "sem streak ativo", "RECUPERACAO": "sem streak ativo",
                              "SONO": "sem streak ativo"},
            "readiness_txt": "sem dado hoje (campo tem ~40% de cobertura)",
            "concordancia_garmin": "sem dado suficiente para comparar",
        }
        self.entry_03 = {**self.entry_02, "date": "2026-09-03"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_cria_o_arquivo_na_primeira_entrada(self):
        self.assertFalse(self.path.exists())
        self.assertTrue(calibration.append_entry(self.path, self.entry_02))
        self.assertTrue(self.path.exists())

    def test_entrada_aparece_no_arquivo(self):
        calibration.append_entry(self.path, self.entry_02)
        texto = self.path.read_text(encoding="utf-8")
        self.assertIn("DATA: 2026-09-02", texto)
        self.assertIn("CARGA: NORMAL", texto)

    def test_segunda_entrada_nao_apaga_a_primeira(self):
        calibration.append_entry(self.path, self.entry_02)
        calibration.append_entry(self.path, self.entry_03)
        texto = self.path.read_text(encoding="utf-8")
        self.assertIn("DATA: 2026-09-02", texto)
        self.assertIn("DATA: 2026-09-03", texto)

    def test_mesma_data_duas_vezes_nao_duplica_nem_altera(self):
        self.assertTrue(calibration.append_entry(self.path, self.entry_02))
        antes = self.path.read_text(encoding="utf-8")
        self.assertFalse(calibration.append_entry(self.path, self.entry_02))
        depois = self.path.read_text(encoding="utf-8")
        self.assertEqual(antes, depois)
        self.assertEqual(depois.count("DATA: 2026-09-02"), 1)


if __name__ == "__main__":
    unittest.main()
