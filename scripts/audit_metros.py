#!/usr/bin/env python3
"""Read-only metro diagnostics and threshold sensitivity; never writes exports.

Checks boundary clipping, CBD location, signal agreement, and repeated directed
road geometry. Sensitivity changes POI/road bars together by one; it is not an
accuracy score or a replacement calibration.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shapely.geometry import Point
from metro import grid, landvalue, pipeline
from metro.config import load_config, merge_overrides


def audit(place, osm_id=None):
    cfg = load_config()
    cfg["city"].update(place=place, osm_id=osm_id)
    cfg["nightlights"]["enabled"] = False  # diagnostic does not need a WMS call
    gdf, city = pipeline.run(cfg)
    if city.source != "osm":
        raise RuntimeError(f"Real OSM layers required: {city.source_error}")
    metro = gdf.in_metro
    uc = gdf.is_urban_centre
    area = gdf.cell_area_km2
    base = float(area[metro].sum())
    all_cells = set(grid.build_grid(city.study_region, cfg["grid"]["h3_resolution"]))
    edge = {c for c in gdf.index[metro]
            if any(n not in all_cells for n in grid.grid_disk(c, 1))}
    lat, lng = gdf.attrs["cbd"]
    roads = city.roads.to_crs(city.roads.estimate_utm_crs())
    unique = ~roads.geometry.normalize().to_wkb().duplicated()
    unique_km = float(roads.loc[unique].length.sum() / 1000)
    result = {
        "place": place, "osm_id": cfg["city"].get("osm_id"),
        "metro_cells": int(metro.sum()), "metro_h3_km2": base,
        "metro_population": round(float(gdf.loc[metro, "population"].sum())),
        "cbd": [lng, lat],
        "cbd_in_boundary": bool(city.boundary.geometry.union_all().covers(Point(lng, lat))),
        "metro_edge_cells": len(edge),
        "osm_only_metro_pct": 100 * int((metro & gdf.osm_urban & ~uc).sum()) / max(1, int(metro.sum())),
        "population_only_metro_pct": 100 * int((metro & uc & ~gdf.osm_urban).sum()) / max(1, int(metro.sum())),
        "population_centre_cells_in_study": int(uc.sum()),
        "road_length_inflation": float(roads.length.sum() / 1000) / unique_km if unique_km else None,
    }
    for name, shift in [("looser", -1), ("stricter", 1)]:
        alt = merge_overrides(cfg, {"metro": {
            "min_poi_per_cell": cfg["metro"]["min_poi_per_cell"] + shift,
            "min_road_km_per_cell": cfg["metro"]["min_road_km_per_cell"] + shift,
        }})
        changed = landvalue.delineate_metro(alt, gdf).in_metro
        result[f"{name}_area_change_pct"] = (float(area[changed].sum()) / base - 1) * 100 if base else None
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--places", nargs="*", help="Saved city names (default: all).")
    ap.add_argument("--output-json", type=Path)
    args = ap.parse_args()
    cities = json.loads((ROOT / "webapp/data/manifest.json").read_text())["cities"]
    if args.places:
        requested = {p.casefold() for p in args.places}
        cities = [c for c in cities if c["place"].casefold() in requested or c["name"].casefold() in requested]
        if not cities:
            raise SystemExit("No matching saved cities")
    results, failures = [], []
    print(f"{'City':24} {'OSM-only%':>10} {'pop-only%':>10} {'edge':>6} {'CBD in':>7} {'roads ×':>8} {'looser%':>9} {'stricter%':>10}", flush=True)
    for c in cities:
        try:
            r = audit(c["place"], c.get("osm_id"))
            results.append(r)
            print(f"{c['name'][:24]:24} {r['osm_only_metro_pct']:10.1f} {r['population_only_metro_pct']:10.1f} "
                  f"{r['metro_edge_cells']:6} {str(r['cbd_in_boundary']):>7} {r['road_length_inflation']:8.2f} "
                  f"{r['looser_area_change_pct']:9.1f} {r['stricter_area_change_pct']:10.1f}", flush=True)
        except Exception as exc:
            failures.append(c["name"])
            print(f"ERROR {c['name']}: {exc}", flush=True)
    if args.output_json:
        args.output_json.write_text(json.dumps(results, indent=2))
    if failures:
        raise SystemExit(f"Incomplete audit: {', '.join(failures)}")


if __name__ == "__main__":
    main()
