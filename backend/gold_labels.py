"""Gold labels for transit underservice, built from Census commute outcomes.

The scoring formula and the clustering model only see distance to a station,
population density and income. These labels come from something else: how long
residents actually take to get to work (ACS 2024 5-year table B08303). A tract
is labeled underserved (1) when its share of workers commuting 60 minutes or
more is in the top quarter of NYC tracts, else 0.

Car-free households (table B25044) are kept as a second, descriptive outcome;
they do not change the label.

Tracts with fewer than MIN_WORKERS workers get no label, because their shares
rest on too few people. Nothing is imputed.

ACS tract estimates are noisy. label_confident marks tracts whose share is
farther from the threshold than its 90% margin of error, so another survey
sample would very likely give the same label. About half the labels are not.

Refresh the source files and write the gold table, from the repository root:
    python -m backend.gold_labels --download   # once, ~130 MB read, ~1 MB kept
    python -m backend.gold_labels               # writes data/gold/gold_labels.csv
"""
import argparse
import io
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .data_loader import DEFAULT_DATA_DIR

SUMMARY_FILE_URL = ("https://www2.census.gov/programs-surveys/acs/summary_file/2024/"
                    "table-based-SF/data/5YRData/acsdt5y2024-{table}.dat")
NYC_TRACT_PREFIXES = tuple(f"1400000US36{county}" for county in ("005", "047", "061", "081", "085"))
TABLES = {"b08303": "commute_time.csv", "b25044": "vehicles.csv"}
DEFAULT_GOLD_PATH = Path(__file__).resolve().parents[1] / "data" / "gold" / "gold_labels.csv"
MIN_WORKERS = 100
LABEL_QUANTILE = 0.75


def fetch_acs_table(table, data_dir=DEFAULT_DATA_DIR, source=None):
    """Keep the NYC tract rows of one ACS summary file as a CSV in data_dir.

    source defaults to the Census Bureau URL; a local .dat path also works.
    Columns are renamed from B08303_E001 to the API style B08303_001E.
    """
    source = source or SUMMARY_FILE_URL.format(table=table)
    if str(source).startswith("https://"):
        with urllib.request.urlopen(source) as response:
            lines = io.TextIOWrapper(response, encoding="utf-8")
            kept = [next(lines)] + [line for line in lines if line.startswith(NYC_TRACT_PREFIXES)]
    else:
        with open(source, encoding="utf-8") as handle:
            kept = [next(handle)] + [line for line in handle if line.startswith(NYC_TRACT_PREFIXES)]
    frame = pd.read_csv(io.StringIO("".join(kept)), sep="|", dtype="string")
    frame.columns = [c if c == "GEO_ID" else f"{c[:6]}_{c[-3:]}{c[7]}" for c in frame.columns]
    path = Path(data_dir) / TABLES[table]
    frame.to_csv(path, index=False)
    return path


def _counts(path, columns):
    frame = pd.read_csv(path, dtype="string")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{Path(path).name}: missing columns {sorted(missing)}")
    result = pd.DataFrame({"GEOID": frame.GEO_ID.str.removeprefix("1400000US")})
    if not result.GEOID.str.fullmatch(r"\d{11}").all() or result.GEOID.duplicated().any():
        raise ValueError(f"{Path(path).name}: expected unique 11-digit tract GEOIDs")
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        # Summary files mark suppressed estimates with negative sentinels.
        result[column] = values.where(values.ge(0))
    return result


def build_gold_labels(data_dir=DEFAULT_DATA_DIR, *, min_workers=MIN_WORKERS, quantile=LABEL_QUANTILE):
    """Return one row per tract: worker and household counts, shares and the label.

    underserved is Int64: 1, 0, or missing when the tract has too few workers.
    attrs["gold_labels"] records the threshold actually used.
    """
    root = Path(data_dir)
    commute = _counts(root / TABLES["b08303"], ["B08303_001E", "B08303_012E", "B08303_013E",
                                                "B08303_001M", "B08303_012M", "B08303_013M"])
    vehicles = _counts(root / TABLES["b25044"], ["B25044_001E", "B25044_003E", "B25044_010E"])
    gold = commute.merge(vehicles, on="GEOID", how="outer", validate="one_to_one")
    gold["workers"] = gold.B08303_001E
    gold["long_commute_share"] = (gold.B08303_012E + gold.B08303_013E) / gold.workers.where(gold.workers > 0)
    # Census Bureau formula for the 90% margin of a derived proportion; when the
    # term under the root is negative, the bureau uses the ratio formula instead.
    long_moe2 = gold.B08303_012M ** 2 + gold.B08303_013M ** 2
    proportion = long_moe2 - gold.long_commute_share ** 2 * gold.B08303_001M ** 2
    ratio = long_moe2 + gold.long_commute_share ** 2 * gold.B08303_001M ** 2
    gold["long_commute_share_moe"] = np.sqrt(proportion.where(proportion >= 0, ratio)) / gold.workers.where(gold.workers > 0)
    gold["households"] = gold.B25044_001E
    gold["car_free_share"] = (gold.B25044_003E + gold.B25044_010E) / gold.households.where(gold.households > 0)
    labeled = gold.workers.ge(min_workers) & gold.long_commute_share.notna()
    threshold = float(gold.long_commute_share[labeled].quantile(quantile))
    label = pd.Series(pd.NA, index=gold.index, dtype="Int64")
    label[labeled] = gold.long_commute_share[labeled].ge(threshold).astype("int64")
    gold["underserved"] = label
    # Confident: the threshold lies outside the share's 90% margin of error.
    gold["label_confident"] = labeled & (gold.long_commute_share - threshold).abs().gt(gold.long_commute_share_moe)
    gold = gold[["GEOID", "workers", "long_commute_share", "long_commute_share_moe", "households",
                 "car_free_share", "underserved", "label_confident"]]
    gold = gold.sort_values("GEOID").reset_index(drop=True)
    gold.attrs["gold_labels"] = {
        "source": "ACS 2024 5-year B08303 (commute time) and B25044 (vehicles)",
        "label": f"long_commute_share >= {quantile:.0%} quantile",
        "threshold": threshold, "min_workers": min_workers,
        "labeled_tracts": int(labeled.sum()), "positive_tracts": int(label.eq(1).sum()),
        "unlabeled_tracts": int((~labeled).sum()), "confident_tracts": int(gold.label_confident.sum()),
    }
    return gold


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="refresh the ACS source CSVs first")
    parser.add_argument("--output", type=Path, default=DEFAULT_GOLD_PATH)
    args = parser.parse_args()
    if args.download:
        for table in TABLES:
            print("wrote", fetch_acs_table(table))
    gold = build_gold_labels()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    gold.to_csv(args.output, index=False)
    info = gold.attrs["gold_labels"]
    print(f"wrote {args.output}: {info['labeled_tracts']} labeled tracts, {info['positive_tracts']} underserved "
          f"(60+ min commute share >= {info['threshold']:.3f}), {info['confident_tracts']} confident, "
          f"{info['unlabeled_tracts']} unlabeled")


if __name__ == "__main__":
    main()
