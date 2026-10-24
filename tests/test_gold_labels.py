import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.gold_labels import build_gold_labels, fetch_acs_table


def write_tables(root, rows):
    """rows: (geoid, workers, commute_60_89, commute_90_plus, households, car_free)."""
    pd.DataFrame({
        "GEO_ID": [f"1400000US{r[0]}" for r in rows],
        "B08303_001E": [r[1] for r in rows], "B08303_012E": [r[2] for r in rows],
        "B08303_013E": [r[3] for r in rows],
        "B08303_001M": [10] * len(rows), "B08303_012M": [10] * len(rows), "B08303_013M": [0] * len(rows),
    }).to_csv(Path(root) / "commute_time.csv", index=False)
    pd.DataFrame({
        "GEO_ID": [f"1400000US{r[0]}" for r in rows],
        "B25044_001E": [r[4] for r in rows], "B25044_003E": [r[5] for r in rows],
        "B25044_010E": [0] * len(rows),
    }).to_csv(Path(root) / "vehicles.csv", index=False)


class SyntheticGoldLabelTests(unittest.TestCase):
    def test_top_quarter_labeled_and_small_tracts_unlabeled(self):
        rows = [(f"360050{i:05d}", 1000, i * 50, 0, 500, 100) for i in range(8)]
        rows.append(("36005099999", 20, 20, 0, 10, 0))
        with tempfile.TemporaryDirectory() as root:
            write_tables(root, rows)
            gold = build_gold_labels(root).set_index("GEOID")
        self.assertEqual(gold.underserved.iloc[:8].tolist(), [0, 0, 0, 0, 0, 0, 1, 1])
        self.assertTrue(pd.isna(gold.loc["36005099999", "underserved"]))
        self.assertAlmostEqual(gold.loc["36005000002", "long_commute_share"], 0.1)
        self.assertAlmostEqual(gold.loc["36005000002", "car_free_share"], 0.2)
        info = gold.attrs["gold_labels"]
        self.assertEqual((info["labeled_tracts"], info["positive_tracts"], info["unlabeled_tracts"]), (8, 2, 1))
        # Shares are i/20 and the cutoff is 0.2625; each margin is about 0.01.
        self.assertTrue(gold.label_confident.iloc[:8].all())
        self.assertFalse(gold.loc["36005099999", "label_confident"])

    def test_share_margin_of_error(self):
        rows = [(f"360050{i:05d}", 1000, 300 + i, 0, 500, 100) for i in range(4)]
        with tempfile.TemporaryDirectory() as root:
            write_tables(root, rows)
            gold = build_gold_labels(root)
        # sqrt(10² - 0.3² × 10²) / 1000
        self.assertAlmostEqual(gold.long_commute_share_moe.iloc[0], (100 - 0.09 * 100) ** 0.5 / 1000)
        # Shares 0.300-0.303 all sit inside their margin of the cutoff.
        self.assertFalse(gold.label_confident.any())

    def test_negative_sentinels_are_missing(self):
        rows = [(f"360050{i:05d}", 1000, 100, 0, 500, 100) for i in range(4)]
        rows.append(("36005099999", -666666666, 100, 0, 500, 100))
        with tempfile.TemporaryDirectory() as root:
            write_tables(root, rows)
            gold = build_gold_labels(root).set_index("GEOID")
        self.assertTrue(pd.isna(gold.loc["36005099999", "workers"]))
        self.assertTrue(pd.isna(gold.loc["36005099999", "underserved"]))

    def test_fetch_keeps_nyc_tracts_and_renames_columns(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "b08303.dat"
            source.write_text("GEO_ID|B08303_E001|B08303_M001\n"
                              "0100000US|5|1\n"
                              "1400000US36005000100|7|2\n"
                              "1400000US36001000100|9|3\n"
                              "1400000US36085000100|11|4\n")
            path = fetch_acs_table("b08303", root, source)
            frame = pd.read_csv(path, dtype="string")
        self.assertEqual(frame.columns.tolist(), ["GEO_ID", "B08303_001E", "B08303_001M"])
        self.assertEqual(frame.GEO_ID.tolist(), ["1400000US36005000100", "1400000US36085000100"])


class RealGoldLabelTests(unittest.TestCase):
    def test_counts(self):
        gold = build_gold_labels()
        info = gold.attrs["gold_labels"]
        self.assertEqual(len(gold), 2327)
        self.assertEqual(info["labeled_tracts"] + info["unlabeled_tracts"], 2327)
        self.assertAlmostEqual(info["positive_tracts"] / info["labeled_tracts"], 0.25, places=2)
        self.assertTrue(gold.long_commute_share.dropna().between(0, 1).all())
        self.assertTrue(0.3 < info["confident_tracts"] / info["labeled_tracts"] < 0.7)


if __name__ == "__main__":
    unittest.main()
