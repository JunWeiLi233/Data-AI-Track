"""Where each tract's residents work, and how far that is by subway.

Source: LEHD LODES 2023 origin-destination flows (all jobs, JT00), which come
from unemployment-insurance records, not from the ACS commute questions behind
the gold labels. data/raw/lodes_flows.csv.gz holds one row per NYC home tract
and work tract with the job count; jobs outside NYC keep only their state
("outside_34" is New Jersey). It covers work in New York State (ny main file)
plus New Jersey and Connecticut (their aux files). LODES adds noise to small
counts on purpose, so single flows are fuzzy but tract totals are sound.

Features per home tract, from tract_minutes_matrix (backend/travel_time.py):
- job_minutes_mean: jobs-weighted mean subway minutes to NYC work tracts;
- job_share_over_45 / job_share_over_60: share of NYC jobs farther than that;
- job_share_manhattan: share of all jobs in Manhattan;
- job_share_outside_nyc: share of all jobs outside the five boroughs.

any_mode_job_features gives the first three again with bus as well as subway
trips (backend/bus_network.py), prefixed any_mode_.

Refresh the flows, from the repository root (reads ~55 MB, keeps ~3.4 MB):
    python -m backend.job_destinations --download
"""
import argparse
import io
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .data_loader import DEFAULT_DATA_DIR
from .bus_network import multimodal_minutes_matrix
from .travel_time import tract_minutes_matrix

LODES_URL = "https://lehd.ces.census.gov/data/lodes/LODES8/{state}/od/{state}_od_{part}_JT00_2023.csv.gz"
LODES_FILES = (("ny", "main"), ("nj", "aux"), ("ct", "aux"))
NYC_COUNTIES = ("36005", "36047", "36061", "36081", "36085")
FLOWS_FILE = "lodes_flows.csv.gz"
FEATURES = ("job_minutes_mean", "job_share_over_45", "job_share_over_60",
            "job_share_manhattan", "job_share_outside_nyc")
ANY_MODE_FEATURES = ("any_mode_job_minutes_mean", "any_mode_job_share_over_45", "any_mode_job_share_over_60")


def fetch_lodes_flows(data_dir=DEFAULT_DATA_DIR, sources=None):
    """Aggregate LODES block flows to NYC home tract -> work tract and write them.

    sources maps (state, part) to a local .csv.gz path; missing ones download.
    """
    sources = sources or {}
    parts = []
    for state, part in LODES_FILES:
        source = sources.get((state, part)) or LODES_URL.format(state=state, part=part)
        handle = urllib.request.urlopen(source) if str(source).startswith("https://") else open(source, "rb")
        with handle:
            chunks = pd.read_csv(io.BytesIO(handle.read()), compression="gzip", usecols=["w_geocode", "h_geocode", "S000"],
                                 dtype={"w_geocode": "string", "h_geocode": "string"}, chunksize=2_000_000)
            for chunk in chunks:
                chunk = chunk[chunk.h_geocode.str[:5].isin(NYC_COUNTIES)]
                work = chunk.w_geocode.str[:11]
                chunk = chunk.assign(home=chunk.h_geocode.str[:11],
                                     work=work.where(work.str[:5].isin(NYC_COUNTIES), "outside_" + work.str[:2]))
                parts.append(chunk.groupby(["home", "work"]).S000.sum())
    flows = pd.concat(parts).groupby(level=[0, 1]).sum().rename("jobs").reset_index()
    path = Path(data_dir) / FLOWS_FILE
    flows.to_csv(path, index=False)
    return path


def load_flows(path=DEFAULT_DATA_DIR / FLOWS_FILE):
    flows = pd.read_csv(path, dtype={"home": "string", "work": "string"})
    if not {"home", "work", "jobs"}.issubset(flows.columns):
        raise ValueError(f"{Path(path).name}: expected home, work, jobs columns")
    if flows.duplicated(["home", "work"]).any():
        raise ValueError(f"{Path(path).name}: duplicate home-work pairs")
    return flows


def _minutes_to_jobs(tracts, minutes, flows):
    """Jobs-weighted mean minutes and shares over 45 / 60 minutes, per home GEOID."""
    position = pd.Series(np.arange(len(tracts)), index=tracts.GEOID.to_numpy())
    inside = flows[flows.work.isin(position.index)].copy()
    inside["minutes"] = minutes[position[inside.home].to_numpy(), position[inside.work].to_numpy()]
    inside = inside[np.isfinite(inside.minutes)]
    weighted = inside.assign(product=inside.minutes * inside.jobs,
                             over_45=inside.jobs.where(inside.minutes > 45, 0),
                             over_60=inside.jobs.where(inside.minutes > 60, 0))
    by_home = weighted.groupby("home")[["jobs", "product", "over_45", "over_60"]].sum()
    return pd.DataFrame({"job_minutes_mean": by_home["product"] / by_home.jobs,
                         "job_share_over_45": by_home.over_45 / by_home.jobs,
                         "job_share_over_60": by_home.over_60 / by_home.jobs})


def _home_flows(tracts, flows):
    flows = load_flows() if flows is None else flows
    return flows[flows.home.isin(set(tracts.GEOID)) & flows.jobs.gt(0)]


def job_destination_features(tracts, data, flows=None):
    """DataFrame of FEATURES indexed like tracts; NaN where a tract has no flows or point."""
    flows = _home_flows(tracts, flows)
    total = flows.groupby("home").jobs.sum()
    features = _minutes_to_jobs(tracts, tract_minutes_matrix(tracts, data), flows).reindex(total.index)
    manhattan = flows[flows.work.str[:5].eq("36061")].groupby("home").jobs.sum()
    outside = flows[flows.work.str.startswith("outside_")].groupby("home").jobs.sum()
    features["job_share_manhattan"] = manhattan.reindex(total.index).fillna(0) / total
    features["job_share_outside_nyc"] = outside.reindex(total.index).fillna(0) / total
    return features[list(FEATURES)].reindex(tracts.GEOID.to_numpy()).set_axis(tracts.index)


def any_mode_job_features(tracts, data, flows=None, bus=None):
    """ANY_MODE_FEATURES: the minute-based features, by bus or subway (backend/bus_network.py)."""
    flows = _home_flows(tracts, flows)
    features = _minutes_to_jobs(tracts, multimodal_minutes_matrix(tracts, data, bus), flows)
    features.columns = list(ANY_MODE_FEATURES)
    return features.reindex(tracts.GEOID.to_numpy()).set_axis(tracts.index)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="rebuild the flows from LODES")
    args = parser.parse_args()
    if args.download:
        print("wrote", fetch_lodes_flows())
    flows = load_flows()
    print(f"{len(flows)} home-work tract pairs, {flows.jobs.sum()} jobs, {flows.home.nunique()} home tracts")


if __name__ == "__main__":
    main()
