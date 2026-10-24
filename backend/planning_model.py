"""The model to use for planning: small, calibrated, with uncertainty and reasons.

backend.supervised compares many methods; this module fits the one picked for
trust over the last bit of accuracy. It is a logistic regression on:
- log distance to the nearest station;
- log population density;
- share of residents' jobs over 45 minutes away by bus or subway
  (job_destinations.any_mode_job_features);
- borough (Manhattan is the baseline).

Why these: the nine-feature model scores ROC AUC 0.77 against 0.76 here, but
its travel features are near copies of each other (correlation up to 0.97), so
its coefficients flip sign between resamples. Every coefficient here keeps
its sign when community districts are resampled. Borough terms fix the
largest calibration error: without them Staten Island is predicted 50%
underserved against 29% observed.

Distance has a negative coefficient: among tracts whose jobs are equally far
away, those farther from a station have fewer long commutes. They are also
the tracts with the most cars, so residents drive instead.

Outputs per tract: the probability of being underserved, a 90% interval from
refitting on resampled community districts, each feature's contribution to
the log-odds and the tract feature that raises it most, so a planner can see
why a tract was flagged. out_of_fold_probability is the prediction made
without that tract's community district, for honest checking.

Run from the repository root: python -m backend.planning_model
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .supervised import grouped_folds, training_frame

FEATURES = ("log_station_distance", "log_density", "job_share_over_45",
            "bronx", "brooklyn", "queens", "staten_island")
TRACT_FEATURES = 3  # the rest are borough indicators
BOROUGHS = {"bronx": "Bronx", "brooklyn": "Brooklyn", "queens": "Queens", "staten_island": "Staten Island"}
C = 1.0
BOOTSTRAPS = 200
INTERVAL = (5, 95)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "gold" / "planning_predictions.csv"


def planning_features(frame):
    """DataFrame of FEATURES, indexed like frame."""
    features = pd.DataFrame({
        "log_station_distance": np.log1p(frame.nearest_station_distance_km.astype("float64")),
        "log_density": np.log(frame.population_density_km2.astype("float64")),
        # Bus as well as subway: it ranks far-from-subway tracts better (backend README).
        "job_share_over_45": frame.any_mode_job_share_over_45.astype("float64"),
    }, index=frame.index)
    for column, borough in BOROUGHS.items():
        features[column] = frame.BoroName.eq(borough).astype("float64")
    return features[list(FEATURES)]


def _model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=1000))


def _district_bootstrap(frame, x, y, n, random_state):
    """Yield models refit on community districts drawn with replacement."""
    districts = frame.CDTA2020.to_numpy()
    rows = {district: np.flatnonzero(districts == district) for district in np.unique(districts)}
    rng = np.random.default_rng(random_state)
    names = np.array(sorted(rows))
    for _ in range(n):
        sample = np.concatenate([rows[district] for district in rng.choice(names, len(names))])
        yield _model().fit(x[sample], y[sample])


def validate(frame):
    """Out-of-fold checks: ranking, calibration overall, by decile and by borough."""
    x = planning_features(frame).to_numpy()
    y = frame.underserved.astype("int64").to_numpy()
    probability = np.zeros(len(frame))
    for train, test in grouped_folds(frame):
        probability[test] = _model().fit(x[train], y[train]).predict_proba(x[test])[:, 1]
    confident = frame.label_confident.to_numpy(dtype=bool)
    decile = pd.qcut(probability, 10, labels=False)
    by_decile = pd.DataFrame({"predicted": probability, "observed": y}).groupby(decile).mean()
    by_borough = pd.DataFrame({"predicted": probability, "observed": y, "borough": frame.BoroName.to_numpy()}
                              ).groupby("borough")[["predicted", "observed"]].mean()
    within = {borough: roc_auc_score(y[mask], probability[mask])
              for borough in by_borough.index
              for mask in [frame.BoroName.eq(borough).to_numpy()] if 0 < y[mask].sum() < mask.sum()}
    by_borough["roc_auc_within"] = pd.Series(within)
    return {"roc_auc": roc_auc_score(y, probability),
            "roc_auc_confident": roc_auc_score(y[confident], probability[confident]),
            "brier": brier_score_loss(y, probability),
            "brier_prevalence_only": brier_score_loss(y, np.full(len(y), y.mean())),
            "by_decile": by_decile, "by_borough": by_borough,
            "out_of_fold_probability": pd.Series(probability, index=frame.index)}


def train_planning_model(frame=None, *, bootstraps=BOOTSTRAPS, random_state=0):
    """Fit on every labeled tract; return (model, coefficients DataFrame, bootstrap models).

    coefficients holds the standardized coefficient and its 95% district-bootstrap
    interval for each feature.
    """
    frame = training_frame() if frame is None else frame
    x = planning_features(frame).to_numpy()
    y = frame.underserved.astype("int64").to_numpy()
    model = _model().fit(x, y)
    boot = list(_district_bootstrap(frame, x, y, bootstraps, random_state))
    draws = np.array([m[-1].coef_[0] for m in boot])
    low, high = np.percentile(draws, [2.5, 97.5], axis=0)
    coefficients = pd.DataFrame({"coefficient": model[-1].coef_[0], "low_95": low, "high_95": high},
                                index=list(FEATURES))
    return model, coefficients, boot


def predict(model, boot, frame):
    """Per-tract probability, 90% interval and log-odds contribution of each feature."""
    features = planning_features(frame)
    x = features.to_numpy()
    probability = model.predict_proba(x)[:, 1]
    draws = np.array([m.predict_proba(x)[:, 1] for m in boot])
    low, high = np.percentile(draws, INTERVAL, axis=0)
    scaler, logistic = model[0], model[-1]
    contributions = (x - scaler.mean_) / scaler.scale_ * logistic.coef_[0]
    result = pd.DataFrame({"GEOID": frame.GEOID.to_numpy(), "probability": probability,
                           "low_90": low, "high_90": high}, index=frame.index)
    for i, name in enumerate(FEATURES):
        result[f"contribution_{name}"] = contributions[:, i]
    # Borough terms shift every tract in a borough alike, so the reason comes
    # from the tract's own features.
    own = contributions[:, :TRACT_FEATURES]
    result["main_reason"] = [FEATURES[i] for i in own.argmax(axis=1)]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    frame = training_frame()
    checks = validate(frame)
    print(f"out of fold: ROC AUC {checks['roc_auc']:.3f} (confident labels {checks['roc_auc_confident']:.3f}), "
          f"Brier {checks['brier']:.4f} vs {checks['brier_prevalence_only']:.4f} from the base rate alone")
    with pd.option_context("display.width", 200):
        print("\ncalibration by decile of predicted probability")
        print(checks["by_decile"].round(3).T.to_string())
        print("\ncalibration and ranking within each borough")
        print(checks["by_borough"].round(3).to_string())
        model, coefficients, boot = train_planning_model(frame)
        print("\nstandardized coefficients with 95% community-district bootstrap intervals")
        print(coefficients.round(3).to_string())
    predictions = predict(model, boot, frame)
    predictions["out_of_fold_probability"] = checks["out_of_fold_probability"]
    predictions = predictions.join(frame[["underserved", "label_confident"]]).sort_values(
        "probability", ascending=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output, index=False, float_format="%.4f")
    print(f"\nwrote {args.output} ({len(predictions)} tracts)")


if __name__ == "__main__":
    main()
