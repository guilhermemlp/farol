import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from farol.insights.course import CoursePoint
from farol.insights.race_conditions import fetch_route_elevation, fetch_weather, sample_route


class TestRaceConditions(unittest.TestCase):
    def setUp(self):
        self.points = [
            CoursePoint(-8.0, -34.0, 10.0),
            CoursePoint(-8.0, -34.01, 11.0),
            CoursePoint(-8.0, -34.02, 12.0),
        ]

    def test_amostra_inicio_fim_e_marcadores_regulares(self):
        samples = sample_route(self.points, every_m=500)
        self.assertEqual(samples[0].distance_km, 0.0)
        self.assertGreater(samples[-1].distance_km, 2.0)
        self.assertAlmostEqual(samples[1].distance_km, 0.5)

    def test_elevacao_e_buscada_em_lotes_e_na_mesma_quantidade(self):
        samples = sample_route(self.points, every_m=100)
        urls = []

        def fetcher(url):
            urls.append(url)
            count = len(parse_qs(urlparse(url).query)["latitude"][0].split(","))
            return {"elevation": list(range(count))}

        elevations = fetch_route_elevation(samples, fetcher)
        self.assertEqual(elevations, [float(index) for index in range(len(samples))])
        self.assertEqual(len(urls), 1)

    def test_clima_usa_inicio_meio_fim_e_hora_da_largada(self):
        samples = sample_route(self.points, every_m=500)
        timestamp = "2026-09-19T18:00"

        def fetcher(url):
            self.assertIn("timezone=America%2FFortaleza", url)
            hourly = {"time": [timestamp]}
            for key in ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m",
                        "precipitation", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                        "cloud_cover", "shortwave_radiation"):
                hourly[key] = [25]
            return [
                {"hourly": hourly}, {"hourly": hourly}, {"hourly": hourly},
            ]

        weather = fetch_weather(
            samples, datetime(2026, 9, 19, 18, tzinfo=timezone(timedelta(hours=-3))), 128.4, fetcher)
        self.assertEqual([point.label for point in weather], ["inicio", "meio", "fim"])
        self.assertEqual(weather[0].temperature_c, 25.0)
        self.assertEqual([point.time for point in weather], ["2026-09-19T18:00", "2026-09-19T19:00", "2026-09-19T20:00"])

    def test_clima_rejeita_horario_sem_fuso_ou_fora_de_fortaleza(self):
        samples = sample_route(self.points, every_m=500)
        with self.assertRaises(ValueError):
            fetch_weather(samples, datetime(2026, 9, 19, 18), 128.4)
        with self.assertRaises(ValueError):
            fetch_weather(samples, datetime(2026, 9, 19, 18, tzinfo=timezone.utc), 128.4)


if __name__ == "__main__":
    unittest.main()
