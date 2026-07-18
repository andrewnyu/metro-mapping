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
Kept deliberately separate from the (relative) land-value score. A cell is
"urban" by an ABSOLUTE bar — it has at least `min_poi_per_cell` establishments
or a dense road grid with enough nearby establishment gravity — judged on its
own terms, not by percentile rank within the city. The metro footprint = the
urban cells contiguously connected to the downtown cell on the H3 lattice. A
small bridge may cross excluded non-land cells such as water, but it may not hop
over ordinary non-urban land. A separate connector pass may add short chains of
weak-but-supported land cells only when they join the core to a meaningful
nearby urban cluster; connector cells are exported separately for audit. This
keeps places like Toledo from attaching to Cebu through mountain/rural gaps,
while avoiding premature cuts through lightly mapped urban corridors.
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


def _positive_rank01(s: pd.Series) -> pd.Series:
    """Percentile rank for positive signal only; true zero stays zero."""
    out = pd.Series(0.0, index=s.index)
    pos = s > 0
    if pos.any():
        out.loc[pos] = s.loc[pos].rank(pct=True)
    return out


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
        "poi_density": _positive_rank01(gdf["poi_weighted_density"]),
        "road_density": _positive_rank01(gdf["road_density_km"]),
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
    """Metro = urban cells (absolute bar) contiguously connected to downtown.

    Urban is judged per cell on its own terms — establishments, or a dense road
    grid supported by nearby establishment access — not by percentile rank, so
    the footprint reflects the real built-up extent. Small non-land gaps are
    bridged so districts separated by a channel stay attached. Short supported
    land gaps may be added as connector cells only when they attach a meaningful
    nearby urban cluster.
    """
    gdf = gdf.copy()
    m = cfg["metro"]

    # Relative built-up score is kept only as a display metric for the web app.
    bw = m["builtup_weights"]
    gdf["builtup_score"] = (
        bw["poi_density"] * _positive_rank01(gdf["poi_weighted_density"])
        + bw["road_density"] * _positive_rank01(gdf["road_density_km"])
    ).values

    # Absolute urban criterion: enough establishments OR a dense-enough road
    # grid with nearby activity. The access guard prevents rural through-roads
    # in very large administrative cities from ballooning the metro footprint.
    min_poi = float(m.get("min_poi_per_cell", 3))
    min_road = float(m.get("min_road_km_per_cell", 4.0))
    min_road_access = float(m.get("min_establishment_access_for_road_cell", 0))
    poi_urban = gdf["poi_count"] >= min_poi
    road_urban = gdf["road_density_km"] >= min_road
    if min_road_access > 0:
        road_urban &= gdf["establishment_access"] >= min_road_access
    gdf["is_urban"] = (poi_urban | road_urban).values

    # Seed = downtown cell (or nearest urban cell to it).
    cbd_lat, cbd_lng = gdf.attrs.get("cbd", (gdf.lat.mean(), gdf.lng.mean()))
    res = cfg["grid"]["h3_resolution"]
    seed = grid.latlng_to_cell(cbd_lat, cbd_lng, res)
    urban = set(gdf.index[gdf["is_urban"]])
    if seed not in urban and urban:
        near = gdf.loc[list(urban)].sort_values("dist_cbd_km").index
        seed = near[0]

    bridge = int(m.get("bridge_gap", 2))
    land = set(gdf.index)
    metro_cells = _connected_component(seed, urban, bridge=bridge, land=land) if urban else set()
    connector_cells = _connector_cells(gdf, urban, metro_cells, land, m) if metro_cells else set()
    if connector_cells:
        metro_cells = _connected_component(
            seed, urban | connector_cells, bridge=bridge, land=land)
    gdf["is_connector"] = gdf.index.isin(connector_cells)
    gdf["in_metro"] = gdf.index.isin(metro_cells)
    gdf.attrs["metro_cell_count"] = len(metro_cells)
    gdf.attrs["urban_cell_count"] = len(urban)
    gdf.attrs["connector_cell_count"] = len(connector_cells)
    return gdf


def _connector_cells(gdf, urban: set[str], metro_cells: set[str],
                     land: set[str], m) -> set[str]:
    """Find short supported land gaps that connect real urban clusters."""
    max_gap = int(m.get("connector_gap", 0))
    if max_gap <= 0:
        return set()

    min_component = int(m.get("connector_min_component_cells", 12))
    connectors: set[str] = set()
    connected = set(metro_cells)

    changed = True
    while changed:
        changed = False
        outside = urban - connected
        for comp in _components(outside):
            if len(comp) < min_component:
                continue
            path = _nearest_supported_path(
                gdf, comp, connected, land, urban, max_gap=max_gap, m=m)
            if path is None:
                continue
            gap_cells = set(path[1:-1])
            connectors |= gap_cells
            connected |= set(comp) | gap_cells
            changed = True
    return connectors


def _components(cells: set[str]) -> list[set[str]]:
    seen: set[str] = set()
    out: list[set[str]] = []
    for cell in cells:
        if cell in seen:
            continue
        comp = {cell}
        seen.add(cell)
        q = deque([cell])
        while q:
            c = q.popleft()
            for n in grid.grid_disk(c, 1):
                if n in cells and n not in seen:
                    seen.add(n)
                    comp.add(n)
                    q.append(n)
        out.append(comp)
    return out


def _nearest_supported_path(gdf, comp: set[str], connected: set[str],
                            land: set[str], urban: set[str], max_gap: int, m):
    best: tuple[int, list[str]] | None = None
    max_distance = max_gap + 1
    for a in comp:
        for b in connected:
            try:
                dist = grid.grid_distance(a, b)
            except Exception:
                continue
            if dist < 2 or dist > max_distance:
                continue
            if best is not None and dist >= best[0]:
                continue
            try:
                path = grid.grid_path_cells(a, b)
            except Exception:
                continue
            if _is_supported_connector_path(gdf, path, land, urban, max_gap, m):
                best = (dist, path)
    return best[1] if best is not None else None


def _is_supported_connector_path(gdf, path: list[str], land: set[str],
                                 urban: set[str], max_gap: int, m) -> bool:
    gap = path[1:-1]
    if not gap or len(gap) > max_gap:
        return False
    min_road = float(m.get("connector_min_road_km_per_cell", 2.0))
    min_access = float(m.get("connector_min_establishment_access", 3.0))
    for cell in gap:
        if cell not in land or cell in urban:
            return False
        row = gdf.loc[cell]
        if row["road_density_km"] < min_road:
            return False
        if row["establishment_access"] < min_access:
            return False
    return True


def _connected_component(seed: str, allowed: set[str], bridge: int = 1,
                         land: set[str] | None = None) -> set[str]:
    """BFS over the H3 lattice, staying inside `allowed`.

    `bridge` is how many rings a step may span. Adjacent urban cells always
    connect. Longer jumps are allowed only when the skipped cells are outside
    the land grid, so a channel can be crossed but ordinary rural/mountain land
    cannot be skipped.
    """
    if seed not in allowed:
        return set()
    land = land or allowed
    seen = {seed}
    q = deque([seed])
    while q:
        c = q.popleft()
        for n in grid.grid_disk(c, bridge):
            if n in allowed and n not in seen and _can_step(c, n, land):
                seen.add(n)
                q.append(n)
    return seen


def _can_step(a: str, b: str, land: set[str]) -> bool:
    if grid.grid_distance(a, b) <= 1:
        return True
    try:
        path = grid.grid_path_cells(a, b)
    except Exception:
        return False
    # Endpoints are urban land cells. Interior land cells are real non-urban
    # gaps, so do not jump them; missing land cells are water/excluded context.
    return all(c not in land for c in path[1:-1])


# ----------------------------------------------------------------------
def run_model(cfg: Config, gdf):
    """Convenience: land value + metro in one call."""
    gdf = compute_land_value(cfg, gdf)
    gdf = delineate_metro(cfg, gdf)
    return gdf
