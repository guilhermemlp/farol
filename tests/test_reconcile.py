import tempfile
import unittest
from pathlib import Path

from farol.core import db
from farol.pipeline import sheets
from farol.domain.daily_state_builder import build_daily_state
from farol.pipeline.reconcile import reconcile
from farol.domain.training_load import recompute_training_load


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.db_path = base / "farol.db"
        self.sheet_path = base / "source.xlsx"
        self.conn = db.connect_write(str(self.db_path))
        self._prepare_sheet()
        self._prepare_database()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _prepare_sheet(self):
        workbook = sheets.open_or_create(str(self.sheet_path))
        sheets.write_day(
            workbook,
            {"date": "2026-01-01", "steps": 0, "resting_hr": 60, "hrv_avg": 20,
             "stress_avg": 40, "body_battery_max": 30},
            {"date": "2026-01-01", "sleep_score": 50},
            {"date": "2026-01-01", "hrv_avg": 20},
        )
        sheets.write_day(workbook, {"date": "2026-01-03", "resting_hr": 65}, {}, {})
        sheets.write_activity(
            workbook,
            {"date": "2026-01-01", "activity_id": 10, "type": "running", "name": "na planilha",
             "duration_minutes": 30, "avg_hr": 150},
        )
        sheets.write_activity(
            workbook,
            {"date": "2026-01-03", "activity_id": 12, "type": "running", "name": "so fonte"},
        )
        workbook.save(self.sheet_path)
        workbook.close()

    def _prepare_database(self):
        self.conn.execute(
            "INSERT INTO daily (date, steps, rhr, hrv_avg, stress_avg, body_battery_max, sleep_score) "
            "VALUES ('2026-01-01', 0, 61, 20, 40, 30, 50)"
        )
        # A segunda linha não existe na fonte e reproduz as quatro constantes
        # da fixture histórica. A reconciliação deve marcá-las como suspeitas,
        # não como prova de que a fixture escreveu no banco.
        self.conn.execute(
            "INSERT INTO daily (date, rhr, hrv_avg, stress_avg, body_battery_max, sleep_score) "
            "VALUES ('2026-01-02', 70, 18, 45, 30, 55)"
        )
        self.conn.execute("INSERT INTO hrv (date, hrv_avg) VALUES ('2026-01-01', 20)")
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, name, duration_min, avg_hr, trimp, source) "
            "VALUES (10, '2026-01-01', 'running', 'no banco', 30, 150, 40, 'garmin')"
        )
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, name, trimp, source) "
            "VALUES (11, '2026-01-02', 'running', 'so banco', 10, 'garmin')"
        )
        self.conn.commit()
        recompute_training_load(self.conn, "2026-01-01")
        self.conn.execute("UPDATE training_load SET ctl = 999 WHERE date = '2026-01-01'")
        build_daily_state(self.conn, "2026-01-01")
        build_daily_state(self.conn, "2026-01-02")
        self.conn.execute("UPDATE farol_daily_state SET stress_avg = 999 WHERE date = '2026-01-01'")
        self.conn.commit()

    def test_relatorio_cobre_fontes_e_materializacoes_sem_escrever(self):
        report = reconcile(str(self.db_path), str(self.sheet_path), hr_max=190).to_dict()
        differences = report["differences"]
        statuses = {item["status"] for item in differences}
        self.assertTrue({"match", "db_only", "source_only", "different", "derived_difference", "suspect"} <= statuses)

        def find(table, key, field, status):
            return next(item for item in differences if item["table"] == table and item["key"] == key
                        and item["field"] == field and item["status"] == status)

        self.assertEqual(find("daily", "2026-01-01", "steps", "match")["db_value"], 0)
        self.assertIsNone(find("daily", "2026-01-01", "hydration_ml", "match")["db_value"])
        self.assertEqual(find("daily", "2026-01-01", "rhr", "different")["source_value"], 60)
        suspect = find("daily", "2026-01-02", "hrv_avg", "suspect")
        self.assertEqual(suspect["evidence_level"], "forte evidência")
        self.assertIn("fixture", suspect["observation"])
        self.assertEqual(find("training_load", "2026-01-01", "ctl", "derived_difference")["db_value"], 999)
        self.assertEqual(find("farol_daily_state", "2026-01-01", "stress_avg", "derived_difference")["db_value"], 999)


if __name__ == "__main__":
    unittest.main()
