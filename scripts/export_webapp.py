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

from metro import grid, mapviz, pipeline  # noqa: E402
from metro.config import load_config, normalise_osm_id  # noqa: E402

WEBAPP_DATA = Path(__file__).resolve().parents[1] / "webapp" / "data"

# component order must match the app's weight sliders
COMPONENTS = ["access_cbd", "access_major_road", "establishment_access",
              "poi_density", "road_density"]

# metrics offered in the "Colour by" dropdown
METRICS = [
    {"key": "land_value", "label": "Land-value index", "prop": "lv", "log": False, "reverse": False},
    {"key": "establishment_access", "label": "Establishment access", "prop": "ea", "log": True, "reverse": False},
    {"key": "poi_density", "label": "POI density", "prop": "pwd", "log": True, "reverse": False},
    {"key": "road_density", "label": "Road density", "prop": "rdk", "log": False, "reverse": False},
    {"key": "dist_cbd", "label": "Distance to downtown", "prop": "dcbd", "log": False, "reverse": True},
    {"key": "builtup", "label": "Built-up score", "prop": "bs", "log": False, "reverse": False},
]


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


def export_city(
    cfg, place: str, synthetic: bool = False, rebuild: bool = False,
    progress=None, osm_id: str | None = None,
) -> dict:
    cfg["city"]["place"] = place
    raw_osm_id = osm_id or _fallback_osm_id(cfg, place)
    cfg["city"]["osm_id"] = normalise_osm_id(raw_osm_id) if raw_osm_id else None
    slug = cfg.city_slug()
    # Pipeline drives 0..0.9; file writing takes the last 10%.
    gdf, city = pipeline.run(
        cfg, rebuild=rebuild, force_synthetic=synthetic,
        progress=(lambda f, m: progress(f * 0.9, m)) if progress else None)
    if progress:
        progress(0.92, "Writing GeoJSON layers…")

    # --- cells geojson (compact properties) ----------------------------
    ex = gdf[["geometry"]].copy()
    ex["id"] = gdf["h3"].values
    ex["lv"] = gdf["land_value_index"].round(2).values
    for i, comp in enumerate(COMPONENTS):
        ex[f"c{i}"] = gdf[f"norm_{comp}"].round(4).values
    ex["ea"] = gdf["establishment_access"].round(3).values
    ex["pwd"] = gdf["poi_weighted_density"].round(3).values
    ex["rdk"] = gdf["road_density_km"].round(3).values
    ex["dcbd"] = gdf["dist_cbd_km"].round(3).values
    ex["pc"] = gdf["poi_count"].astype(int).values
    ex["bs"] = gdf["builtup_score"].round(4).values
    ex["mt"] = gdf["in_metro"].astype(int).values
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
    print(f"  {place:32} land={len(gdf):>5}  metro={int(gdf['in_metro'].sum()):>4}  "
          f"water_excl={gdf.attrs.get('n_water_excluded', 0):>4}")
    return {
        "slug": slug, "name": place.split(",")[0], "place": place,
        "osm_id": cfg["city"].get("osm_id"),
        "center": [r(cbd_lng, 5), r(cbd_lat, 5)],
        "bbox": [r(b[0], 5), r(b[1], 5), r(b[2], 5), r(b[3], 5)],
        "cells": f"{slug}_cells.geojson", "metro": f"{slug}_metro.geojson",
        "pois": f"{slug}_pois.geojson", "water": f"{slug}_water.geojson",
        "n_land": int(len(gdf)), "n_water": int(gdf.attrs.get("n_water_excluded", 0)),
        "n_pois": int(len(city.pois)), "metro_km2": metro_area, "source": city.source,
        "source_error": city.source_error,
    }


def manifest_path() -> Path:
    return WEBAPP_DATA / "manifest.json"


def read_manifest() -> dict | None:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else None


def build_manifest(cfg, cities: list[dict]) -> dict:
    return {
        "cities": cities,
        "components": COMPONENTS,
        "component_labels": ["Downtown", "Major road", "Establishments", "POI density", "Road density"],
        "weights_default": dict(cfg["landvalue"]["weights"]),
        "metrics": METRICS,
        "poi_categories": list(cfg["poi_categories"].keys()),
    }


def write_manifest(manifest: dict) -> None:
    manifest_path().write_text(json.dumps(manifest, indent=0))


def upsert_city(cfg, city: dict) -> dict:
    """Add (or replace) one city in the on-disk manifest and return it."""
    man = read_manifest() or build_manifest(cfg, [])
    man["cities"] = [c for c in man["cities"] if c["slug"] != city["slug"]] + [city]
    man["cities"].sort(key=lambda c: c["name"])
    write_manifest(man)
    return man


def export_and_register(
    place: str, synthetic: bool = False, rebuild: bool = False,
    progress=None, osm_id: str | None = None,
) -> dict:
    """Build one city, write its files, upsert it into the manifest. Used by the server."""
    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    city = export_city(
        cfg, place, synthetic=synthetic, rebuild=rebuild,
        progress=progress, osm_id=osm_id,
    )
    if not synthetic and city.get("source") != "osm":
        # OSM geocode/fetch failed and the pipeline fell back to synthetic —
        # remove the stray files, don't register, surface a helpful error.
        for key in ("cells", "metro", "pois", "water"):
            (WEBAPP_DATA / city[key]).unlink(missing_ok=True)
        if city.get("osm_id"):
            detail = f" Tried exact OSM ID {city['osm_id']}."
        else:
            detail = " Try a fuller name, e.g. “Davao City, Philippines”, or provide an exact OSM relation ID."
        if city.get("source_error"):
            detail += f" OSM error: {city['source_error']}"
        raise LookupError(
            f"Couldn't build “{place}” from OpenStreetMap.{detail}")
    upsert_city(cfg, city)
    if progress:
        progress(1.0, "Done")
    return city


def main() -> None:
    ap = argparse.ArgumentParser(description="Export static data for the web app.")
    ap.add_argument("--places", nargs="*", help="City names (default: config city).")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    places = args.places or [cfg["city"]["place"]]

    print(f"Exporting {len(places)} city(ies) -> {WEBAPP_DATA}")
    cities = [export_city(cfg, p, args.synthetic, args.rebuild) for p in places]
    write_manifest(build_manifest(cfg, cities))
    print(f"Wrote manifest.json ({len(cities)} cities). Serve with: bash webapp/serve.sh")


if __name__ == "__main__":
    main()
