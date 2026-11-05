"""Testes de farol.pipeline.sheets.load_into_db: o ETL central do projeto (planilha ->
banco). Nenhum teste existia para esta funcao antes desta auditoria -- toda
a cobertura de `_drop_superseded_activities`/`_tag_daily_source` em
test_core.py testa auxiliares dela, nunca o caminho principal.
"""
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from farol.core import db
from farol.pipeline import sheets

_HR_MAX = 192.0


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


def _wb():
    """Workbook em memoria, nunca tocando disco -- open_or_create so cria a
    partir do zero quando o caminho nao existe, e nao salva nada sozinho."""
    return sheets.open_or_create("caminho-que-nao-existe.xlsx")


class TestLoadIntoDbDaily(unittest.TestCase):
    def test_mapeia_colunas_da_planilha_para_o_banco(self):
        # resting_hr (planilha) -> rhr (banco): nomes divergem de proposito
        # (ver schema.sql) -- exatamente onde um typo no mapeamento passaria
        # despercebido sem este teste.
        wb = _wb()
        sheets.write_day(
            wb,
            daily_row={"date": "2026-06-15", "resting_hr": 65, "hrv_avg": 22.5,
                       "stress_avg": 30, "vo2max": 40},
            sleep_row={"date": "2026-06-15", "total_sleep_minutes": 420, "sleep_score": 70},
            hrv_row={},
        )
        conn = _memory_db()
        self.addCleanup(conn.close)
        n_daily, n_act = sheets.load_into_db(conn, wb, _HR_MAX)
        self.assertEqual(n_daily, 1)
        self.assertEqual(n_act, 0)

        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertEqual(row["rhr"], 65)
        self.assertEqual(row["hrv_avg"], 22.5)
        self.assertEqual(row["stress_avg"], 30)
        self.assertEqual(row["vo2max"], 40)

    def test_junta_daily_health_com_sleep_pela_data(self):
        wb = _wb()
        sheets.write_day(
            wb,
            daily_row={"date": "2026-06-15", "resting_hr": 65},
            sleep_row={"date": "2026-06-15", "total_sleep_minutes": 415,
                       "sleep_score": 72, "night_hr_avg": 80},
            hrv_row={},
        )
        conn = _memory_db()
        self.addCleanup(conn.close)
        sheets.load_into_db(conn, wb, _HR_MAX)
        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertEqual(row["total_sleep_minutes"], 415)
        self.assertEqual(row["sleep_score"], 72)
        self.assertEqual(row["night_hr_avg"], 80)

    def test_data_como_datetime_e_como_string_ainda_se_juntam(self):
        # openpyxl devolve datetime numa aba e string em outra dependendo de
        # como a celula foi escrita -- _key() precisa normalizar os dois
        # lados, senao o join falha em silencio e o sono nunca chega ao
        # banco (bug ja documentado no docstring de _key()).
        wb = _wb()
        ws_daily = wb[sheets.SHEET_DAILY]
        headers_d = sheets._header_index(ws_daily)
        ws_daily.cell(row=2, column=headers_d["date"], value=datetime(2026, 6, 15))
        ws_daily.cell(row=2, column=headers_d["resting_hr"], value=65)

        ws_sleep = wb[sheets.SHEET_SLEEP]
        headers_s = sheets._header_index(ws_sleep)
        ws_sleep.cell(row=2, column=headers_s["date"], value="2026-06-15")
        ws_sleep.cell(row=2, column=headers_s["sleep_score"], value=72)

        conn = _memory_db()
        self.addCleanup(conn.close)
        sheets.load_into_db(conn, wb, _HR_MAX)
        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertEqual(row["sleep_score"], 72)

    def test_segunda_carga_com_null_nao_apaga_valor_existente(self):
        # _db_upsert usa COALESCE(excluded.x, x): uma sincronizacao que nao
        # trouxe dado para um campo nao pode apagar o que ja estava la.
        wb1 = _wb()
        sheets.write_day(wb1, daily_row={"date": "2026-06-15", "resting_hr": 65, "hrv_avg": 22.5},
                          sleep_row={}, hrv_row={})
        conn = _memory_db()
        self.addCleanup(conn.close)
        sheets.load_into_db(conn, wb1, _HR_MAX)

        wb2 = _wb()
        sheets.write_day(wb2, daily_row={"date": "2026-06-15", "resting_hr": 66},
                          sleep_row={}, hrv_row={})
        sheets.load_into_db(conn, wb2, _HR_MAX)

        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertEqual(row["rhr"], 66)          # atualizado
        self.assertEqual(row["hrv_avg"], 22.5)    # preservado, nao veio na 2a carga

    def test_sync_limpa_erro_quando_endpoint_volta(self):
        # F9: errors e' status da ULTIMA sync, nao dado fisiologico -- o
        # COALESCE do _db_upsert (feito para nao apagar medicao ausente)
        # trabalha ao contrario aqui: se a re-sync do mesmo dia vier limpa
        # (errors=None), o texto de um endpoint que ja voltou a funcionar
        # precisa sumir da celula E do banco, senao o relatorio continua
        # avisando de um problema que nao existe mais.
        wb = _wb()
        sheets.write_day(wb, daily_row={"date": "2026-06-15", "resting_hr": 65,
                                         "errors": "get_stats"},
                          sleep_row={}, hrv_row={})
        conn = _memory_db()
        self.addCleanup(conn.close)
        sheets.load_into_db(conn, wb, _HR_MAX)
        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertEqual(row["errors"], "get_stats")

        # Re-sync do MESMO dia, agora sem falha: o fetch_day devolve
        # errors=None e a celula antiga precisa ser limpa (o _upsert so
        # grava valores nao-None; sem o clear_when_none o texto obsoleto
        # voltaria para o banco na proxima carga).
        sheets.write_day(wb, daily_row={"date": "2026-06-15", "resting_hr": 66,
                                         "errors": None},
                          sleep_row={}, hrv_row={})
        ws = wb[sheets.SHEET_DAILY]
        headers = sheets._header_index(ws)
        self.assertIsNone(ws.cell(row=2, column=headers["errors"]).value)

        sheets.load_into_db(conn, wb, _HR_MAX)
        row = dict(conn.execute("SELECT * FROM daily WHERE date = '2026-06-15'").fetchone())
        self.assertIsNone(row["errors"])
        self.assertEqual(row["rhr"], 66)          # demais campos atualizaram normal

    def test_write_day_parcial_nao_limpa_errors(self):
        # write_day com row parcial (sem a chave errors) nao deve mexer na
        # celula: so a sync completa, com errors=None explicito, limpa. Um
        # dia que ficou para tras nao perde o registro da falha por acaso.
        wb = _wb()
        sheets.write_day(wb, daily_row={"date": "2026-06-15", "errors": "get_stats"},
                          sleep_row={}, hrv_row={})
        sheets.write_day(wb, daily_row={"date": "2026-06-15", "resting_hr": 66},
                          sleep_row={}, hrv_row={})
        ws = wb[sheets.SHEET_DAILY]
        headers = sheets._header_index(ws)
        self.assertEqual(ws.cell(row=2, column=headers["errors"]).value, "get_stats")


class TestLoadIntoDbActivities(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _wb_com_rhr(self, rhr=65):
        wb = _wb()
        sheets.write_day(wb, daily_row={"date": "2026-06-15", "resting_hr": rhr}, sleep_row={}, hrv_row={})
        return wb

    def test_atividade_garmin_calcula_trimp_e_marca_fonte(self):
        wb = self._wb_com_rhr()
        sheets.write_activity(wb, {
            "date": "2026-06-15", "activity_id": 12345, "type": "running",
            "duration_minutes": 40.0, "avg_hr": 150, "distance_km": 8.0,
        })
        n_daily, n_act = sheets.load_into_db(self.conn, wb, _HR_MAX)
        self.assertEqual(n_act, 1)
        row = dict(self.conn.execute("SELECT * FROM activities WHERE activity_id = 12345").fetchone())
        self.assertEqual(row["source"], "garmin")
        self.assertGreater(row["trimp"], 0)
        self.assertEqual(row["distance_km"], 8.0)

    def test_atividade_legada_anula_distancia_mas_preserva_trimp(self):
        wb = _wb()
        sheets.write_day(wb, daily_row={"date": "2026-06-01", "resting_hr": 65}, sleep_row={}, hrv_row={})
        sheets.write_activity(wb, {
            "date": "2026-06-01", "activity_id": -1, "type": "running",
            "duration_minutes": 40.0, "avg_hr": 150, "distance_km": 150158.0,
        })
        sheets.load_into_db(self.conn, wb, _HR_MAX)
        row = dict(self.conn.execute("SELECT * FROM activities WHERE activity_id = -1").fetchone())
        self.assertEqual(row["source"], "legado")
        self.assertIsNone(row["distance_km"])
        self.assertIsNone(row["avg_pace_min_km"])
        self.assertGreater(row["trimp"], 0)  # duracao/FC continuam validas

    def test_atividade_legada_reconciliada_importa_distancia_sem_mudar_fonte(self):
        wb = _wb()
        sheets.write_activity(wb, {
            "date": "2026-05-01", "activity_id": -123, "type": "running",
            "duration_minutes": 40, "distance_km": 6.5,
            "avg_pace_min_km": 6.1, "moving_minutes": 39.65,
            "historical_source": "strava_export", "vertical_ratio": 9.2,
            "running_dynamics_source": "fit_in_strava_export",
        })
        sheets.load_into_db(self.conn, wb, _HR_MAX)
        row = self.conn.execute(
            "SELECT source, distance_km, avg_pace_min_km, vertical_ratio "
            "FROM activities WHERE activity_id = -123").fetchone()
        self.assertEqual(row["source"], "legado")
        self.assertEqual(row["distance_km"], 6.5)
        self.assertEqual(row["avg_pace_min_km"], 6.1)
        self.assertEqual(row["vertical_ratio"], 9.2)

        # Se a marca for retirada da planilha, o upsert não pode reter
        # distância validada apenas por uma carga anterior.
        header = sheets._header_index(wb[sheets.SHEET_ACTIVITIES])
        wb[sheets.SHEET_ACTIVITIES].cell(2, header["historical_source"]).value = None
        sheets.load_into_db(self.conn, wb, _HR_MAX)
        row = self.conn.execute(
            "SELECT distance_km, avg_pace_min_km, historical_source "
            "FROM activities WHERE activity_id = -123").fetchone()
        self.assertEqual(tuple(row), (None, None, None))

    def test_corrida_somente_do_export_nao_muda_trimp_diario(self):
        wb = _wb()
        sheets.write_day(wb, daily_row={"date": "2026-03-30", "resting_hr": 65},
                         sleep_row={}, hrv_row={})
        sheets.write_activity(wb, {
            "date": "2026-03-30", "activity_id": -123456789,
            "type": "running", "duration_minutes": 35,
            "distance_km": 5, "avg_hr": 160,
            "historical_source": "strava_export", "historical_only": 1,
        })
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, trimp) "
            "VALUES (-123456789, '2026-03-30', 'running', 80)")
        sheets.load_into_db(self.conn, wb, _HR_MAX)
        row = self.conn.execute(
            "SELECT distance_km, trimp, historical_only FROM activities "
            "WHERE activity_id = -123456789").fetchone()
        self.assertEqual(tuple(row), (5, None, 1))

    def test_atividade_sem_activity_id_e_ignorada(self):
        wb = self._wb_com_rhr()
        sheets.write_activity(wb, {"date": "2026-06-15", "type": "running"})
        n_daily, n_act = sheets.load_into_db(self.conn, wb, _HR_MAX)
        self.assertEqual(n_act, 0)


class TestBackupDaPlanilha(unittest.TestCase):
    """A planilha e' a fonte de verdade: o ingest precisa guardar o estado
    anterior a gravacao, sem sobrescrever a primeira copia do dia."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sheet = Path(self._tmp.name) / "garmin_health.xlsx"

    def test_copia_antes_da_gravacao(self):
        self.sheet.write_bytes(b"conteudo original")
        destino = sheets.backup_sheet(self.sheet, date(2026, 11, 5))
        self.assertEqual(destino.name, "garmin_health.xlsx.bak-20261105")
        self.assertEqual(destino.read_bytes(), b"conteudo original")

    def test_nao_sobrescreve_a_primeira_copia_do_dia(self):
        self.sheet.write_bytes(b"estado da manha")
        sheets.backup_sheet(self.sheet, date(2026, 11, 5))
        self.sheet.write_bytes(b"estado da tarde")
        destino = sheets.backup_sheet(self.sheet, date(2026, 11, 5))
        self.assertEqual(destino.read_bytes(), b"estado da manha")

    def test_sem_planilha_nao_cria_nada(self):
        self.assertIsNone(sheets.backup_sheet(self.sheet, date(2026, 11, 5)))
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
