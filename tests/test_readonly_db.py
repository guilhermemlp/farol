import sqlite3
import tempfile
import unittest
from pathlib import Path

from farol.core import db
from farol.core.config import DB_PATH


class TestConnectReadonly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "source.db"
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE evidence (value INTEGER)")
        conn.execute("INSERT INTO evidence VALUES (7)")
        conn.commit()
        conn.close()

    def tearDown(self):
        if hasattr(self, "conn"):
            self.conn.close()
        self.tmp.cleanup()

    def test_select_funciona_sem_inicializar_schema(self):
        self.conn = db.connect_readonly(str(self.path))

        self.assertEqual(self.conn.execute("SELECT value FROM evidence").fetchone()["value"], 7)
        tables = {row["name"] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        self.assertEqual(tables, {"evidence"})

    def test_mutacoes_falham_por_forca_do_sqlite(self):
        self.conn = db.connect_readonly(str(self.path))

        with self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("INSERT INTO evidence VALUES (8)")
        with self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("CREATE TABLE should_not_exist (id INTEGER)")

    def test_connect_write_em_tempfile_aplica_schema_e_permite_escrita(self):
        self.conn = db.connect_write(str(self.path))

        self.conn.execute("INSERT INTO daily (date, rhr) VALUES ('2026-01-01', 70)")
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT rhr FROM daily").fetchone()["rhr"], 70)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(activities)")}
        self.assertIn("splits_status", columns)

    @unittest.skipUnless(Path(DB_PATH).exists(), "banco pessoal indisponível")
    def test_banco_pessoal_abre_por_compatibilidade_somente_para_leitura(self):
        conn = db.connect()
        self.addCleanup(conn.close)

        self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden_by_readonly (id INTEGER)")

    @unittest.skipUnless(Path(DB_PATH).exists(), "banco pessoal indisponível")
    def test_api_mutavel_recusa_banco_pessoal_durante_testes(self):
        with self.assertRaisesRegex(RuntimeError, "Testes não podem abrir"):
            db.connect_write()


if __name__ == "__main__":
    unittest.main()
