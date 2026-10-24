import unittest

import numpy as np

from backend import network, optimizer


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx = optimizer.context()
        cls.stations, cls.lines = ctx.stations, ctx.lines

    def test_every_station_is_on_a_line(self):
        on_lines = set().union(*(set(line.stations) for line in self.lines))
        self.assertEqual(on_lines, set(range(len(self.stations))))

    def test_named_lines_are_connected_trees(self):
        for line in self.lines:
            with self.subTest(line=line.name):
                self.assertEqual(len(line.edges), len(line.stations) - 1)

    def test_known_lines_use_their_routes(self):
        by_name = {line.name: line for line in self.lines}
        self.assertEqual(by_name["Flushing"].routes[0], "7")
        self.assertEqual(by_name["Flushing"].color, "#B933AD")
        self.assertEqual(by_name["Staten Island Railway"].routes, ["SIR"])
        self.assertEqual(by_name["42nd St Shuttle"].routes[0], "GS")

    def test_flushing_line_links_consecutive_stations(self):
        names = self.stations.stop_name.tolist()
        flushing = next(line for line in self.lines if line.name == "Flushing")
        pairs = {frozenset((names[i], names[j])) for i, j in flushing.edges}
        self.assertIn(frozenset(("Flushing - Main St", "Mets - Willets Point")), pairs)
        self.assertIn(frozenset(("Vernon Blvd - Jackson Av", "Hunters Point Av")), pairs)


class OptimizerTests(unittest.TestCase):
    def _on_segment_km(self, station):
        """Distance from a placed site to the nearest existing line edge (planar approximation)."""
        ctx = optimizer.context()
        lat = ctx.stations.latitude.to_numpy("float64")
        lon = ctx.stations.longitude.to_numpy("float64")
        scale = np.cos(np.radians(40.7))
        p = np.array([station["longitude"] * scale, station["latitude"]])
        best = np.inf
        for line in ctx.lines:
            for i, j in line.edges:
                a, b = np.array([lon[i] * scale, lat[i]]), np.array([lon[j] * scale, lat[j]])
                t = np.clip(np.dot(p - a, b - a) / np.dot(b - a, b - a), 0, 1)
                best = min(best, np.linalg.norm(p - (a + t * (b - a))) * 111.2)
        return best

    def test_along_lines_places_infill_on_existing_track(self):
        result = optimizer.optimize("access", 5, along_lines=True)
        self.assertEqual(len(result["stations"]), 5)
        for station in result["stations"]:
            self.assertTrue(station["on_existing_line"])
            self.assertEqual(station["offline_km"], 0)
            self.assertLess(self._on_segment_km(station), 0.01)
        self.assertEqual(result["impact"]["on_existing_lines"], 5)

    def test_spacing_from_existing_and_new_stations(self):
        result = optimizer.optimize("eff", 6, along_lines=False, min_spacing_km=1.0)
        ctx = optimizer.context()
        sites = result["stations"]
        for k, site in enumerate(sites):
            to_existing = network.haversine_km(site["latitude"], site["longitude"],
                                               ctx.stations.latitude.to_numpy("float64"),
                                               ctx.stations.longitude.to_numpy("float64"))
            self.assertGreaterEqual(to_existing.min(), 1.0)
            for other in sites[:k]:
                self.assertGreaterEqual(float(network.haversine_km(
                    site["latitude"], site["longitude"], other["latitude"], other["longitude"])), 1.0)

    def test_budget_caps_new_track(self):
        for budget, cap in optimizer.BUDGET_OFFLINE_KM.items():
            result = optimizer.optimize("access", 5, along_lines=False, budget=budget)
            self.assertTrue(all(s["offline_km"] <= cap for s in result["stations"]), budget)

    def test_impact_improves_coverage_and_matches_station_totals(self):
        result = optimizer.optimize("access", 3)
        impact = result["impact"]
        self.assertGreater(impact["covered_after"], impact["covered_before"])
        self.assertLessEqual(impact["walk_minutes_after"], impact["walk_minutes_before"])
        self.assertEqual(impact["residents_added"], sum(s["residents"] for s in result["stations"]))

    def test_greedy_ranking_is_deterministic(self):
        first = optimizer.optimize("eff", 4)["stations"]
        second = optimizer.optimize("eff", 4)["stations"]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
