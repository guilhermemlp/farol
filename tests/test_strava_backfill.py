"""Importação local com export sintético; nunca usa arquivos pessoais."""

import csv
import gzip
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from farol.pipeline.fit_dynamics import summarize_dynamics
from farol.pipeline.sheets import SHEET_ACTIVITIES, _header_index, open_or_create, write_activity
from farol.pipeline.strava_backfill import reconcile


def _fit_record():
    fields = [(4, 1, 0x02), (39, 2, 0x84), (41, 2, 0x84),
              (83, 2, 0x84), (85, 2, 0x84)]
    definition = bytes([0x60, 0, 0]) + struct.pack("<H", 20) + bytes([len(fields)])
    definition += b"".join(bytes(f) for f in fields)
    definition += bytes([1, 1, 2, 0])  # um campo developer de 2 bytes
    data = bytes([0, 85]) + struct.pack("<HHHH", 840, 2700, 920, 9000) + b"\x01\x02"
    header = bytes([14, 0x20]) + struct.pack("<H", 100) + struct.pack("<I", len(definition + data))
    header += b".FIT" + b"\0\0"
    return gzip.compress(header + definition + data + b"\0\0")


class TestFitDynamics(unittest.TestCase):
    def test_campos_padrao_e_developer_nao_desalinham_registros(self):
        metrics = summarize_dynamics(_fit_record())
        self.assertEqual(metrics, {
            "avg_cadence": 170, "vertical_oscillation": 8.4,
            "ground_contact_time": 270, "vertical_ratio": 9.2,
            "stride_length": 90,
        })


class TestStravaBackfill(unittest.TestCase):
    def test_previa_importacao_e_reexecucao_sao_idempotentes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sheet = root / "health.xlsx"
            archive = root / "export.zip"
            wb = open_or_create(str(sheet))
            write_activity(wb, {"date": "2026-03-01", "activity_id": -1,
                                "type": "running", "duration_minutes": 30,
                                "distance_km": 30000, "avg_cadence": 80,
                                "elevation_gain_m": 300})
            wb.save(sheet)
            fields = ["ID da atividade", "Data da atividade", "Tipo de atividade",
                      "Tempo de movimentação", "Tempo decorrido", "Distância",
                      "Ganho de elevação", "Perda de elevação", "Cadência média",
                      "Nome do arquivo", "Nome da atividade"]
            rows = [
                ["100", "1 de mar. de 2026, 10:00:00", "Corrida", "1800", "1810",
                 "5000", "30", "28", "80", "activities/100.fit.gz", "Corrida"],
                ["101", "2 de mar. de 2026, 10:00:00", "Corrida", "2100", "2105",
                 "6000", "31", "29", "82", "", "Corrida"],
            ]
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(fields)
            writer.writerows(rows)
            with zipfile.ZipFile(archive, "w") as out:
                out.writestr("activities.csv", buffer.getvalue())
                out.writestr("activities/100.fit.gz", _fit_record())
            before = sheet.read_bytes()
            preview = reconcile(archive, sheet)
            self.assertEqual(preview["matched"], 1)
            self.assertEqual(preview["added"], 1)
            self.assertEqual(before, sheet.read_bytes())

            reconcile(archive, sheet, apply=True)
            import openpyxl
            wb = openpyxl.load_workbook(sheet, read_only=True, data_only=True)
            ws = wb[SHEET_ACTIVITIES]
            header = _header_index(ws)
            self.assertEqual(ws.max_row, 3)
            self.assertEqual(ws.cell(2, header["distance_km"]).value, 5)
            self.assertEqual(ws.cell(2, header["avg_cadence"]).value, 160)
            self.assertEqual(ws.cell(2, header["vertical_ratio"]).value, 9.2)
            self.assertEqual(ws.cell(2, header["historical_source"]).value, "strava_export")
            self.assertEqual(ws.cell(3, header["activity_id"]).value, -101)
            self.assertEqual(ws.cell(3, header["historical_only"]).value, 1)
            wb.close()
            self.assertEqual(reconcile(archive, sheet)["added"], 0)


if __name__ == "__main__":
    unittest.main()
