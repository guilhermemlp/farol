"""Relatório histórico usa só corridas com distância rastreável."""

import sqlite3
import unittest

from farol.db import ensure_schema
from farol.running_history import build_report


class TestRunningHistory(unittest.TestCase):
    def test_une_legado_reconciliado_e_garmin_sem_contar_legado_incerto(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        ensure_schema(conn)
        rows = [
            (-1, "running", 5, "legado", "strava_export"),
            (-2, "running", 900, "legado", None),
            (3, "treadmill_running", 3, "garmin", None),
        ]
        for aid, kind, km, source, history in rows:
            conn.execute(
                "INSERT INTO activities (activity_id, date, type, distance_km, "
                "moving_minutes, avg_pace_min_km, source, historical_source) "
                "VALUES (?, '2026-03-01', ?, ?, 30, 6, ?, ?)",
                (aid, kind, km, source, history))
        report = build_report(conn, "2026-03-01", "2026-03-01")
        self.assertIn("2 corridas, 8,00 km", report)
        self.assertIn("Pendência de reconciliação: 1", report)
        self.assertNotIn("900,00 km", report)


if __name__ == "__main__":
    unittest.main()
