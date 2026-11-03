import json
import unittest

from farol.garmin_client import fetch_activities


class _GarminActivitiesFake:
    def get_activities_by_date(self, _start, _end):
        return [{
            "activityId": 123,
            "activityName": "Resumo incompleto",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-09-08T06:00:00",
            "duration": 600,
            "distance": 1000,
            "averageSpeed": 1.0,
        }]

    def get_activity(self, activity_id):
        assert activity_id == 123
        return {
            "activityId": activity_id,
            "activityName": "Corrida detalhada",
            "activityTypeDTO": {"typeKey": "running"},
            "summaryDTO": {
                "startTimeLocal": "2026-09-08T06:01:00",
                "startLatitude": -8.1,
                "startLongitude": -34.9,
                "duration": 1200,
                "distance": 5000,
                "averageSpeed": 2.5,
                "maxSpeed": 3.0,
                "calories": 400,
                "trainingEffect": 3.2,
                "anaerobicTrainingEffect": 1.1,
                "averageHR": 150,
                "maxHR": 170,
                "averageRunCadence": 175,
                "strideLength": 80,
                "groundContactTime": 250,
                "verticalOscillation": 7.5,
                "elevationGain": 40,
                "elevationLoss": 35,
                "averagePower": 230,
                "maxPower": 320,
            },
        }

    def get_activity_splits(self, activity_id):
        assert activity_id == 123
        return {"activityId": activity_id, "lapDTOs": [{"distance": 1000}]}


class TestFetchActivities(unittest.TestCase):
    def test_detalhe_individual_tem_precedencia_e_grava_gps_e_voltas(self):
        activities = fetch_activities(_GarminActivitiesFake(), "2026-09-08", "2026-09-08")

        self.assertEqual(len(activities), 1)
        activity = activities[0]
        self.assertEqual(activity["name"], "Corrida detalhada")
        self.assertEqual(activity["date"], "2026-09-08")
        self.assertEqual(activity["distance_km"], 5.0)
        self.assertEqual(activity["duration_minutes"], 20.0)
        self.assertEqual(activity["avg_cadence"], 175)
        self.assertEqual(activity["gps"], "-8.1,-34.9")
        self.assertEqual(json.loads(activity["splits"])["activityId"], 123)


if __name__ == "__main__":
    unittest.main()
