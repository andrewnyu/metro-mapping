"""Land-value proxy model + data-driven metro delineation.

LAND VALUE (starter / unsupervised)
-----------------------------------
With no transaction prices in the starter, we model *relative* land value as
a transparent weighted accessibility index. Distances become access via
exponential decay (closer = higher, diminishing returns); density features
are rank-normalised. The output `land_value_index` is 0-100.

    value = Σ_i  weight_i · normalise(component_i)

This is deliberately interpretable. To go supervised later, keep these same
columns as features and fit e.g. gradient boosting on real ₱/m² labels —
the feature table and app don't change.

METRO DELINEATION
-----------------
A cell is "urban" if its built-up score (POI + road density) is above a
percentile. The metro footprint = the urban cells contiguously connected to
the downtown cell on the H3 lattice. This mirrors how urban extents and
commuting zones are built: a thresholded core grown by adjacency.
"""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

from . import grid
from .config import Config


# ----------------------------------------------------------------------
def _minmax(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def _rank01(s: pd.Series) -> pd.Series:
    """Percentile rank in [0,1] — robust to skew/outliers (common with POIs)."""
    return s.rank(pct=True)


# ----------------------------------------------------------------------
def compute_land_value(cfg: Config, gdf):
    gdf = gdf.copy()
    scales = cfg["landvalue"]["decay_scale_km"]
    weights = cfg["landvalue"]["weights"]

    # Distance -> access (0..1-ish), then min-max for comparability.
    gdf["access_cbd"] = np.exp(-gdf["dist_cbd_km"] / scales["cbd"])
    road = gdf["dist_major_road_km"].fillna(gdf["dist_major_road_km"].max())
    gdf["access_major_road"] = np.exp(-road / scales["major_road"])

    components = {
        "access_cbd": _minmax(gdf["access_cbd"]),
        "access_major_road": _minmax(gdf["access_major_road"]),
        "establishment_access": _rank01(gdf["establishment_access"]),
        "poi_density": _rank01(gdf["poi_weighted_density"]),
        "road_density": _rank01(gdf["road_density_km"]),
    }
    for name, comp in components.items():
        gdf[f"norm_{name}"] = comp.values

    total_w = sum(weights[k] for k in components)
    score = sum(weights[k] * components[k] for k in components) / total_w
    gdf["land_value_score"] = score.values
    gdf["land_value_index"] = (_minmax(score) * 100).round(2).values
    return gdf


# ----------------------------------------------------------------------
def delineate_metro(cfg: Config, gdf):
    gdf = gdf.copy()
    bw = cfg["metro"]["builtup_weights"]
    builtup = (
        bw["poi_density"] * _rank01(gdf["poi_weighted_density"])
        + bw["road_density"] * _rank01(gdf["road_density_km"])
    )
    gdf["builtup_score"] = builtup.values

    pct = cfg["metro"]["urban_percentile"]
    thresh = np.percentile(builtup, pct)
    gdf["is_urban"] = (builtup >= thresh).values

    # Seed = downtown cell (or nearest urban cell to it).
    cbd_lat, cbd_lng = gdf.attrs.get("cbd", (gdf.lat.mean(), gdf.lng.mean()))
    res = cfg["grid"]["h3_resolution"]
    seed = grid.latlng_to_cell(cbd_lat, cbd_lng, res)
    urban = set(gdf.index[gdf["is_urban"]])
    if seed not in urban and urban:
        near = gdf.loc[list(urban)].sort_values("dist_cbd_km").index
        seed = near[0]

    metro_cells = _connected_component(seed, urban) if urban else set()
    gdf["in_metro"] = gdf.index.isin(metro_cells)
    gdf.attrs["metro_cell_count"] = len(metro_cells)
    gdf.attrs["urban_threshold"] = float(thresh)
    return gdf


def _connected_component(seed: str, allowed: set[str]) -> set[str]:
    """BFS over H3 neighbours, staying inside `allowed`."""
    if seed not in allowed:
        return set()
    seen = {seed}
    q = deque([seed])
    while q:
        c = q.popleft()
        for n in grid.grid_disk(c, 1):
            if n in allowed and n not in seen:
                seen.add(n)
                q.append(n)
    return seen


# ----------------------------------------------------------------------
def run_model(cfg: Config, gdf):
    """Convenience: land value + metro in one call."""
    gdf = compute_land_value(cfg, gdf)
    gdf = delineate_metro(cfg, gdf)
    return gdf
