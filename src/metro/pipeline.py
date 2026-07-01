"""End-to-end pipeline glue: data -> grid -> features (cached) -> model.

The expensive part (OSM fetch + feature engineering) is cached to GeoParquet
keyed on city + buffer + H3 resolution. The model layer is cheap and runs
fresh every call so the app's weight sliders are interactive.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from . import features as feat
from . import grid
from . import landvalue
from .config import Config
from .data import CityData, load_city_data


def features_path(cfg: Config, synthetic: bool = False) -> Path:
    slug = cfg.city_cache_slug()
    res = cfg["grid"]["h3_resolution"]
    buf = cfg["city"]["study_buffer_km"]
    tag = "_synth" if synthetic else ""
    return cfg.data_dir / f"{slug}_features_res{res}_{buf:g}km{tag}.parquet"


def build_features(cfg: Config, city: CityData, progress=None) -> gpd.GeoDataFrame:
    cells = grid.build_grid(city.study_region, cfg["grid"]["h3_resolution"])
    gdf = feat.build_features(cfg, city, cells, progress=progress)
    # Persist scalars into columns so they survive a parquet round-trip.
    cbd_lat, cbd_lng = gdf.attrs.get("cbd", (gdf.lat.mean(), gdf.lng.mean()))
    gdf["cbd_lat"] = cbd_lat
    gdf["cbd_lng"] = cbd_lng
    gdf["n_grid_cells"] = gdf.attrs.get("n_grid_cells", len(gdf))
    gdf["n_water_excluded"] = gdf.attrs.get("n_water_excluded", 0)
    gdf["data_source"] = city.source
    gdf["cache_place"] = cfg["city"]["place"]
    gdf["cache_osm_id"] = cfg["city"].get("osm_id")
    return gdf


def _cache_matches_city(gdf: gpd.GeoDataFrame, city: CityData) -> bool:
    """Reject old poisoned feature caches from synthetic fallback/geocode misses."""
    if city.source == "synthetic":
        return "data_source" in gdf.columns and bool((gdf["data_source"] == "synthetic").any())
    if "data_source" in gdf.columns and not bool((gdf["data_source"] == city.source).all()):
        return False
    if gdf.empty:
        return False
    cache_geom: BaseGeometry = gdf.geometry.union_all()
    return bool(cache_geom.intersects(city.study_region))


def load_or_build_features(
    cfg: Config, rebuild: bool = False, force_synthetic: bool = False, progress=None
) -> tuple[gpd.GeoDataFrame, CityData]:
    city = load_city_data(cfg, force_synthetic=force_synthetic, progress=progress)
    path = features_path(cfg, synthetic=force_synthetic)
    if path.exists() and not rebuild:
        if progress is not None:
            progress(0.85, "Loading cached feature table…")
        gdf = gpd.read_parquet(path).set_index("h3", drop=False)
        if not _cache_matches_city(gdf, city):
            if progress is not None:
                progress(0.86, "Cached features do not match this city; rebuilding…")
            gdf = build_features(cfg, city, progress=progress)
            if city.source == "osm" or force_synthetic:
                gdf.to_parquet(path)
    else:
        gdf = build_features(cfg, city, progress=progress)
        if city.source == "osm" or force_synthetic:
            gdf.to_parquet(path)
    if progress is not None:
        progress(1.0, "Ready")
    # Restore scalars into attrs for the model / reporting stages.
    if "cbd_lat" in gdf.columns:
        gdf.attrs["cbd"] = (float(gdf["cbd_lat"].iloc[0]), float(gdf["cbd_lng"].iloc[0]))
    if "n_grid_cells" in gdf.columns:
        gdf.attrs["n_grid_cells"] = int(gdf["n_grid_cells"].iloc[0])
        gdf.attrs["n_water_excluded"] = int(gdf["n_water_excluded"].iloc[0])
    return gdf, city


def run(cfg: Config, rebuild: bool = False, force_synthetic: bool = False, progress=None):
    """Full pipeline returning the modelled cell GeoDataFrame + city layers."""
    gdf, city = load_or_build_features(
        cfg, rebuild=rebuild, force_synthetic=force_synthetic, progress=progress)
    gdf = landvalue.run_model(cfg, gdf)
    return gdf, city


def output_paths(cfg: Config, synthetic: bool = False) -> dict[str, Path]:
    slug = cfg.city_cache_slug()
    res = cfg["grid"]["h3_resolution"]
    tag = "_synth" if synthetic else ""
    return {
        "geojson": cfg.data_dir / f"{slug}_metro_res{res}{tag}.geojson",
        "map": cfg.data_dir / f"{slug}_context_map_res{res}{tag}.html",
    }


def build_context_outputs(cfg: Config, gdf: gpd.GeoDataFrame, city: CityData,
                          rebuild: bool = False, synthetic: bool = False) -> dict[str, Path]:
    """Write (and cache) the metro polygon GeoJSON + the folium context map."""
    from . import mapviz

    paths = output_paths(cfg, synthetic=synthetic)
    if rebuild or not paths["map"].exists() or not paths["geojson"].exists():
        mapviz.save_polygon_geojson(gdf, paths["geojson"])
        mapviz.save_map(mapviz.build_context_map(cfg, gdf, city), paths["map"])
    return paths
