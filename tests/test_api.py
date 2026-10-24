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


if __name__ == "__main__":
    unittest.main()
