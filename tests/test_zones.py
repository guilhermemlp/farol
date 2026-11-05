"""Testes de farol.domain.zones: zonas de FC por LTHR (preferido) ou Karvonen
(fallback). Sem teste antes desta auditoria (2026-09-04)."""
import unittest

from farol.domain.zones import ZONE_NAMES, heart_rate_zones


class TestHeartRateZones(unittest.TestCase):
    def test_usa_lthr_quando_perfil_tem_lthr_e_floors_completos(self):
        perfil = {
            "hr_max": 192, "hr_rest": 68, "lthr": 182,
            "hr_zone_floors": {"z1": 118, "z2": 155, "z3": 164, "z4": 173},
        }
        zonas = heart_rate_zones(perfil)
        self.assertEqual(len(zonas), 5)
        self.assertEqual(zonas[0]["hr_min"], 118)   # z1 floor
        self.assertEqual(zonas[3]["hr_max"], 182)   # z4 teto = lthr
        self.assertEqual(zonas[4]["hr_max"], 192)   # z5 teto = hr_max
        self.assertEqual([z["name"] for z in zonas], ZONE_NAMES)

    def test_cai_para_karvonen_sem_lthr(self):
        perfil = {"hr_max": 190, "hr_rest": 60}
        zonas = heart_rate_zones(perfil)
        self.assertEqual(len(zonas), 5)
        # Z1 comeca em 50% da reserva (Karvonen), nao no hr_rest cru
        reserva = 190 - 60
        self.assertEqual(zonas[0]["hr_min"], round(60 + reserva * 0.50))
        self.assertEqual(zonas[4]["hr_max"], round(60 + reserva * 1.00))

    def test_cai_para_karvonen_com_floors_incompletos(self):
        # lthr presente mas floors faltando um z -- nao deve usar _zones_from_lthr
        perfil = {"hr_max": 190, "hr_rest": 60, "lthr": 175,
                  "hr_zone_floors": {"z1": 110, "z2": 150, "z3": 160}}  # falta z4
        zonas = heart_rate_zones(perfil)
        reserva = 190 - 60
        self.assertEqual(zonas[0]["hr_min"], round(60 + reserva * 0.50))

    def test_zonas_sao_contiguas_e_crescentes(self):
        perfil = {
            "hr_max": 192, "hr_rest": 68, "lthr": 182,
            "hr_zone_floors": {"z1": 118, "z2": 155, "z3": 164, "z4": 173},
        }
        zonas = heart_rate_zones(perfil)
        for i in range(4):
            self.assertEqual(zonas[i]["hr_max"], zonas[i + 1]["hr_min"])
            self.assertLess(zonas[i]["hr_min"], zonas[i]["hr_max"])


if __name__ == "__main__":
    unittest.main()
