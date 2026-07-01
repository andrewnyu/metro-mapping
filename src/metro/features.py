"""Per-cell feature engineering.

Given the H3 grid and the OSM layers, produce one row per hex cell with the
spatial-economics features that drive land value:

    dist_cbd_km            haversine distance to the detected downtown
    dist_major_road_km     distance to the nearest arterial
    poi_count              POIs whose point falls in the cell
    poi_weighted_density   sum of category weights in the cell
    establishment_access   gravity access:  sum_j w_j * exp(-d_ij / scale)
    road_density_km        total road length clipped to the cell
    poi_count_<category>    per-category counts (handy for inspection)

The output is a GeoDataFrame keyed by H3 id with the cell polygon geometry,
ready for the model stage and direct rendering on a map.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist
from shapely.geometry import Point

from . import grid
from .config import Config
from .data import CityData

WGS84 = "EPSG:4326"
EARTH_R_KM = 6371.0088


def haversine_km(lat1, lng1, lat2, lng2) -> np.ndarray:
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    d = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(d))


def detect_cbd(cfg: Config, cells: list[str], pois: gpd.GeoDataFrame) -> tuple[float, float]:
    """Downtown = peak of neighbourhood-smoothed weighted POI density.

    Honour a manual override from config if both lat/lng are set.
    """
    man = cfg.get("cbd", {})
    if man.get("lat") is not None and man.get("lng") is not None:
        return float(man["lat"]), float(man["lng"])

    res = cfg["grid"]["h3_resolution"]
    weighted = pd.Series(0.0, index=cells)
    if not pois.empty:
        pcell = [grid.latlng_to_cell(la, ln, res) for la, ln in zip(pois["lat"], pois["lng"])]
        w = pois.assign(cell=pcell).groupby("cell")["weight"].sum()
        weighted = weighted.add(w, fill_value=0.0).reindex(cells).fillna(0.0)

    # Smooth over a 2-ring neighbourhood so a single mega-POI doesn't win.
    idx = {c: i for i, c in enumerate(cells)}
    vals = weighted.values
    smoothed = np.zeros(len(cells))
    for i, c in enumerate(cells):
        nbrs = [idx[n] for n in grid.grid_disk(c, 2) if n in idx]
        smoothed[i] = vals[nbrs].mean() if nbrs else vals[i]
    best = cells[int(np.argmax(smoothed))]
    return grid.cell_to_latlng(best)


def build_features(cfg: Config, city: CityData, cells: list[str] | None = None,
                   progress=None) -> gpd.GeoDataFrame:
    def _p(frac, msg):
        if progress is not None:
            progress(frac, msg)

    res = cfg["grid"]["h3_resolution"]
    _p(0.80, "Building H3 grid…")
    if cells is None:
        cells = grid.build_grid(city.study_region, res)

    latlng = grid.cells_to_latlng(cells)  # (N,2) lat,lng
    gdf = gpd.GeoDataFrame(
        {"h3": cells, "lat": latlng[:, 0], "lng": latlng[:, 1]},
        geometry=[grid.cell_polygon(c) for c in cells],
        crs=WGS84,
    ).set_index("h3", drop=False)
    metric_crs = gdf.estimate_utm_crs()

    # --- cheap, mask-relevant features first ---------------------------
    _p(0.85, "Counting POIs & road density per cell…")
    _add_poi_counts(gdf, city.pois, res, cfg)
    gdf["road_density_km"] = _road_density(gdf, city.roads, metric_crs)

    # --- drop water cells, then work only on the land grid -------------
    _p(0.90, "Masking out water cells…")
    n_grid = len(gdf)
    gdf = apply_water_mask(cfg, gdf, city)
    n_land = len(gdf)
    cell_xy = _to_xy(gdf.lat.values, gdf.lng.values, gdf, metric_crs)

    # --- CBD distance (downtown from POI density on the land grid) -----
    _p(0.94, "Detecting downtown & computing accessibility…")
    cbd_lat, cbd_lng = detect_cbd(cfg, list(gdf.index), city.pois)
    gdf["dist_cbd_km"] = haversine_km(gdf.lat.values, gdf.lng.values, cbd_lat, cbd_lng)
    gdf.attrs["cbd"] = (cbd_lat, cbd_lng)

    # --- distance to nearest major road --------------------------------
    gdf["dist_major_road_km"] = _dist_to_roads(gdf, city.major_roads, metric_crs)

    # --- gravity establishment access ----------------------------------
    gdf["establishment_access"] = _establishment_access(
        cell_xy, city.pois, metric_crs,
        scale_km=cfg["landvalue"]["decay_scale_km"].get("establishment", 1.5),
    )
    gdf.attrs["n_land_cells"] = n_land
    gdf.attrs["n_grid_cells"] = n_grid
    gdf.attrs["n_water_excluded"] = n_grid - n_land
    return gdf


def apply_water_mask(cfg: Config, gdf: gpd.GeoDataFrame, city: CityData) -> gpd.GeoDataFrame:
    """Keep only land cells.

    A cell is water if its centroid sits in a mapped water polygon, OR (the
    open-sea case) it is not "reachable": no road in the cell or any neighbour,
    no POI, and it doesn't touch the city boundary.
    """
    keep = pd.Series(True, index=gdf.index)

    # 1) centroid inside a mapped water body
    if city.water is not None and not city.water.empty:
        water_union = city.water.geometry.union_all()
        centroids = gpd.points_from_xy(gdf["lng"], gdf["lat"])
        in_water = gpd.GeoSeries(centroids, crs=WGS84).within(water_union).values
        keep &= ~in_water

    # 2) reachability — removes the open ocean (no water polygon exists for it)
    if cfg["water"].get("require_reachable", True):
        road_cells = set(gdf.index[gdf["road_density_km"] > 0])
        near_road = np.array(
            [any(n in road_cells for n in grid.grid_disk(c, 1)) for c in gdf.index]
        )
        has_poi = (gdf.get("poi_count", pd.Series(0, index=gdf.index)) > 0).to_numpy()
        touches_boundary = np.zeros(len(gdf), dtype=bool)
        if city.boundary is not None and not city.boundary.empty:
            bnd = city.boundary.geometry.union_all()
            touches_boundary = gdf.geometry.intersects(bnd).to_numpy()
        reachable = near_road | has_poi | touches_boundary
        keep &= pd.Series(reachable, index=gdf.index)

    out = gdf[keep].copy()
    out["is_land"] = True
    return out


# ----------------------------------------------------------------------
def _to_xy(lat, lng, ref_gdf, metric_crs) -> np.ndarray:
    pts = gpd.GeoSeries([Point(x, y) for y, x in zip(lat, lng)], crs=WGS84).to_crs(metric_crs)
    return np.column_stack([pts.x.values, pts.y.values])


def _dist_to_roads(gdf: gpd.GeoDataFrame, roads: gpd.GeoDataFrame, metric_crs) -> np.ndarray:
    if roads.empty:
        return np.full(len(gdf), np.nan)
    cell_pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(gdf.lng, gdf.lat), crs=WGS84).to_crs(metric_crs)
    roads_m = roads.to_crs(metric_crs)
    joined = gpd.sjoin_nearest(cell_pts, roads_m[["geometry"]], distance_col="d_m")
    # sjoin_nearest can emit ties (duplicate rows); keep the closest per cell.
    d = joined.groupby(level=0)["d_m"].min().reindex(range(len(gdf))).values
    return d / 1000.0


def _add_poi_counts(gdf: gpd.GeoDataFrame, pois: gpd.GeoDataFrame, res: int, cfg: Config) -> None:
    categories = list(cfg["poi_categories"].keys())
    for cat in categories:
        gdf[f"poi_count_{cat}"] = 0
    gdf["poi_count"] = 0
    gdf["poi_weighted_density"] = 0.0
    if pois.empty:
        return
    pcell = [grid.latlng_to_cell(la, ln, res) for la, ln in zip(pois["lat"], pois["lng"])]
    p = pois.assign(cell=pcell)
    total = p.groupby("cell").size()
    wsum = p.groupby("cell")["weight"].sum()
    gdf["poi_count"] = total.reindex(gdf.index).fillna(0).astype(int).values
    gdf["poi_weighted_density"] = wsum.reindex(gdf.index).fillna(0.0).values
    bycat = p.groupby(["cell", "category"]).size().unstack(fill_value=0)
    for cat in categories:
        if cat in bycat.columns:
            gdf[f"poi_count_{cat}"] = bycat[cat].reindex(gdf.index).fillna(0).astype(int).values


def _establishment_access(cell_xy: np.ndarray, pois: gpd.GeoDataFrame, metric_crs, scale_km: float) -> np.ndarray:
    if pois.empty:
        return np.zeros(len(cell_xy))
    p = pois.to_crs(metric_crs)
    poi_xy = np.column_stack([p.geometry.x.values, p.geometry.y.values])
    w = pois["weight"].values
    scale_m = scale_km * 1000.0
    # Chunk the distance matrix to bound memory for large grids.
    out = np.zeros(len(cell_xy))
    chunk = 2000
    for s in range(0, len(cell_xy), chunk):
        d = cdist(cell_xy[s : s + chunk], poi_xy)  # meters
        out[s : s + chunk] = (np.exp(-d / scale_m) * w).sum(axis=1)
    return out


def _road_density(gdf: gpd.GeoDataFrame, roads: gpd.GeoDataFrame, metric_crs) -> np.ndarray:
    if roads.empty:
        return np.zeros(len(gdf))
    cells_m = gdf[["h3", "geometry"]].to_crs(metric_crs)
    roads_m = roads[["geometry"]].to_crs(metric_crs)
    inter = gpd.overlay(roads_m, cells_m, how="intersection", keep_geom_type=False)
    inter["len_km"] = inter.geometry.length / 1000.0
    by_cell = inter.groupby("h3")["len_km"].sum()
    return by_cell.reindex(gdf.index).fillna(0.0).values
