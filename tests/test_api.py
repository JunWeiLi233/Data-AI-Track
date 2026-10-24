import unittest

from backend import api


class RecommendationTests(unittest.TestCase):
    def test_query_strings_reach_the_optimizer(self):
        result = api.recommendations("eff", "2", along="0", spacing="0.8", budget="High")
        self.assertEqual(result["goal"], "eff")
        self.assertLessEqual(len(result["stations"]), 2)
        self.assertTrue(all(s["offline_km"] <= 3.0 for s in result["stations"]))

    def test_rejects_invalid_parameters(self):
        for kwargs in ({"goal": "speed"}, {"n": "0"}, {"n": "11"}, {"along": "maybe"},
                       {"spacing": "far"}, {"spacing": "-1"}, {"budget": "Huge"}):
            with self.assertRaises(ValueError, msg=kwargs):
                api.recommendations(**kwargs)


class MapLayerTests(unittest.TestCase):
    def test_tract_density_is_geojson_with_ordered_breaks(self):
        tracts = api.tract_density()
        self.assertEqual(tracts["type"], "FeatureCollection")
        self.assertEqual(tracts["breaks"], sorted(tracts["breaks"]))
        feature = next(f for f in tracts["features"] if f["properties"]["density"])
        self.assertIn(feature["geometry"]["type"], ("Polygon", "MultiPolygon"))
        self.assertGreater(feature["properties"]["density"], 0)

    def test_lines_and_deserts_are_geojson(self):
        lines = api.existing_lines()["features"]
        self.assertGreater(len(lines), 30)
        self.assertTrue(all(f["geometry"]["type"] == "MultiLineString" for f in lines))
        deserts = api.transit_deserts()
        self.assertGreater(deserts["population"], 0)
        self.assertIn(deserts["features"][0]["geometry"]["type"], ("Polygon", "MultiPolygon"))

    def test_existing_stations_are_inside_nyc(self):
        for station in api.existing_stations()["stations"]:
            self.assertTrue(40.4 < station["latitude"] < 41.0)
            self.assertTrue(-74.3 < station["longitude"] < -73.6)



class PlanningModelApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = api.planning_predictions()

    def test_every_tract_with_features_has_a_prediction(self):
        tracts = self.body["tracts"]
        geoids = {f["properties"]["geoid"] for f in api.tract_density()["features"]}
        self.assertGreater(len(tracts), 2100)
        self.assertTrue(set(tracts) <= geoids)
        for t in tracts.values():
            self.assertTrue(0 <= t["low"] <= t["high"] <= 1)
            self.assertTrue(0 <= t["probability"] <= 1)
            self.assertIn(t["reason"], [None, *self.body["reasons"]])
        # A few tracts have features but too few workers for a gold label.
        self.assertTrue(any(t["underserved"] is None for t in tracts.values()))

    def test_model_summary(self):
        model = self.body["model"]
        self.assertGreater(model["roc_auc"], 0.74)
        self.assertLess(model["brier"], model["brier_prevalence_only"])
        self.assertEqual([c["feature"] for c in model["coefficients"]], model["features"])
        self.assertEqual({b["borough"] for b in model["boroughs"]},
                         {"Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"})


if __name__ == "__main__":
    unittest.main()
