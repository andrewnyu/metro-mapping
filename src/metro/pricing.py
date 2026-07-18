"""Supervised peso-per-square-metre model for commercial vacant land.

Listings are asking/minimum prices, not completed transactions.  The model is
therefore an automated valuation *estimate* and its metadata keeps that label
semantics explicit.  Validation is grouped by city whenever possible so a
city-level deposit or population feature cannot leak into a random row split.
"""
from __future__ import annotations

import copy
import json
import re
from itertools import product
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.pipeline import Pipeline

from . import grid, landvalue
from .config import Config, REPO_ROOT
from .economics import (
    AREA_COLUMNS,
    attach_area_features,
    match_reference_row,
    normalize_area_name,
)


ARTIFACT_VERSION = 5

RAW_MODEL_FEATURES = (
    "dist_cbd_km",
    "dist_major_road_km",
    "establishment_access",
    "poi_weighted_density",
    "road_density_km",
    "poi_count",
    "builtup_score",
    "in_metro",
    "city_population",
    "bank_deposits_php",
    "bank_deposits_per_capita_php",
    "local_tax_revenue_php",
    "local_tax_revenue_per_capita_php",
    "real_property_tax_php",
    "business_tax_php",
)

MODEL_FEATURES = (
    "dist_cbd_km",
    "dist_major_road_km",
    "log_establishment_access",
    "log_poi_density",
    "log_road_density",
    "log_poi_count",
    "builtup_score",
    "in_metro",
    "log_city_population",
    "log_bank_deposits",
    "log_bank_deposits_per_capita",
    "log_local_tax_revenue",
    "log_local_tax_revenue_per_capita",
    "log_real_property_tax",
    "log_business_tax",
)

MARKET_MODEL_FEATURES = (
    "log_city_population",
    "log_bank_deposits",
    "log_bank_accounts",
    "log_bank_offices",
    "log_bank_deposits_per_capita",
    "log_local_tax_revenue",
    "log_local_tax_revenue_per_capita",
    "log_real_property_tax",
    "log_business_tax",
)

LISTING_ALIASES = {
    "lat": "latitude",
    "lng": "longitude",
    "lon": "longitude",
    "asking_price": "price_php",
    "advertised_price": "price_php",
    "selling_price": "price_php",
    "minimum_bid_price": "price_php",
    "price": "price_php",
    "land_area": "lot_area_sqm",
    "lot_area": "lot_area_sqm",
    "area_sqm": "lot_area_sqm",
    "price_per_sqm": "price_per_sqm_php",
    "price_sqm": "price_per_sqm_php",
    "url": "source_url",
}


def artifact_path(cfg: Config) -> Path:
    raw = Path(cfg.get("price_model", {}).get(
        "artifact", "data/models/land_price_model.joblib"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def labels_path(cfg: Config) -> Path:
    raw = Path(cfg.get("price_model", {}).get(
        "labels_file", "data/commercial_land_price_listings.csv"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def market_observations_path(cfg: Config) -> Path:
    raw = Path(cfg.get("price_model", {}).get(
        "market_observations_file",
        "reference_data/commercial_land_market_observations.json"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def top_market_anchors_path(cfg: Config) -> Path:
    raw = Path(cfg.get("price_model", {}).get(
        "top_market_anchors_file",
        "reference_data/commercial_land_top_market_anchors.json"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def _canonical_property_type(value: object) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def normalize_listings(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Validate and normalize commercial vacant-land listing rows.

    Accepted inputs may provide total price + lot area or an already computed
    price_per_sqm_php.  A row must have coordinates (or a precomputed H3 id),
    and property types mentioning buildings/condominiums are rejected because
    their price is not a clean land label.
    """
    out = df.rename(columns={c: LISTING_ALIASES.get(c.lower(), c.lower()) for c in df.columns}).copy()
    def numeric(series: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_numeric(series, errors="coerce")
        cleaned = series.astype("string").str.replace(r"[^0-9.\-]", "", regex=True)
        return pd.to_numeric(cleaned.replace("", pd.NA), errors="coerce")

    for col in ("price_php", "lot_area_sqm", "price_per_sqm_php", "latitude", "longitude"):
        if col not in out:
            out[col] = np.nan
        out[col] = numeric(out[col])
    for col in ("listing_id", "source", "source_url", "observed_at", "city",
                "province", "property_type", "h3"):
        if col not in out:
            out[col] = pd.NA

    computed = out["price_php"] / out["lot_area_sqm"].replace(0, np.nan)
    out["price_per_sqm_php"] = out["price_per_sqm_php"].fillna(computed)
    out["property_type"] = out["property_type"].map(_canonical_property_type)
    out["observed_at"] = pd.to_datetime(out["observed_at"], errors="coerce", utc=True)

    model_cfg = cfg.get("price_model", {})
    include_terms = tuple(model_cfg.get("land_type_terms", ["land", "lot"]))
    required_terms = tuple(model_cfg.get("required_property_type_terms", ["commercial"]))
    exclude_terms = tuple(model_cfg.get(
        "improvement_terms", ["house", "condo", "townhouse", "building", "improvement"]))
    is_land = out["property_type"].map(lambda s: any(t in s for t in include_terms))
    has_required_type = out["property_type"].map(
        lambda s: all(t in s for t in required_terms))
    has_improvement = out["property_type"].map(lambda s: any(t in s for t in exclude_terms))

    lo = float(model_cfg.get("min_price_per_sqm_php", 250))
    hi = float(model_cfg.get("max_price_per_sqm_php", 1_000_000))
    target_ok = out["price_per_sqm_php"].between(lo, hi, inclusive="both")
    coords_ok = (
        out["latitude"].between(4, 22, inclusive="both")
        & out["longitude"].between(116, 127, inclusive="both"))
    location_ok = coords_ok | out["h3"].notna()
    source_ok = out["source"].notna() & out["source"].astype(str).str.strip().ne("")
    url_ok = out["source_url"].fillna("").astype(str).str.match(r"https?://")
    provenance_ok = source_ok & url_ok & out["observed_at"].notna()
    keep = (
        is_land & has_required_type & ~has_improvement
        & target_ok & location_ok & provenance_ok
    )

    stats = {
        "input_rows": int(len(out)),
        "accepted_rows": int(keep.sum()),
        "rejected_non_commercial_land_or_improved": int(
            (~is_land | ~has_required_type | has_improvement).sum()),
        "rejected_price": int((~target_ok).sum()),
        "rejected_location": int((~location_ok).sum()),
        "rejected_provenance": int((~provenance_ok).sum()),
    }
    out = out.loc[keep].copy()
    out["city_group"] = out["city"].map(normalize_area_name)

    # Prefer explicit listing/source identifiers, then remove exact spatial
    # price duplicates which commonly arise from broker cross-posting.
    id_key = out["source"].fillna("").astype(str) + "|" + out["listing_id"].fillna("").astype(str)
    has_id = out["listing_id"].notna() & out["listing_id"].astype(str).str.len().gt(0)
    out = pd.concat([
        out.loc[has_id].assign(_id_key=id_key.loc[has_id]).drop_duplicates("_id_key"),
        out.loc[~has_id],
    ]).sort_index()
    out = out.drop_duplicates(
        ["source_url", "latitude", "longitude", "price_per_sqm_php"], keep="last")
    out = out.drop(columns="_id_key", errors="ignore")
    stats["deduplicated_rows"] = int(len(out))
    out.attrs["normalization"] = stats
    return out


def read_listings(path: Path, cfg: Config) -> pd.DataFrame:
    if path.suffix.lower() in {".json", ".jsonl"}:
        raw = pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    else:
        raw = pd.read_csv(path)
    return normalize_listings(raw, cfg)


def model_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Create stable numeric columns used by both training and inference."""
    raw = pd.DataFrame(index=frame.index)
    for col in RAW_MODEL_FEATURES:
        raw[col] = pd.to_numeric(frame[col], errors="coerce") if col in frame else np.nan

    def log1p(col: str) -> pd.Series:
        return np.log1p(raw[col].clip(lower=0))

    matrix = pd.DataFrame({
        "dist_cbd_km": raw["dist_cbd_km"],
        "dist_major_road_km": raw["dist_major_road_km"],
        "log_establishment_access": log1p("establishment_access"),
        "log_poi_density": log1p("poi_weighted_density"),
        "log_road_density": log1p("road_density_km"),
        "log_poi_count": log1p("poi_count"),
        "builtup_score": raw["builtup_score"],
        "in_metro": raw["in_metro"],
        "log_city_population": log1p("city_population"),
        "log_bank_deposits": log1p("bank_deposits_php"),
        "log_bank_deposits_per_capita": log1p("bank_deposits_per_capita_php"),
        "log_local_tax_revenue": log1p("local_tax_revenue_php"),
        "log_local_tax_revenue_per_capita": log1p("local_tax_revenue_per_capita_php"),
        "log_real_property_tax": log1p("real_property_tax_php"),
        "log_business_tax": log1p("business_tax_php"),
    }, index=frame.index)[list(MODEL_FEATURES)]
    return matrix.replace([np.inf, -np.inf], np.nan)


def normalize_market_observations(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Validate source-linked commercial vacant-land market observations.

    These rows summarize one named subdivision or local vacant-lot market.
    They are used to learn a citywide baseline; H3 variation is added later
    from the independently computed accessibility score.
    """
    out = df.rename(columns={c: c.lower() for c in df.columns}).copy()
    required = {
        "market_id", "city", "province", "property_type",
        "price_per_sqm_php", "observed_at", "source", "source_url",
    }
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"Market observation file missing columns: {sorted(missing)}")
    out["price_per_sqm_php"] = pd.to_numeric(
        out["price_per_sqm_php"], errors="coerce")
    if "underlying_listings" not in out:
        out["underlying_listings"] = 1
    out["underlying_listings"] = pd.to_numeric(
        out["underlying_listings"], errors="coerce").fillna(1).clip(lower=1)
    out["observed_at"] = pd.to_datetime(out["observed_at"], errors="coerce", utc=True)
    out["property_type"] = out["property_type"].map(_canonical_property_type)

    model_cfg = cfg.get("price_model", {})
    lo = float(model_cfg.get("min_price_per_sqm_php", 250))
    hi = float(model_cfg.get("max_price_per_sqm_php", 1_000_000))
    required_terms = tuple(model_cfg.get(
        "required_property_type_terms", ["commercial"]))
    land_terms = tuple(model_cfg.get("land_type_terms", ["land", "lot"]))
    improvement_terms = tuple(model_cfg.get(
        "improvement_terms", ["house", "condo", "townhouse", "building", "improvement"]))
    commercial_land = (
        out["property_type"].map(lambda s: all(t in s for t in required_terms))
        & out["property_type"].map(lambda s: any(t in s for t in land_terms))
        & ~out["property_type"].map(lambda s: any(t in s for t in improvement_terms))
    )
    provenance = (
        out["source"].fillna("").astype(str).str.strip().ne("")
        & out["source_url"].fillna("").astype(str).str.match(r"https?://")
        & out["observed_at"].notna()
    )
    target = out["price_per_sqm_php"].between(lo, hi, inclusive="both")
    named_market = (
        out["market_id"].fillna("").astype(str).str.strip().ne("")
        & out["city"].fillna("").astype(str).str.strip().ne("")
    )
    keep = commercial_land & provenance & target & named_market
    stats = {
        "input_rows": int(len(out)),
        "accepted_rows": int(keep.sum()),
        "rejected_property_type": int((~commercial_land).sum()),
        "rejected_provenance": int((~provenance).sum()),
        "rejected_price": int((~target).sum()),
    }
    out = out.loc[keep].drop_duplicates("market_id", keep="last").copy()
    out["market_group"] = (
        out["city"].map(normalize_area_name) + "|"
        + out["province"].map(normalize_area_name)
    )
    stats["deduplicated_rows"] = int(len(out))
    out.attrs["normalization"] = stats
    return out


def read_market_observations(path: Path, cfg: Config) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".geojson":
        raw = gpd.read_file(path).drop(columns="geometry", errors="ignore")
    elif suffix in {".json", ".jsonl"}:
        raw = pd.read_json(path, lines=suffix == ".jsonl")
    else:
        raw = pd.read_csv(path)
    return normalize_market_observations(raw, cfg)


def _weighted_quantile(values, weights, quantile: float) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return float("nan")
    values, weights = values[valid], weights[valid]
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cutoff = min(max(float(quantile), 0.0), 1.0) * weights.sum()
    index = min(int(np.searchsorted(np.cumsum(weights), cutoff, side="left")), len(values) - 1)
    return float(values[index])


def build_top_market_calibrations(cfg: Config, anchors: pd.DataFrame) -> dict:
    """Build robust listing-rich anchors without changing the cross-city fit.

    Each source row is one deduplicated advertised commercial vacant lot. The
    interval estimates uncertainty in the local median anchor; it is not the
    full dispersion of every parcel in the named neighborhoods.
    """
    model_cfg = cfg.get("price_model", {})
    minimum = int(model_cfg.get("minimum_top_anchor_observations", 8))
    z = float(model_cfg.get("anchor_interval_z", 1.2816))
    min_half = float(model_cfg.get("anchor_interval_min_log", 0.12))
    max_half = float(model_cfg.get("anchor_interval_max_log", 0.35))
    score_quantile = float(model_cfg.get("top_anchor_score_quantile", 0.90))
    calibrations = {}

    for city_key, rows in anchors.groupby(
        anchors["city"].map(normalize_area_name), sort=True
    ):
        if len(rows) < minimum:
            continue
        prices = rows["price_per_sqm_php"].to_numpy(dtype=float)
        # Market-summary rows may carry a listing count. Square-root weights
        # preserve depth without allowing one duplicated cluster to dominate.
        weights = np.sqrt(rows["underlying_listings"].to_numpy(dtype=float))
        log_prices = np.log(prices)
        log_anchor = _weighted_quantile(log_prices, weights, 0.5)
        mad = _weighted_quantile(np.abs(log_prices - log_anchor), weights, 0.5)
        robust_se = 1.4826 * mad / np.sqrt(len(rows))
        half_width = float(np.clip(z * robust_se, min_half, max_half))
        calibrations[city_key] = {
            "city": str(rows["city"].iloc[0]),
            "price_per_sqm_php": float(np.exp(log_anchor)),
            "role": "top_market_anchor",
            "score_quantile": score_quantile,
            "confidence_level": 0.80,
            "confidence_log_half_width": half_width,
            "n_market_observations": int(len(rows)),
            "n_underlying_listings": int(rows["underlying_listings"].sum()),
            "market_areas": sorted(rows.get(
                "market_area", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()),
            "source_urls": sorted(rows["source_url"].dropna().astype(str).unique().tolist()),
            "province": str(rows["province"].iloc[0]),
        }
    return calibrations


def _cached_land_cell_count(cfg: Config, city: str) -> tuple[int | None, str | None]:
    """Return the current analyzed-land-cell count for a donor city cache."""
    slug = str(city).strip().lower().replace(" ", "_")
    res = int(cfg["grid"]["h3_resolution"])
    buf = float(cfg["city"]["study_buffer_km"])
    suffix = f"_features_res{res}_{buf:g}km.parquet"
    key = normalize_area_name(city)
    osm_id = None
    for name, candidate in (cfg.get("city", {}).get("osm_id_fallbacks", {}) or {}).items():
        if normalize_area_name(name) == key:
            osm_id = str(candidate).lower()
            break
    candidates = []
    if osm_id:
        candidates.append(cfg.data_dir / f"{slug}_{osm_id}{suffix}")
    candidates.append(cfg.data_dir / f"{slug}{suffix}")
    candidates.extend(sorted(cfg.data_dir.glob(f"{slug}_*{suffix}")))
    seen = set()
    for path in candidates:
        if path in seen or not path.exists() or "_synth" in path.stem:
            continue
        seen.add(path)
        try:
            count = len(pd.read_parquet(path, columns=["h3"]))
        except Exception:
            continue
        if count > 0:
            return int(count), str(path)
    return None, None


def build_donor_city_profiles(cfg: Config, calibrations: dict) -> dict:
    """Attach deposits-per-land-cell context to listing-rich anchor cities."""
    profiles = {}
    for city_key, anchor in calibrations.items():
        city = str(anchor.get("city") or city_key)
        province = str(anchor.get("province") or "")
        economic = match_reference_row(
            cfg, f"{city}, {province}, Philippines")
        n_cells, cache = _cached_land_cell_count(cfg, city)
        deposits = None if economic is None else pd.to_numeric(
            economic.get("bank_deposits_php"), errors="coerce")
        if n_cells is None or pd.isna(deposits) or float(deposits) <= 0:
            continue
        population = pd.to_numeric(
            economic.get("city_population"), errors="coerce")
        profiles[city_key] = {
            "city": city,
            "province": province,
            "anchor_price_per_sqm_php": float(anchor["price_per_sqm_php"]),
            "bank_deposits_php": float(deposits),
            "n_land_cells": int(n_cells),
            "bank_deposits_per_land_cell_php": float(deposits) / n_cells,
            "city_population": None if pd.isna(population) else float(population),
            "anchor_confidence_log_half_width": float(
                anchor.get("confidence_log_half_width", 0.35)),
            "feature_cache": cache,
        }
    return profiles


def infer_comparable_city_baseline(cfg: Config, gdf, metadata: dict) -> dict | None:
    """Infer a commercial baseline from similar anchored deposit/cell markets.

    Donor anchors are first right-sized by the exact ratio of bank deposits per
    analyzed land cell. The nearest donor markets in log deposit/cell space are
    then averaged with Gaussian similarity weights.
    """
    profiles = metadata.get("donor_city_profiles", {}) or {}
    if not profiles or gdf.empty or "bank_deposits_php" not in gdf:
        return None
    deposits = pd.to_numeric(gdf["bank_deposits_php"].iloc[0], errors="coerce")
    n_cells = int(len(gdf))
    if pd.isna(deposits) or float(deposits) <= 0 or n_cells <= 0:
        return None
    target_density = float(deposits) / n_cells
    fallback_cfg = cfg.get("price_model", {}).get("comparable_city_fallback", {}) or {}
    n_neighbors = max(1, int(fallback_cfg.get("n_neighbors", 3)))
    bandwidth = max(float(fallback_cfg.get("similarity_bandwidth_log", 0.75)), 1e-6)
    exponent = float(fallback_cfg.get("deposit_ratio_exponent", 1.0))
    ratio_min = float(fallback_cfg.get("deposit_ratio_min", 0.01))
    ratio_max = float(fallback_cfg.get("deposit_ratio_max", 100.0))

    candidates = []
    for city_key, profile in profiles.items():
        donor_density = float(profile["bank_deposits_per_land_cell_php"])
        donor_price = float(profile["anchor_price_per_sqm_php"])
        if donor_density <= 0 or donor_price <= 0:
            continue
        log_distance = abs(float(np.log(target_density / donor_density)))
        ratio = float(np.clip(target_density / donor_density, ratio_min, ratio_max))
        scaled_price = donor_price * ratio ** exponent
        candidates.append({
            "city_key": city_key,
            "city": profile.get("city", city_key),
            "log_distance": log_distance,
            "similarity_weight": float(np.exp(-0.5 * (log_distance / bandwidth) ** 2)),
            "deposit_per_land_cell_php": donor_density,
            "deposit_density_ratio": ratio,
            "anchor_price_per_sqm_php": donor_price,
            "scaled_price_per_sqm_php": scaled_price,
            "anchor_confidence_log_half_width": float(
                profile.get("anchor_confidence_log_half_width", 0.35)),
        })
    donors = sorted(candidates, key=lambda row: row["log_distance"])[:n_neighbors]
    if not donors:
        return None
    weights = np.array([max(row["similarity_weight"], 1e-6) for row in donors])
    prices = np.array([row["scaled_price_per_sqm_php"] for row in donors])
    baseline = float(np.average(prices, weights=weights))
    log_prices = np.log(prices)
    disagreement = float(np.sqrt(np.average(
        (log_prices - np.average(log_prices, weights=weights)) ** 2,
        weights=weights,
    )))
    anchor_half = float(np.average(
        [row["anchor_confidence_log_half_width"] for row in donors],
        weights=weights,
    ))
    min_half = float(fallback_cfg.get("interval_min_log", 0.25))
    max_half = float(fallback_cfg.get("interval_max_log", 0.65))
    half_width = float(np.clip(max(anchor_half, 1.2816 * disagreement), min_half, max_half))
    weight_sum = float(weights.sum())
    for row, weight in zip(donors, weights):
        row["similarity_weight"] = float(weight / weight_sum)
    return {
        "price_per_sqm_php": baseline,
        "target_bank_deposits_php": float(deposits),
        "target_n_land_cells": n_cells,
        "target_bank_deposits_per_land_cell_php": target_density,
        "confidence_log_half_width": half_width,
        "donors": donors,
        "method": "similar_anchored_cities_scaled_by_bank_deposits_per_land_cell",
    }


def market_model_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Create the area-level feature matrix used by the market model."""
    raw = pd.DataFrame(index=frame.index)
    for col in (*AREA_COLUMNS, "bank_deposits_per_capita_php",
                "local_tax_revenue_per_capita_php"):
        raw[col] = pd.to_numeric(frame[col], errors="coerce") if col in frame else np.nan

    def log1p(col: str) -> pd.Series:
        return np.log1p(raw[col].clip(lower=0))

    matrix = pd.DataFrame({
        "log_city_population": log1p("city_population"),
        "log_bank_deposits": log1p("bank_deposits_php"),
        "log_bank_accounts": log1p("bank_accounts"),
        "log_bank_offices": log1p("bank_offices"),
        "log_bank_deposits_per_capita": log1p("bank_deposits_per_capita_php"),
        "log_local_tax_revenue": log1p("local_tax_revenue_php"),
        "log_local_tax_revenue_per_capita": log1p("local_tax_revenue_per_capita_php"),
        "log_real_property_tax": log1p("real_property_tax_php"),
        "log_business_tax": log1p("business_tax_php"),
    }, index=frame.index)[list(MARKET_MODEL_FEATURES)]
    return matrix.replace([np.inf, -np.inf], np.nan)


def build_market_training_frame(
    cfg: Config, observations: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    """Join named market observations to city/metro economic features."""
    rows = []
    unmatched = 0
    for _, observation in observations.iterrows():
        place = f"{observation['city']}, {observation['province']}, Philippines"
        economic = match_reference_row(cfg, place)
        if economic is None:
            unmatched += 1
            continue
        record = {col: pd.to_numeric(economic.get(col), errors="coerce")
                  for col in AREA_COLUMNS}
        population = record.get("city_population")
        record["bank_deposits_per_capita_php"] = (
            record.get("bank_deposits_php") / population
            if pd.notna(population) and population > 0 else np.nan)
        record["local_tax_revenue_per_capita_php"] = (
            record.get("local_tax_revenue_php") / population
            if pd.notna(population) and population > 0 else np.nan)
        record.update({
            "market_id": observation["market_id"],
            "market_group": observation["market_group"],
            "city": observation["city"],
            "province": observation["province"],
            "price_per_sqm_php": float(observation["price_per_sqm_php"]),
            "underlying_listings": int(observation["underlying_listings"]),
            "source": observation["source"],
            "source_url": observation["source_url"],
            "observed_at": observation["observed_at"],
        })
        rows.append(record)
    return pd.DataFrame(rows), {
        "market_observations": int(len(observations)),
        "matched_observations": int(len(rows)),
        "unmatched_observations": int(unmatched),
        "underlying_listings": int(observations["underlying_listings"].sum()),
    }


def _modeled_feature_table(cfg: Config, path: Path) -> tuple[str, gpd.GeoDataFrame]:
    gdf = gpd.read_parquet(path).set_index("h3", drop=False)
    place = str(gdf["cache_place"].iloc[0]) if "cache_place" in gdf else path.stem
    city_cfg = copy.deepcopy(cfg)
    city_cfg["city"]["place"] = place
    gdf = landvalue.run_model(city_cfg, gdf)
    gdf = attach_area_features(city_cfg, gdf)
    return normalize_area_name(place), gdf


def build_training_frame(
    cfg: Config, listings: pd.DataFrame, feature_paths: list[Path]
) -> tuple[pd.DataFrame, dict]:
    """Spatially join normalized listings to cached H3 feature tables."""
    tables: dict[str, list[gpd.GeoDataFrame]] = {}
    all_tables: list[tuple[str, gpd.GeoDataFrame]] = []
    for path in feature_paths:
        key, gdf = _modeled_feature_table(cfg, path)
        tables.setdefault(key, []).append(gdf)
        all_tables.append((key, gdf))

    rows = []
    unmatched = 0
    res = int(cfg["grid"]["h3_resolution"])
    for _, listing in listings.iterrows():
        cell_value = listing.get("h3")
        cell = "" if cell_value is None or pd.isna(cell_value) else str(cell_value).strip()
        if not cell:
            cell = grid.latlng_to_cell(
                float(listing["latitude"]), float(listing["longitude"]), res)
        key_value = listing.get("city_group")
        key = "" if key_value is None or pd.isna(key_value) else str(key_value)
        candidates = [(key, g) for g in tables.get(key, [])]
        if not candidates:
            candidates = all_tables
        matches = [(k, g) for k, g in candidates if cell in g.index]
        if not matches:
            unmatched += 1
            continue
        # Overlapping city buffers occasionally contain the same H3 cell.  Use
        # the named city when available; otherwise the closest detected CBD.
        matched_key, matched = min(
            matches, key=lambda item: float(item[1].loc[cell, "dist_cbd_km"]))
        feature = matched.loc[cell]
        record = {col: feature.get(col, np.nan) for col in RAW_MODEL_FEATURES}
        record.update({
            "h3": cell,
            "city_group": matched_key,
            "price_per_sqm_php": float(listing["price_per_sqm_php"]),
            "source": listing.get("source"),
            "source_url": listing.get("source_url"),
            "observed_at": listing.get("observed_at"),
        })
        rows.append(record)
    return pd.DataFrame(rows), {
        "listing_rows": int(len(listings)),
        "matched_rows": int(len(rows)),
        "unmatched_rows": int(unmatched),
        "feature_files": int(len(feature_paths)),
    }


def _new_estimator(cfg: Config, overrides: dict | None = None) -> HistGradientBoostingRegressor:
    params = cfg.get("price_model", {}).get("estimator", {}) or {}
    overrides = overrides or {}
    return HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=float(params.get("learning_rate", 0.05)),
        max_iter=int(params.get("max_iter", 300)),
        max_leaf_nodes=int(overrides.get("max_leaf_nodes", params.get("max_leaf_nodes", 15))),
        min_samples_leaf=int(overrides.get("min_samples_leaf", params.get("min_samples_leaf", 10))),
        l2_regularization=float(overrides.get(
            "l2_regularization", params.get("l2_regularization", 1.0))),
        random_state=42,
    )


def _tuning_candidates(cfg: Config) -> list[dict]:
    tune = cfg.get("price_model", {}).get("tuning", {}) or {}
    leaves = tune.get("max_leaf_nodes", [15])
    min_leaf = tune.get("min_samples_leaf", [10])
    l2 = tune.get("l2_regularization", [1.0])
    return [
        {"max_leaf_nodes": int(a), "min_samples_leaf": int(b),
         "l2_regularization": float(c)}
        for a, b, c in product(leaves, min_leaf, l2)
    ]


def _market_tuning_candidates(cfg: Config) -> list[dict]:
    tune = cfg.get("price_model", {}).get("market_tuning", {}) or {}
    return [
        {"min_samples_leaf": int(leaf), "max_features": float(features)}
        for leaf, features in product(
            tune.get("min_samples_leaf", [2, 3]),
            tune.get("max_features", [0.7, 1.0]),
        )
    ]


def _new_market_estimator(params: dict) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("model", ExtraTreesRegressor(
            n_estimators=400,
            min_samples_leaf=int(params["min_samples_leaf"]),
            max_features=float(params["max_features"]),
            random_state=42,
            n_jobs=1,
        )),
    ])


def _market_oof_for_params(X, y, splits, params: dict) -> np.ndarray:
    predictions = np.full(len(X), np.nan)
    for train_idx, test_idx in splits:
        fold = clone(_new_market_estimator(params))
        fold.fit(X.iloc[train_idx], y[train_idx])
        predictions[test_idx] = fold.predict(X.iloc[test_idx])
    return predictions


def _select_market_params(X, y, splits, cfg: Config) -> tuple[dict, list[dict]]:
    results = []
    best = None
    for params in _market_tuning_candidates(cfg):
        predictions = _market_oof_for_params(X, y, splits, params)
        score = float(np.nanmedian(np.abs(y - predictions)))
        results.append({**params, "median_absolute_log_error": score})
        if best is None or score < best[0]:
            best = (score, params)
    assert best is not None
    return best[1], results


def _oof_for_params(X, y, splits, cfg: Config, params: dict) -> np.ndarray:
    estimator = _new_estimator(cfg, params)
    predictions = np.full(len(X), np.nan)
    for train_idx, test_idx in splits:
        fold = clone(estimator)
        fold.fit(X.iloc[train_idx], y[train_idx])
        predictions[test_idx] = fold.predict(X.iloc[test_idx])
    return predictions


def _select_params(X, y, splits, cfg: Config) -> tuple[dict, list[dict]]:
    results = []
    best = None
    for params in _tuning_candidates(cfg):
        predictions = _oof_for_params(X, y, splits, cfg, params)
        score = float(np.nanmedian(np.abs(y - predictions)))
        results.append({**params, "median_absolute_log_error": score})
        if best is None or score < best[0]:
            best = (score, params)
    assert best is not None
    return best[1], results


def fit_price_model(
    cfg: Config, training: pd.DataFrame, *, allow_small_sample: bool = False
) -> dict:
    """Fit the log-price model and compute honest out-of-fold diagnostics."""
    n = len(training)
    groups = training["city_group"].fillna("unknown").astype(str)
    n_cities = int(groups.nunique())
    model_cfg = cfg.get("price_model", {})
    min_labels = int(model_cfg.get("minimum_labels", 60))
    min_cities = int(model_cfg.get("minimum_cities", 3))
    if not allow_small_sample and (n < min_labels or n_cities < min_cities):
        raise ValueError(
            f"Need at least {min_labels} matched land labels across {min_cities} cities; "
            f"found {n} labels across {n_cities} cities. Use --allow-small-sample only for testing.")
    if n < 8:
        raise ValueError("At least 8 matched labels are required even in small-sample mode.")

    X = model_matrix(training)
    target = training["price_per_sqm_php"].astype(float).to_numpy()
    y = np.log(target)
    if n_cities >= 2:
        splitter = GroupKFold(n_splits=min(5, n_cities))
        splits = list(splitter.split(X, y, groups))
        validation = "nested_grouped_by_city" if n_cities >= 3 else "grouped_by_city_small_sample"
    else:
        splitter = KFold(n_splits=min(5, max(2, n // 4)), shuffle=True, random_state=42)
        splits = list(splitter.split(X, y))
        validation = "row_random_small_sample_only"

    # Report predictions from an outer held-out-city fold. For three or more
    # cities, estimator complexity is selected again using only the outer
    # training cities (nested CV), so the reported error is not the score used
    # to choose that fold's model.
    candidates = _tuning_candidates(cfg)
    oof_log = np.full(n, np.nan)
    outer_selections = []
    if n_cities >= 3:
        for outer_train, outer_test in splits:
            X_train, y_train = X.iloc[outer_train], y[outer_train]
            inner_groups = groups.iloc[outer_train]
            inner_splitter = GroupKFold(n_splits=min(4, int(inner_groups.nunique())))
            inner_splits = list(inner_splitter.split(X_train, y_train, inner_groups))
            fold_params, _ = _select_params(X_train, y_train, inner_splits, cfg)
            fold = _new_estimator(cfg, fold_params)
            fold.fit(X_train, y_train)
            oof_log[outer_test] = fold.predict(X.iloc[outer_test])
            outer_selections.append(fold_params)
    else:
        # Two-city and one-city paths are available only via the explicit
        # small-sample override; do not tune on their reporting folds.
        fold_params = candidates[0]
        oof_log = _oof_for_params(X, y, splits, cfg, fold_params)
        outer_selections.append(fold_params)

    # Choose the deployment estimator on all available cities. Its tuning
    # scores are metadata, while headline errors above remain nested OOF.
    best_params, tuning_results = _select_params(X, y, splits, cfg)
    estimator = _new_estimator(cfg, best_params)
    oof = np.exp(oof_log)
    residual_log = y - oof_log
    q_low, q_high = np.nanquantile(residual_log, [0.10, 0.90])
    q_low, q_high = min(float(q_low), 0.0), max(float(q_high), 0.0)
    ape = np.abs(oof - target) / target

    estimator.fit(X, y)
    metadata = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "cell_level",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "target": "advertised/minimum commercial vacant-land price, PHP per sqm",
        "n_labels": int(n),
        "n_cities": n_cities,
        "cities": sorted(groups.unique().tolist()),
        "validation": validation,
        "mae_php_sqm": float(mean_absolute_error(target, oof)),
        "median_ae_php_sqm": float(median_absolute_error(target, oof)),
        "median_ape": float(np.nanmedian(ape)),
        "r2_log": float(r2_score(y, oof_log)),
        "residual_log_q10": float(q_low),
        "residual_log_q90": float(q_high),
        "selected_estimator": best_params,
        "tuning_results": tuning_results,
        "outer_fold_estimators": outer_selections,
        "feature_names": list(MODEL_FEATURES),
        "label_sources": sorted(training["source"].dropna().astype(str).unique().tolist()),
    }
    return {"model": estimator, "metadata": metadata}


def fit_market_price_model(
    cfg: Config, training: pd.DataFrame, *, allow_small_sample: bool = False
) -> dict:
    """Fit the economic market-baseline model with city-held-out validation."""
    n = len(training)
    groups = training["market_group"].fillna("unknown").astype(str)
    n_cities = int(groups.nunique())
    model_cfg = cfg.get("price_model", {})
    min_observations = int(model_cfg.get("minimum_market_observations", 15))
    min_cities = int(model_cfg.get("minimum_market_cities", 10))
    if not allow_small_sample and (n < min_observations or n_cities < min_cities):
        raise ValueError(
            f"Need at least {min_observations} market observations across "
            f"{min_cities} cities; found {n} across {n_cities}.")
    if n < 8 or n_cities < 3:
        raise ValueError("Market training requires at least 8 observations across 3 cities.")

    X = market_model_matrix(training)
    target = training["price_per_sqm_php"].astype(float).to_numpy()
    y = np.log(target)
    splitter = GroupKFold(n_splits=min(5, n_cities))
    splits = list(splitter.split(X, y, groups))

    oof_log = np.full(n, np.nan)
    outer_selections = []
    for outer_train, outer_test in splits:
        X_train, y_train = X.iloc[outer_train], y[outer_train]
        inner_groups = groups.iloc[outer_train]
        inner_splitter = GroupKFold(n_splits=min(4, int(inner_groups.nunique())))
        inner_splits = list(inner_splitter.split(X_train, y_train, inner_groups))
        fold_params, _ = _select_market_params(X_train, y_train, inner_splits, cfg)
        fold = _new_market_estimator(fold_params)
        fold.fit(X_train, y_train)
        oof_log[outer_test] = fold.predict(X.iloc[outer_test])
        outer_selections.append(fold_params)

    best_params, tuning_results = _select_market_params(X, y, splits, cfg)
    estimator = _new_market_estimator(best_params)
    estimator.fit(X, y)

    oof = np.exp(oof_log)
    residual_log = y - oof_log
    q_low, q_high = np.nanquantile(residual_log, [0.10, 0.90])
    q_low, q_high = min(float(q_low), 0.0), max(float(q_high), 0.0)
    ape = np.abs(oof - target) / target
    calibrations = {}
    for city_key, city_rows in training.groupby(
        training["city"].map(normalize_area_name), sort=True
    ):
        weights = city_rows["underlying_listings"].to_numpy(dtype=float)
        prices = city_rows["price_per_sqm_php"].to_numpy(dtype=float)
        calibrations[city_key] = {
            "price_per_sqm_php": float(np.average(prices, weights=weights)),
            "n_market_observations": int(len(city_rows)),
            "n_underlying_listings": int(weights.sum()),
            "province": str(city_rows["province"].iloc[0]),
        }

    metadata = {
        "artifact_version": ARTIFACT_VERSION,
        "mode": "market_baseline",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "target": "advertised commercial vacant-land market price, PHP per sqm",
        "property_segment": "commercial_vacant_land",
        "n_labels": int(training["underlying_listings"].sum()),
        "n_market_observations": int(n),
        "n_cities": n_cities,
        "cities": sorted(groups.unique().tolist()),
        "validation": "nested_grouped_by_city",
        "mae_php_sqm": float(mean_absolute_error(target, oof)),
        "median_ae_php_sqm": float(median_absolute_error(target, oof)),
        "median_ape": float(np.nanmedian(ape)),
        "r2_log": float(r2_score(y, oof_log)),
        "residual_log_q10": float(q_low),
        "residual_log_q90": float(q_high),
        "selected_estimator": best_params,
        "tuning_results": tuning_results,
        "outer_fold_estimators": outer_selections,
        "feature_names": list(MARKET_MODEL_FEATURES),
        "label_sources": sorted(training["source"].dropna().astype(str).unique().tolist()),
        "source_urls": sorted(training["source_url"].dropna().astype(str).unique().tolist()),
        "cell_calibration": "area-weighted relative accessibility score",
        "observed_market_calibrations": calibrations,
    }
    return {"model": estimator, "metadata": metadata}


def save_artifact(bundle: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    path.with_suffix(".json").write_text(json.dumps(bundle["metadata"], indent=2))


def _empty_prediction_columns(gdf):
    out = gdf.copy()
    out["land_price_php_sqm"] = np.nan
    out["land_price_low_php_sqm"] = np.nan
    out["land_price_high_php_sqm"] = np.nan
    out["land_price_market_baseline_php_sqm"] = np.nan
    return out


def _area_weighted_mean(gdf, values: np.ndarray) -> float:
    metric = gdf.to_crs(gdf.estimate_utm_crs())
    weights = metric.geometry.area.to_numpy(dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return float(np.nanmean(values))
    return float(np.average(values[valid], weights=weights[valid]))


def _blend_relative_score(cfg: Config, gdf, estimate: np.ndarray) -> np.ndarray:
    """Blend learned local variation with the user's score-share allocation.

    Both relative curves are recentered to an area-weighted mean of one, so
    the blended cell prices preserve the model's citywide average ₱/m².
    """
    blend = float(cfg.get("price_model", {}).get("relative_score_blend", 0.35))
    if blend <= 0 or "land_value_score" not in gdf:
        return estimate
    blend = min(blend, 1.0)
    eps = 1e-6
    score = pd.to_numeric(gdf["land_value_score"], errors="coerce").to_numpy(dtype=float)
    score = np.clip(score, eps, None)
    base = np.clip(np.asarray(estimate, dtype=float), eps, None)
    city_mean = _area_weighted_mean(gdf, base)
    score_rel = score / max(_area_weighted_mean(gdf, score), eps)
    model_rel = base / max(city_mean, eps)
    combined = np.exp((1.0 - blend) * np.log(model_rel) + blend * np.log(score_rel))
    combined /= max(_area_weighted_mean(gdf, combined), eps)
    return city_mean * combined


def _top_anchor_price_surface(cfg: Config, gdf, anchor: dict) -> np.ndarray:
    """Place the observed top-market anchor on the score curve, then spread it."""
    model_cfg = cfg.get("price_model", {})
    score = pd.to_numeric(
        gdf["land_value_score"], errors="coerce").to_numpy(dtype=float)
    finite = score[np.isfinite(score)]
    if not len(finite):
        return np.full(len(gdf), float(anchor["price_per_sqm_php"]))
    eps = 1e-6
    quantile = float(anchor.get(
        "score_quantile", model_cfg.get("top_anchor_score_quantile", 0.90)))
    anchor_score = max(float(np.nanquantile(finite, quantile)), eps)
    elasticity = float(model_cfg.get("relative_score_elasticity", 0.65))
    multiplier = (np.clip(score, eps, None) / anchor_score) ** elasticity
    multiplier = np.clip(
        multiplier,
        float(model_cfg.get("relative_multiplier_min", 0.10)),
        float(model_cfg.get("relative_multiplier_max", 1.35)),
    )
    return float(anchor["price_per_sqm_php"]) * multiplier


def apply_price_model(cfg: Config, gdf):
    """Predict calibrated commercial-land ₱/m² for metro cells only.

    Rural/outside-metro cells intentionally remain null: the current listing
    evidence and accessibility model do not support defensible pricing there.
    """
    out = _empty_prediction_columns(gdf)
    path = artifact_path(cfg)
    if not path.exists():
        out.attrs["price_model"] = {"status": "not_trained", "artifact": str(path)}
        return out
    try:
        bundle = joblib.load(path)
        metadata = bundle["metadata"]
        if metadata.get("artifact_version") != ARTIFACT_VERSION:
            raise ValueError("unsupported artifact version")
        metro_mask = (
            out["in_metro"].fillna(False).astype(bool)
            if "in_metro" in out else pd.Series(True, index=out.index)
        )
        priced = out.loc[metro_mask].copy()
        if priced.empty:
            out.attrs["price_model"] = {
                "status": "no_metro_cells", "artifact": str(path),
                "priced_cell_count": 0, **metadata,
            }
            return out
        mode = metadata.get("mode", "cell_level")
        local_anchor = None
        comparable = None
        if mode == "market_baseline":
            baseline = float(np.exp(bundle["model"].predict(
                market_model_matrix(priced.iloc[[0]]))[0]))
            baseline_source = "machine_learning"
            city_key = normalize_area_name(cfg["city"]["place"])
            local_anchor = metadata.get("top_market_calibrations", {}).get(city_key)
            calibration = metadata.get(
                "observed_market_calibrations", {}).get(city_key)
            if local_anchor:
                baseline = float(local_anchor["price_per_sqm_php"])
                baseline_source = "top_market_anchor"
                raw_estimate = _top_anchor_price_surface(cfg, priced, local_anchor)
            elif calibration:
                baseline = float(calibration["price_per_sqm_php"])
                baseline_source = "observed_market_calibration"
                raw_estimate = np.full(len(priced), baseline, dtype=float)
            else:
                comparable = infer_comparable_city_baseline(cfg, out, metadata)
                if comparable:
                    baseline = float(comparable["price_per_sqm_php"])
                    baseline_source = "deposit_per_cell_comparable_cities"
                    raw_estimate = np.full(len(priced), baseline, dtype=float)
                else:
                    out.attrs["price_model"] = {
                        "status": "insufficient_economic_evidence",
                        "artifact": str(path),
                        "priced_cell_count": 0,
                        "price_geography": "metro_cells_only",
                        "market_baseline_source": "none",
                        **metadata,
                    }
                    return out
        elif mode == "cell_level":
            baseline = np.nan
            baseline_source = "machine_learning"
            raw_estimate = np.exp(bundle["model"].predict(model_matrix(priced)))
        else:
            raise ValueError(f"unsupported pricing mode: {mode}")
        lo = float(cfg.get("price_model", {}).get("min_price_per_sqm_php", 250))
        hi = float(cfg.get("price_model", {}).get("max_price_per_sqm_php", 1_000_000))
        if local_anchor:
            estimate = raw_estimate
            half_width = float(local_anchor["confidence_log_half_width"])
            low = estimate * np.exp(-half_width)
            high = estimate * np.exp(half_width)
            interval_method = "local_top_market_anchor_80pct"
        elif comparable:
            estimate = _blend_relative_score(cfg, priced, raw_estimate)
            half_width = float(comparable["confidence_log_half_width"])
            low = estimate * np.exp(-half_width)
            high = estimate * np.exp(half_width)
            interval_method = "comparable_city_deposit_per_cell_ratio_80pct"
        else:
            estimate = _blend_relative_score(cfg, priced, raw_estimate)
            low = estimate * np.exp(float(metadata["residual_log_q10"]))
            high = estimate * np.exp(float(metadata["residual_log_q90"]))
            interval_method = "held_out_city_residual_q10_q90"
        out.loc[priced.index, "land_price_php_sqm"] = np.clip(estimate, lo, hi)
        out.loc[priced.index, "land_price_low_php_sqm"] = np.clip(low, lo, hi)
        out.loc[priced.index, "land_price_high_php_sqm"] = np.clip(high, lo, hi)
        out.loc[priced.index, "land_price_market_baseline_php_sqm"] = baseline
        out.attrs["price_model"] = {
            "status": "trained", "artifact": str(path),
            "priced_cell_count": int(len(priced)),
            "price_geography": "metro_cells_only",
            "market_baseline_php_sqm": None if not np.isfinite(baseline) else baseline,
            "market_baseline_source": baseline_source,
            "interval_method": interval_method,
            "local_anchor_n_observations": (
                local_anchor.get("n_market_observations") if local_anchor else None),
            "local_anchor_n_listings": (
                local_anchor.get("n_underlying_listings") if local_anchor else None),
            "local_anchor_market_areas": (
                local_anchor.get("market_areas", []) if local_anchor else []),
            "local_anchor_score_quantile": (
                local_anchor.get("score_quantile") if local_anchor else None),
            "local_anchor_confidence_level": (
                local_anchor.get("confidence_level") if local_anchor else None),
            "comparable_city_method": comparable.get("method") if comparable else None,
            "comparable_city_donors": comparable.get("donors", []) if comparable else [],
            "target_bank_deposits_per_land_cell_php": (
                comparable.get("target_bank_deposits_per_land_cell_php")
                if comparable else None),
            "target_n_land_cells": (
                comparable.get("target_n_land_cells") if comparable else len(out)),
            **metadata,
        }
    except Exception as exc:  # a bad optional artifact must not break metro mapping
        out.attrs["price_model"] = {
            "status": "error", "artifact": str(path),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return out
