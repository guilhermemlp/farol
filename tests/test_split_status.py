import json
import sqlite3
import unittest

from farol import db
from farol.ingest import sync_activity_splits
from farol.splits import historical_splits_status, split_retry_candidates


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.ensure_schema(conn)
    return conn


class _GarminSuccess:
    def get_activity_typed_splits(self, activity_id):
        return {"splits": [{"type": "INTERVAL_ACTIVE", "distance": 1000, "duration": 240}]}


class _GarminEmpty:
    def get_activity_typed_splits(self, activity_id):
        return {"splits": [{"type": "WARMUP", "distance": 800, "duration": 300}]}


class _GarminFailure:
    def get_activity_typed_splits(self, activity_id):
        raise RuntimeError("Garmin indisponível")


class TestSplitStatus(unittest.TestCase):
    def setUp(self):
        self.conn = _memory_db()

    def tearDown(self):
        self.conn.close()

    def _activity(self, activity_id=1, splits=None, status=None):
        self.conn.execute(
            "INSERT INTO activities (activity_id, date, type, source, splits, splits_status) "
            "VALUES (?, '2026-09-01', 'running', 'garmin', ?, ?)",
            (activity_id, splits, status),
        )
        self.conn.commit()

    def _row(self, activity_id=1):
        return self.conn.execute(
            "SELECT splits, splits_status FROM activities WHERE activity_id = ?", (activity_id,)
        ).fetchone()

    def test_pending_e_elegivel(self):
        self._activity()
        self.assertEqual(split_retry_candidates(self.conn), [1])

    def test_success_preserva_payload_e_nao_e_retentado(self):
        self._activity()
        self.assertEqual(sync_activity_splits(self.conn, _GarminSuccess()), 1)

        row = self._row()
        self.assertEqual(row["splits_status"], "success")
        self.assertEqual(json.loads(row["splits"])["n_reps"], 1)
        self.assertEqual(split_retry_candidates(self.conn), [])

        payload = row["splits"]
        sync_activity_splits(self.conn, _GarminFailure())
        row = self._row()
        self.assertEqual(row["splits_status"], "success")
        self.assertEqual(row["splits"], payload)

    def test_resposta_valida_sem_segmentos_vira_empty_sem_retry(self):
        self._activity()
        self.assertEqual(sync_activity_splits(self.conn, _GarminEmpty()), 0)

        row = self._row()
        self.assertEqual(row["splits_status"], "empty")
        self.assertIsNone(row["splits"])
        self.assertEqual(split_retry_candidates(self.conn), [])

    def test_erro_permanece_retryable_sem_mascarar_com_json_null(self):
        self._activity()
        self.assertEqual(sync_activity_splits(self.conn, _GarminFailure()), 0)

        row = self._row()
        self.assertEqual(row["splits_status"], "error")
        self.assertIsNone(row["splits"])
        self.assertEqual(split_retry_candidates(self.conn), [1])

    def test_json_null_historico_e_unknown_nunca_error(self):
        self._activity(splits="null")
        self.assertEqual(historical_splits_status("null"), "unknown")
        self.assertEqual(split_retry_candidates(self.conn), [1])

    def test_status_unknown_explicito_tambem_e_retryable(self):
        self._activity(splits="null", status="unknown")
        self.assertEqual(split_retry_candidates(self.conn), [1])

    def test_json_invalido_historico_tambem_e_unknown(self):
        self.assertEqual(historical_splits_status("{invalido"), "unknown")


if __name__ == "__main__":
    unittest.main()
