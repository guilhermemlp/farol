import unittest
from pathlib import Path


_GOLDEN_TESTS = {
    "test_daily_analysis.py": "conn = db.connect_readonly()",
    "test_dashboard.py": "conn = db.connect_readonly()",
    "test_longitudinal.py": "conn = db.connect_readonly()",
    "test_race_prediction.py": "conn = db.connect_readonly()",
    "test_state_json.py": "conn = db.connect_readonly()",
}


class TestGoldenDatabaseContract(unittest.TestCase):
    def test_golden_tests_declaram_conexao_somente_leitura(self):
        tests_dir = Path(__file__).parent
        for filename, expected in _GOLDEN_TESTS.items():
            with self.subTest(filename=filename):
                source = (tests_dir / filename).read_text(encoding="utf-8")
                self.assertIn(expected, source)
                self.assertNotIn("conn = db.connect()", source)


if __name__ == "__main__":
    unittest.main()
