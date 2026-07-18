"""Constrained supervised training for interpretable spatial-index weights."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import LeaveOneOut

from .config import Config, REPO_ROOT
from .landvalue import COMPONENT_NAMES, WEIGHT_ARTIFACT_VERSION


def labels_path(cfg: Config) -> Path:
    raw = Path(cfg.get("weight_model", {}).get(
        "labels_file", "reference_data/commercial_land_spatial_markets.geojson"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def artifact_path(cfg: Config) -> Path:
    raw = Path(cfg.get("weight_model", {}).get(
        "artifact", "data/models/landvalue_weight_model.json"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def _vectors(cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    model_cfg = cfg.get("weight_model", {})
    prior_cfg = model_cfg.get("prior_weights", {})
    floor_cfg = model_cfg.get("minimum_weights", {})
    prior = np.array([float(prior_cfg.get(name, 0)) for name in COMPONENT_NAMES])
    floors = np.array([float(floor_cfg.get(name, 0)) for name in COMPONENT_NAMES])
    if floors.sum() > 1 + 1e-9:
        raise ValueError("weight_model.minimum_weights must sum to at most 1")
    prior = np.maximum(prior, floors)
    prior /= prior.sum()
    return prior, floors


def _fit_once(X, y, sample_weight, prior, floors, regularization):
    def objective(params):
        weights, intercept, slope = params[:-2], params[-2], params[-1]
        error = y - (intercept + slope * X.dot(weights))
        fit_loss = np.average(error ** 2, weights=sample_weight)
        return fit_loss + regularization * np.sum((weights - prior) ** 2)

    initial_score = X.dot(prior)
    initial = np.r_[
        prior,
        np.average(y, weights=sample_weight) - np.average(initial_score, weights=sample_weight),
        1.0,
    ]
    bounds = [(float(floor), 1.0) for floor in floors] + [(None, None), (0.0, 5.0)]
    result = minimize(
        objective, initial, method="SLSQP", bounds=bounds,
        constraints=[{"type": "eq", "fun": lambda p: p[:-2].sum() - 1.0}],
        options={"maxiter": 1000, "ftol": 1e-10},
    )
    if not result.success:
        raise RuntimeError(f"weight optimization failed: {result.message}")
    return result.x


def fit_weight_model(cfg: Config, training: pd.DataFrame) -> dict:
    """Tune regularization with leave-one-market-area-out validation."""
    minimum = int(cfg.get("weight_model", {}).get("minimum_labels", 6))
    if len(training) < minimum:
        raise ValueError(f"Need at least {minimum} in-metro spatial labels; found {len(training)}")
    X = training[[f"norm_{name}" for name in COMPONENT_NAMES]].to_numpy(dtype=float)
    target = training["price_per_sqm_php"].to_numpy(dtype=float)
    y = np.log(target)
    sample_weight = np.sqrt(training["listing_observations"].to_numpy(dtype=float).clip(1))
    prior, floors = _vectors(cfg)
    grid = [float(x) for x in cfg.get("weight_model", {}).get(
        "regularization_grid", [1.0, 10.0, 100.0])]

    results = []
    best = None
    for regularization in grid:
        predicted = np.full(len(training), np.nan)
        for train_idx, test_idx in LeaveOneOut().split(X):
            params = _fit_once(
                X[train_idx], y[train_idx], sample_weight[train_idx],
                prior, floors, regularization,
            )
            predicted[test_idx] = np.exp(
                params[-2] + params[-1] * X[test_idx].dot(params[:-2]))
        ape = np.abs(predicted - target) / target
        row = {
            "regularization": regularization,
            "mae_php_sqm": float(mean_absolute_error(target, predicted)),
            "median_ape": float(np.median(ape)),
        }
        results.append(row)
        key = (row["median_ape"], row["mae_php_sqm"])
        if best is None or key < best[0]:
            best = (key, regularization, predicted)
    assert best is not None

    params = _fit_once(X, y, sample_weight, prior, floors, best[1])
    weights = {name: float(value) for name, value in zip(COMPONENT_NAMES, params[:-2])}
    return {
        "artifact_version": WEIGHT_ARTIFACT_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "model": "positive simplex ridge on log advertised price",
        "target": "market-area median commercial vacant-land asking price, PHP per sqm",
        "property_segment": "commercial_vacant_land",
        "components": list(COMPONENT_NAMES),
        "weights": weights,
        "prior_weights": {name: float(value) for name, value in zip(COMPONENT_NAMES, prior)},
        "minimum_weights": {name: float(value) for name, value in zip(COMPONENT_NAMES, floors)},
        "score_intercept": float(params[-2]),
        "score_slope": float(params[-1]),
        "n_spatial_labels": int(len(training)),
        "n_listing_observations": int(training["listing_observations"].sum()),
        "markets": training["market_area"].astype(str).tolist(),
        "validation": "leave_one_market_area_out",
        "mae_php_sqm": float(best[0][1]),
        "median_ape": float(best[0][0]),
        "selected_regularization": float(best[1]),
        "tuning_results": results,
        "price_source_urls": sorted(training["price_source_url"].astype(str).unique().tolist()),
        "location_source_urls": sorted(training["location_source_url"].astype(str).unique().tolist()),
        "limitations": "Small market-area sample; domain floors stabilize highway/POI effects.",
    }


def save_artifact(metadata: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2))
