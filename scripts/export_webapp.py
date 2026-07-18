#!/usr/bin/env python3
"""Export the modelled data as static files for the MapLibre web app.

Writes, per city, into webapp/data/:
    <slug>_cells.geojson   H3 land cells + features (short property keys)
    <slug>_water.geojson   excluded water/open-sea cells + mapped water polygons
    <slug>_metro.geojson   the metro-area polygon
    <slug>_pois.geojson    POIs (category)
and a single manifest.json describing the cities, metrics and default weights.

The web app recomputes the land-value index live in the browser from the
exported normalised components (c0..c4), so the weight sliders stay interactive
with no server — same spirit as the bank-deposits app's period slider.

Usage:
    python scripts/export_webapp.py                       # config city
    python scripts/export_webapp.py --places "Cebu City, Philippines" "Iloilo City, Philippines"
    python scripts/export_webapp.py --synthetic
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from metro import grid, landvalue, mapviz, pipeline  # noqa: E402
from metro.config import load_config, normalise_osm_id  # noqa: E402

WEBAPP_DATA = Path(__file__).resolve().parents[1] / "webapp" / "data"

# component order must match the app's weight sliders
COMPONENTS = ["access_cbd", "access_major_road", "establishment_access",
              "poi_density", "road_density"]

# exported metric definitions; the app's view tabs choose the active one
METRICS = [
    {"key": "land_value", "label": "Land-value index", "prop": "lv", "log": False, "reverse": False},
    {"key": "establishment_access", "label": "Establishment access", "prop": "ea", "log": True, "reverse": False},
    {"key": "poi_density", "label": "POI density", "prop": "pwd", "log": True, "reverse": False},
    {"key": "road_density", "label": "Road density", "prop": "rdk", "log": False, "reverse": False},
    {"key": "dist_cbd", "label": "Distance to downtown", "prop": "dcbd", "log": False, "reverse": True},
    {"key": "builtup", "label": "Built-up score", "prop": "bs", "log": False, "reverse": False},
]
PRICE_METRIC = {
    "key": "land_price", "label": "Estimated commercial land price (PHP/sqm)",
    "prop": "ppsm", "log": True, "reverse": False,
}


def r(x, n=4):
    return round(float(x), n)


def _norm_place(place: str) -> str:
    return " ".join(place.lower().replace(",", " ").split())


def _fallback_osm_id(cfg, place: str) -> str | None:
    fallbacks = cfg["city"].get("osm_id_fallbacks", {}) or {}
    norm = _norm_place(place)
    for key, osm_id in fallbacks.items():
        key_norm = _norm_place(str(key))
        if norm == key_norm or norm.startswith(key_norm + " "):
            return str(osm_id)
    return None


def _resolved_place(cfg, place: str) -> str:
    aliases = cfg["city"].get("place_aliases", {}) or {}
    norm = _norm_place(place)
    for key, value in aliases.items():
        if norm == _norm_place(str(key)):
            return str(value)
    return place


def _area_km2(gdf_or_geom) -> float:
    if isinstance(gdf_or_geom, gpd.GeoDataFrame):
        metric = gdf_or_geom.to_crs(gdf_or_geom.estimate_utm_crs())
        return float(metric.geometry.union_all().area / 1_000_000)
    gs = gpd.GeoSeries([gdf_or_geom], crs="EPSG:4326")
    metric = gs.to_crs(gs.estimate_utm_crs())
    return float(metric.iloc[0].area / 1_000_000)


def export_city(
    cfg, place: str, synthetic: bool = False, rebuild: bool = False,
    progress=None, osm_id: str | None = None, require_osm: bool = False,
) -> dict:
    place = _resolved_place(cfg, place)
    cfg["city"]["place"] = place
    raw_osm_id = osm_id or _fallback_osm_id(cfg, place)
    cfg["city"]["osm_id"] = normalise_osm_id(raw_osm_id) if raw_osm_id else None
    base_slug = cfg.city_slug()
    slug = f"{base_slug}_synthetic" if synthetic else base_slug
    # Pipeline drives 0..0.9; file writing takes the last 10%.
    gdf, city = pipeline.run(
        cfg, rebuild=rebuild, force_synthetic=synthetic,
        progress=(lambda f, m: progress(f * 0.9, m)) if progress else None)
    if require_osm and not synthetic and city.source != "osm":
        if cfg["city"].get("osm_id"):
            detail = f" Tried exact OSM ID {cfg['city']['osm_id']}."
        else:
            detail = (
                " Try a fuller name, e.g. “Davao City, Philippines”, or provide "
                "an exact OSM relation ID."
            )
        if city.source_error:
            detail += f" OSM error: {city.source_error}"
        raise LookupError(f"Couldn't build “{place}” from OpenStreetMap.{detail}")
    if progress:
        progress(0.92, "Writing GeoJSON layers…")

    # --- cells geojson (compact properties) ----------------------------
    ex = gdf[["geometry"]].copy()
    ex["id"] = gdf["h3"].values
    ex["lv"] = gdf["land_value_index"].round(2).values
    ex["rvs"] = gdf["relative_value_share"].round(8).values
    if gdf["land_price_php_sqm"].notna().any():
        ex["ppsm"] = gdf["land_price_php_sqm"].round(0).values
        ex["plo"] = gdf["land_price_low_php_sqm"].round(0).values
        ex["phi"] = gdf["land_price_high_php_sqm"].round(0).values
    for i, comp in enumerate(COMPONENTS):
        ex[f"c{i}"] = gdf[f"norm_{comp}"].round(4).values
    ex["ea"] = gdf["establishment_access"].round(3).values
    ex["pwd"] = gdf["poi_weighted_density"].round(3).values
    ex["rdk"] = gdf["road_density_km"].round(3).values
    ex["dcbd"] = gdf["dist_cbd_km"].round(3).values
    ex["pc"] = gdf["poi_count"].astype(int).values
    ex["bs"] = gdf["builtup_score"].round(4).values
    ex["mt"] = gdf["in_metro"].astype(int).values
    ex["cn"] = (
        gdf["is_connector"].astype(int).values
        if "is_connector" in gdf.columns else 0
    )
    ex = ex.reset_index(drop=True)
    (WEBAPP_DATA / f"{slug}_cells.geojson").write_text(ex.to_json())

    # --- water/excluded cells geojson ----------------------------------
    # These cells are excluded from the land-value model, but rendering them
    # under the land grid prevents bays/rivers/open water from looking like
    # unexplained holes in the metro map.
    all_cells = set(grid.build_grid(city.study_region, cfg["grid"]["h3_resolution"]))
    water_cells = sorted(all_cells - set(gdf.index))
    water_parts = []
    if water_cells:
        water_parts.append(gpd.GeoDataFrame(
            {"id": water_cells, "kind": ["excluded_cell"] * len(water_cells)},
            geometry=[grid.cell_polygon(c) for c in water_cells],
            crs="EPSG:4326",
        ))
    if city.water is not None and not city.water.empty:
        mapped = city.water[["geometry"]].copy()
        mapped["id"] = [f"water_{i}" for i in range(len(mapped))]
        mapped["kind"] = "mapped_water"
        water_parts.append(mapped[["id", "kind", "geometry"]])
    if water_parts:
        water = gpd.GeoDataFrame(pd.concat(water_parts, ignore_index=True), crs="EPSG:4326")
    else:
        water = gpd.GeoDataFrame({"id": [], "kind": []}, geometry=[], crs="EPSG:4326")
    (WEBAPP_DATA / f"{slug}_water.geojson").write_text(water.to_json())

    # --- metro polygon -------------------------------------------------
    metro_fc = mapviz.polygon_geojson(gdf)
    (WEBAPP_DATA / f"{slug}_metro.geojson").write_text(json.dumps(metro_fc))

    # --- POIs ----------------------------------------------------------
    pois = city.pois
    if len(pois) > 4000:
        pois = pois.sample(4000, random_state=0)
    poi_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"cat": row["category"]},
             "geometry": {"type": "Point", "coordinates": [r(row["lng"], 5), r(row["lat"], 5)]}}
            for _, row in pois.iterrows()
        ],
    }
    (WEBAPP_DATA / f"{slug}_pois.geojson").write_text(json.dumps(poi_fc))

    cbd_lat, cbd_lng = gdf.attrs.get("cbd", (gdf.lat.mean(), gdf.lng.mean()))
    b = gdf.total_bounds  # minx,miny,maxx,maxy
    metro_area = metro_fc["features"][0]["properties"]["area_km2"] if metro_fc["features"] else 0
    n_metro = int(gdf["in_metro"].sum())
    n_connectors = int(gdf["is_connector"].sum()) if "is_connector" in gdf.columns else 0
    city_area = _area_km2(city.boundary) if city.boundary is not None and not city.boundary.empty else 0
    study_area = _area_km2(city.study_region)
    land_area = _area_km2(gdf)
    econ = gdf.attrs.get("economics", {})
    price_model = gdf.attrs.get("price_model", {})
    first = gdf.iloc[0]
    print(f"  {place:32} land={len(gdf):>5}  metro={int(gdf['in_metro'].sum()):>4}  "
          f"water_excl={gdf.attrs.get('n_water_excluded', 0):>4}")
    return {
        "slug": slug,
        "name": place.split(",")[0] + (" (synthetic)" if synthetic else ""),
        "place": place,
        "osm_id": cfg["city"].get("osm_id"),
        "center": [r(cbd_lng, 5), r(cbd_lat, 5)],
        "bbox": [r(b[0], 5), r(b[1], 5), r(b[2], 5), r(b[3], 5)],
        "cells": f"{slug}_cells.geojson", "metro": f"{slug}_metro.geojson",
        "pois": f"{slug}_pois.geojson", "water": f"{slug}_water.geojson",
        "n_land": int(len(gdf)), "n_water": int(gdf.attrs.get("n_water_excluded", 0)),
        "n_metro": n_metro, "n_connectors": n_connectors, "n_pois": int(len(city.pois)),
        "metro_km2": r(metro_area, 1), "city_km2": r(city_area, 1),
        "study_km2": r(study_area, 1), "land_km2": r(land_area, 1),
        "source": city.source,
        "source_error": city.source_error,
        "population": int(first["city_population"]) if pd.notna(first["city_population"]) else None,
        "bank_deposits_php": r(first["bank_deposits_php"], 0)
        if pd.notna(first["bank_deposits_php"]) else None,
        "local_tax_revenue_php": r(first["local_tax_revenue_php"], 0)
        if pd.notna(first["local_tax_revenue_php"]) else None,
        "economics": econ,
        "price_model_status": price_model.get("status", "not_trained"),
        "price_property_segment": price_model.get("property_segment"),
        "n_price_cells": price_model.get("priced_cell_count", 0),
        "price_geography": price_model.get("price_geography"),
        "price_model_n_labels": price_model.get("n_labels"),
        "price_model_n_market_observations": price_model.get("n_market_observations"),
        "price_model_n_cities": price_model.get("n_cities"),
        "price_model_mae_php_sqm": price_model.get("mae_php_sqm"),
        "price_model_median_ape": price_model.get("median_ape"),
        "price_market_baseline_php_sqm": price_model.get("market_baseline_php_sqm"),
        "price_market_baseline_source": price_model.get("market_baseline_source"),
        "price_interval_method": price_model.get("interval_method"),
        "price_anchor_n_observations": price_model.get("local_anchor_n_observations"),
        "price_anchor_n_listings": price_model.get("local_anchor_n_listings"),
        "price_anchor_market_areas": price_model.get("local_anchor_market_areas", []),
        "price_anchor_score_quantile": price_model.get("local_anchor_score_quantile"),
        "price_anchor_confidence_level": price_model.get("local_anchor_confidence_level"),
        "price_comparable_city_method": price_model.get("comparable_city_method"),
        "price_comparable_city_donors": price_model.get("comparable_city_donors", []),
        "price_target_bank_deposits_per_land_cell_php": price_model.get(
            "target_bank_deposits_per_land_cell_php"),
        "price_target_n_land_cells": price_model.get("target_n_land_cells"),
        "price_model_validation": price_model.get("validation"),
    }


def manifest_path() -> Path:
    return WEBAPP_DATA / "manifest.json"


def read_manifest() -> dict | None:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else None


def build_manifest(cfg, cities: list[dict]) -> dict:
    metrics = list(METRICS)
    if any(c.get("price_model_status") == "trained" for c in cities):
        metrics.insert(0, PRICE_METRIC)
    return {
        "cities": cities,
        "components": COMPONENTS,
        "component_labels": ["Downtown", "Major road", "Establishments", "POI density", "Road density"],
        "weights_default": landvalue.effective_weights(cfg),
        "weights_model": landvalue.weight_model_metadata(cfg),
        "metrics": metrics,
        "poi_categories": list(cfg["poi_categories"].keys()),
    }


def write_manifest(manifest: dict) -> None:
    manifest_path().write_text(json.dumps(manifest, indent=0))


def upsert_city(cfg, city: dict) -> dict:
    """Add (or replace) one city in the on-disk manifest and return it."""
    man = read_manifest() or build_manifest(cfg, [])
    man["cities"] = [c for c in man["cities"] if c["slug"] != city["slug"]] + [city]
    man["cities"].sort(key=lambda c: c["name"])
    refreshed = build_manifest(cfg, man["cities"])
    for key in (
        "components", "component_labels", "weights_default", "weights_model",
        "metrics", "poi_categories",
    ):
        man[key] = refreshed[key]
    write_manifest(man)
    return man


def register_exports(cfg, cities: list[dict], replace: bool = False) -> dict:
    """Register exports while preserving saved cities unless replacement is explicit."""
    if replace:
        manifest = build_manifest(cfg, cities)
        write_manifest(manifest)
        return manifest

    manifest = read_manifest() or build_manifest(cfg, [])
    for city in cities:
        manifest = upsert_city(cfg, city)
    return manifest


def export_and_register(
    place: str, synthetic: bool = False, rebuild: bool = False,
    progress=None, osm_id: str | None = None,
) -> dict:
    """Build one city, write its files, upsert it into the manifest. Used by the server."""
    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    city = export_city(
        cfg, place, synthetic=synthetic, rebuild=rebuild,
        progress=progress, osm_id=osm_id, require_osm=not synthetic,
    )
    upsert_city(cfg, city)
    if progress:
        progress(1.0, "Done")
    return city


def main() -> None:
    ap = argparse.ArgumentParser(description="Export static data for the web app.")
    ap.add_argument("--places", nargs="*", help="City names (default: config city).")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument(
        "--replace-manifest", action="store_true",
        help="Replace the saved-city list instead of upserting exported cities.",
    )
    args = ap.parse_args()

    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    places = args.places or [cfg["city"]["place"]]

    print(f"Exporting {len(places)} city(ies) -> {WEBAPP_DATA}")
    cities = [
        export_city(
            cfg, p, synthetic=args.synthetic, rebuild=args.rebuild,
            require_osm=not args.synthetic,
        )
        for p in places
    ]
    manifest = register_exports(cfg, cities, replace=args.replace_manifest)
    print(f"Wrote manifest.json ({len(manifest['cities'])} cities). "
          "Serve with: bash webapp/serve.sh")


if __name__ == "__main__":
    main()
