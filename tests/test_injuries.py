import json
import tempfile
import unittest
from pathlib import Path

from farol.records import injuries


class TestInjuryLog(unittest.TestCase):
    def test_as_of_nao_vaza_observacao_futura(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "injury.json"
            path.write_text(json.dumps({"observations": [
                {"date": "2026-09-10", "area": "pe", "pain": 2},
                {"date": "2026-09-20", "area": "pe", "pain": 8},
            ]}), encoding="utf-8")
            latest = injuries.latest_observation("2026-09-16", path)
            self.assertEqual(latest["pain"], 2)

    def test_alerta_so_usa_piora_ou_capacidade_marcada(self):
        self.assertIsNone(injuries.observation_alert({"pain": 10, "can_walk": True, "can_run": True}))
        self.assertIn("nao correr", injuries.observation_alert({"worse": True}))
        self.assertIn("nao correr", injuries.observation_alert({"can_run": False}))

    def test_add_normaliza_e_grava(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "injury.json"
            entry = injuries.add_observation({
                "date": "2026-09-16", "area": "dedao", "pain": 4, "swelling": "leve",
                "shoe": "Teste", "can_walk": True, "can_run": True, "worse": False,
            }, path)
            self.assertEqual(entry["pain"], 4)
            self.assertEqual(injuries.latest_observation("2026-09-16", path)["area"], "dedao")
