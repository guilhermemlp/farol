"""Testes do registro manual pos-prova: arquivo isolado e sem vazamento temporal."""
import tempfile
import unittest
from pathlib import Path

from farol import race_log


class TestRaceLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "race_log.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_substitui_relato_da_mesma_prova(self):
        race_log.add_entry({"date": "2026-09-19", "rpe": 8, "shoe": "A"}, self.path)
        saved = race_log.add_entry({"date": "2026-09-19", "rpe": 9, "shoe": "B",
                                    "nutrition_carbohydrate_g": 129}, self.path)

        self.assertEqual(saved["rpe"], 9)
        entries = race_log.load_entries(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["shoe"], "B")
        self.assertEqual(entries[0]["nutrition_carbohydrate_g"], 129)

    def test_preserva_temperatura_decimal(self):
        saved = race_log.add_entry({"date": "2026-09-19", "temperature_c": 29.6}, self.path)

        self.assertEqual(saved["temperature_c"], 29.6)

    def test_nao_expoe_relato_de_prova_futura(self):
        race_log.add_entry({"date": "2026-09-19", "rpe": 8}, self.path)

        self.assertIsNone(race_log.entry_as_of("2026-09-19", "2026-09-18", self.path))
        self.assertEqual(race_log.entry_as_of("2026-09-19", "2026-09-19", self.path)["rpe"], 8)

    def test_rejeita_rpe_fora_da_escala_do_proprio_formulario(self):
        with self.assertRaisesRegex(ValueError, "RPE"):
            race_log.add_entry({"date": "2026-09-19", "rpe": 11}, self.path)


if __name__ == "__main__":
    unittest.main()
