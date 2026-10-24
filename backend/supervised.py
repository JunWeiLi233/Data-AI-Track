"""Supervised models scored against the gold labels, next to the existing methods.

Every method ranks tracts from most to least likely underserved, and each
ranking is checked against gold_labels.underserved.

Single-number rankings, no training:
- priority_score: Emily's rule-based formula;
- distance_only: nearest station distance;
- transit_minutes_only: estimated subway trip to the job centers
  (backend/travel_time.py);
- job_share_over_45_only: share of residents' jobs over 45 subway minutes
  away (backend/job_destinations.py).
- cluster: the k-means groups, ranked by their underserved rate in the
  training folds only.

Trained models, one per FEATURE_SETS entry, with out-of-fold predictions;
ensemble averages the logistic regression and gradient boosting ranks.
Hyperparameters are chosen by an inner grouped search inside each training
fold, so the test fold never influences them.

Folds are grouped by community district (CDTA2020), so a model never trains on
a tract's next-door neighbours and is then tested on it. Random ranking scores
ROC AUC 0.5 and precision at top quarter equal to the underserved rate.

Run from the repository root: python -m backend.supervised
"""
import argparse

import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .clustering import cluster_tracts
from .data_loader import load_data
from .gold_labels import build_gold_labels
from .job_destinations import FEATURES as JOB_DESTINATION_FEATURES, job_destination_features
from .scoring import score_tracts
from .travel_time import transit_minutes_to_jobs

# Column -> transform. Counts and distances are skewed, so they are logged.
CLUSTERING_FEATURES = {"nearest_station_distance_km": "log1p", "population_density_km2": "log",
                       "median_household_income": "log"}
TRAVEL_FEATURES = {**CLUSTERING_FEATURES, "transit_minutes_to_jobs": "log"}
JOB_FEATURES = {**TRAVEL_FEATURES, "job_minutes_mean": "log", "job_share_over_45": "raw",
                "job_share_over_60": "raw", "job_share_manhattan": "raw", "job_share_outside_nyc": "raw"}
FEATURE_SETS = {
    "clustering_features": CLUSTERING_FEATURES,
    "plus_travel_time": TRAVEL_FEATURES,
    "plus_job_destinations": JOB_FEATURES,
    # Where the tract is: lets a model learn patterns the network misses
    # (express buses, ferries, car commuting), at some cost to explanation.
    "plus_location": {**JOB_FEATURES, "BoroName": "onehot", "latitude": "raw", "longitude": "raw"},
}
BOROUGHS = ("Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island")
DEFAULT_FEATURE_SET = "plus_location"
MODELS = {
    "logistic_regression": GridSearchCV(
        make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
        {"logisticregression__C": [0.01, 0.1, 1.0, 10.0]}, scoring="roc_auc"),
    "gradient_boosting": GridSearchCV(
        HistGradientBoostingClassifier(learning_rate=0.05, min_samples_leaf=40, random_state=0),
        {"max_depth": [2, 3], "max_iter": [100, 300]}, scoring="roc_auc"),
}
DEFAULT_SPLITS = 5
INNER_SPLITS = 3
TOP_SHARE = 0.25


def feature_matrix(frame, feature_set=DEFAULT_FEATURE_SET):
    columns = []
    for column, transform in FEATURE_SETS[feature_set].items():
        if transform == "onehot":
            columns += [frame[column].eq(borough).to_numpy(dtype="float64") for borough in BOROUGHS]
            continue
        values = frame[column].astype("float64").to_numpy()
        columns.append({"log": np.log, "log1p": np.log1p, "raw": np.asarray}[transform](values))
    return np.column_stack(columns)


def feature_frame(data=None, gold=None, flows=None):
    """Every tract with clustering, travel and job features, left-joined to gold labels."""
    data = load_data() if data is None else data
    gold = build_gold_labels() if gold is None else gold
    clustered = cluster_tracts(score_tracts(data))
    clustered["transit_minutes_to_jobs"] = transit_minutes_to_jobs(clustered, data)
    clustered = clustered.join(job_destination_features(clustered, data, flows))
    return clustered.merge(gold, on="GEOID", how="left", validate="one_to_one")


def has_features(frame):
    """Rows with every feature any method uses, labeled or not."""
    return (frame.cluster.notna() & frame.priority_score.notna() & frame.CDTA2020.notna()
            & frame.transit_minutes_to_jobs.gt(0) & frame.job_minutes_mean.gt(0)
            & frame[list(JOB_DESTINATION_FEATURES)].notna().all(axis=1))


def training_frame(data=None, gold=None, flows=None, frame=None):
    """feature_frame rows with a gold label and every feature; pass frame to reuse one."""
    frame = feature_frame(data, gold, flows) if frame is None else frame
    usable = has_features(frame) & frame.underserved.notna()
    return frame[usable].sort_values("GEOID").reset_index(drop=True)


def _precision_at_top(y, score, share=TOP_SHARE):
    top = max(1, int(round(len(y) * share)))
    order = np.argsort(-score, kind="stable")[:top]
    return float(y[order].mean())


def grouped_folds(frame, n_splits=DEFAULT_SPLITS):
    """(train, test) position arrays; no community district is on both sides."""
    y = frame.underserved.astype("int64").to_numpy()
    return list(GroupKFold(n_splits=n_splits).split(frame, y, frame.CDTA2020.to_numpy()))


def fit_model(name, x, y, groups):
    """Fit MODELS[name], tuning it with folds grouped like the outer ones."""
    inner = list(GroupKFold(n_splits=INNER_SPLITS).split(x, y, groups))
    return clone(MODELS[name]).set_params(cv=inner).fit(x, y)


def out_of_fold_scores(frame, n_splits=DEFAULT_SPLITS, feature_sets=tuple(FEATURE_SETS)):
    """Return a DataFrame of out-of-fold ranking scores, one column per method."""
    y = frame.underserved.astype("int64").to_numpy()
    groups = frame.CDTA2020.to_numpy()
    matrices = {name: feature_matrix(frame, name) for name in feature_sets}
    cluster = frame.cluster.astype("int64").to_numpy()
    scores = {"priority_score": frame.priority_score.to_numpy(dtype="float64"),
              "distance_only": frame.nearest_station_distance_km.to_numpy(dtype="float64"),
              "transit_minutes_only": frame.transit_minutes_to_jobs.to_numpy(dtype="float64"),
              "job_share_over_45_only": frame.job_share_over_45.to_numpy(dtype="float64"),
              "cluster": np.full(len(frame), np.nan)}
    for feature_set in feature_sets:
        for model in MODELS:
            scores[f"{model}:{feature_set}"] = np.full(len(frame), np.nan)
    for train, test in grouped_folds(frame, n_splits):
        rates = pd.Series(y[train]).groupby(cluster[train]).mean()
        scores["cluster"][test] = pd.Series(cluster[test]).map(rates).fillna(y[train].mean()).to_numpy()
        for feature_set, x in matrices.items():
            for name in MODELS:
                fitted = fit_model(name, x[train], y[train], groups[train])
                scores[f"{name}:{feature_set}"][test] = fitted.predict_proba(x[test])[:, 1]
    for feature_set in feature_sets:
        # Ranks are pooled over all folds; fine for AUC, which only compares ranks.
        scores[f"ensemble:{feature_set}"] = (rankdata(scores[f"logistic_regression:{feature_set}"])
                                             + rankdata(scores[f"gradient_boosting:{feature_set}"])) / 2
    return pd.DataFrame(scores, index=frame.index)


def evaluate(frame=None, n_splits=DEFAULT_SPLITS, feature_sets=tuple(FEATURE_SETS)):
    """One row per method: ROC AUC, average precision and precision at top quarter.

    roc_auc_confident scores only tracts whose label is outside the ACS margin
    of error; models still train on every label.
    """
    frame = training_frame() if frame is None else frame
    y = frame.underserved.astype("int64").to_numpy()
    confident = frame.label_confident.to_numpy(dtype=bool)
    scores = out_of_fold_scores(frame, n_splits, feature_sets)
    rows = [{"method": name,
             "roc_auc": roc_auc_score(y, score),
             "roc_auc_confident": roc_auc_score(y[confident], score[confident]),
             "average_precision": average_precision_score(y, score),
             "precision_at_top_quarter": _precision_at_top(y, score.to_numpy())}
            for name, score in scores.items()]
    result = pd.DataFrame(rows).sort_values("roc_auc", ascending=False).reset_index(drop=True)
    result.attrs["evaluation"] = {"tracts": len(frame), "underserved_rate": float(y.mean()),
                                  "confident_tracts": int(confident.sum()),
                                  "n_splits": n_splits, "groups": "CDTA2020"}
    return result


def cluster_gold_summary(frame):
    """One row per k-means group: how often the gold label calls it underserved."""
    return (frame.groupby("cluster")
            .agg(cluster_name=("cluster_name", "first"), tracts=("GEOID", "size"),
                 underserved_rate=("underserved", "mean"),
                 median_long_commute_share=("long_commute_share", "median"),
                 median_transit_minutes=("transit_minutes_to_jobs", "median"))
            .reset_index())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--splits", type=int, default=DEFAULT_SPLITS)
    args = parser.parse_args()
    frame = training_frame()
    result = evaluate(frame, args.splits)
    info = result.attrs["evaluation"]
    print(f"{info['tracts']} tracts ({info['confident_tracts']} confident), {info['underserved_rate']:.1%} "
          f"underserved by the gold label, {info['n_splits']} folds grouped by {info['groups']}")
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(result.round(3).to_string(index=False))
        print()
        print(cluster_gold_summary(frame).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
