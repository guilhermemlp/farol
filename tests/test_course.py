import unittest

from farol.course import CoursePoint, parse_gpx_xml, summarize_course


_GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
  <trkpt lat="-8.0000" lon="-34.0000"><ele>10</ele></trkpt>
  <trkpt lat="-8.0000" lon="-34.0010"><ele>11</ele></trkpt>
  <trkpt lat="-8.0000" lon="-34.0020"><ele>14</ele></trkpt>
  <trkpt lat="-8.0000" lon="-34.0030"><ele>12</ele></trkpt>
  <trkpt lat="-8.0000" lon="-34.0040"><ele>8</ele></trkpt>
</trkseg></trk></gpx>"""


class TestCourse(unittest.TestCase):
    def test_parseia_track_com_namespace_e_elevacao(self):
        points = parse_gpx_xml(_GPX_SAMPLE)
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0], CoursePoint(-8.0, -34.0, 10.0))

    def test_resumo_calcula_distancia_e_separa_ganho_bruto_do_filtrado(self):
        profile = summarize_course(parse_gpx_xml(_GPX_SAMPLE), noise_floor_m=3.0)
        self.assertAlmostEqual(profile.distance_km, 0.44, places=2)
        self.assertEqual(profile.elevation_min_m, 8.0)
        self.assertEqual(profile.elevation_max_m, 14.0)
        self.assertEqual(profile.raw_gain_m, 4.0)
        self.assertEqual(profile.raw_loss_m, 6.0)
        self.assertEqual(profile.filtered_gain_m, 4.0)
        self.assertEqual(profile.filtered_loss_m, 6.0)

    def test_sem_elevacao_nao_inventa_altimetria(self):
        points = [CoursePoint(-8.0, -34.0, None), CoursePoint(-8.0, -34.01, None)]
        profile = summarize_course(points)
        self.assertIsNone(profile.elevation_min_m)
        self.assertIsNone(profile.filtered_gain_m)

    def test_piso_de_ruido_precisa_ser_positivo(self):
        points = parse_gpx_xml(_GPX_SAMPLE)
        with self.assertRaises(ValueError):
            summarize_course(points, noise_floor_m=0)


if __name__ == "__main__":
    unittest.main()
