import unittest

from backend import api


class RecommendationTests(unittest.TestCase):
    def test_ranks_by_goal_and_limits_count(self):
        access = api.recommendations("access", 3)["stations"]
        self.assertEqual([s["rank"] for s in access], [1, 2, 3])
        self.assertEqual([s["score"] for s in access], sorted((s["score"] for s in access), reverse=True))
        eff = api.recommendations("eff", 5)["stations"]
        impact = [s["minutes_saved"] * s["residents"] for s in eff]
        self.assertEqual(impact, sorted(impact, reverse=True))

    def test_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            api.recommendations("speed", 3)
        for n in (0, 6):
            with self.assertRaises(ValueError):
                api.recommendations("access", n)


class MapLayerTests(unittest.TestCase):
    def test_tract_density_is_geojson_with_ordered_breaks(self):
        tracts = api.tract_density()
        self.assertEqual(tracts["type"], "FeatureCollection")
        self.assertEqual(tracts["breaks"], sorted(tracts["breaks"]))
        feature = next(f for f in tracts["features"] if f["properties"]["density"])
        self.assertIn(feature["geometry"]["type"], ("Polygon", "MultiPolygon"))
        self.assertGreater(feature["properties"]["density"], 0)

    def test_existing_stations_are_inside_nyc(self):
        for station in api.existing_stations()["stations"]:
            self.assertTrue(40.4 < station["latitude"] < 41.0)
            self.assertTrue(-74.3 < station["longitude"] < -73.6)


if __name__ == "__main__":
    unittest.main()
