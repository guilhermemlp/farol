import unittest

from farol import nutrition


class TestNutrition(unittest.TestCase):
    def test_plano_conta_pre_e_substitui_um_gel_por_ultra(self):
        config = nutrition.config_from_profile({"race_nutrition": {
            "gel_carbohydrate_g": 27, "gel_caffeine_mg": 75, "interval_minutes": 30,
            "pre_start_minutes": 15, "caffeine_at_minutes": 60,
            "last_gel_cutoff_minutes": 15, "coffee_before_race": True,
        }})
        plan = nutrition.build_plan(128, config, "18:00")
        self.assertEqual([event["minute"] for event in plan["events"]], [-15, 30, 60, 90])
        self.assertEqual(plan["events"][2]["type"], "Ultra Gel")
        self.assertEqual(plan["total_carbs_g"], 108.0)
        self.assertEqual(plan["total_caffeine_mg"], 75.0)
        self.assertEqual(plan["events"][0]["clock"], "17:45")
