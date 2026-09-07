#!/usr/bin/env python3
"""PROTOTYPE: gridded population + EU/UN Degree of Urbanisation per city.

Attaches WorldPop population to the existing H3 feature table, classifies every
cell with the DEGURBA standard, and compares that against the current
combined metro footprint and the OSM-only baseline. Read-only: it does not modify exports.

Usage:
    python scripts/prototype_population.py                  # all manifest cities
    python scripts/prototype_population.py --places "Cebu City, Philippines"
    python scripts/prototype_population.py --detail "Zamboanga City"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro import pipeline, population, landvalue, mapviz  # noqa: E402
from metro.config import load_config, merge_overrides  # noqa: E402

MANIFEST = ROOT / "webapp" / "data" / "manifest.json"


def manifest_cities() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.load(open(MANIFEST))["cities"]


def analyse(cfg, place: str, osm_id: str | None):
    cfg["city"]["place"] = place
    cfg["city"]["osm_id"] = osm_id
    # This diagnostic needs no night-light request and retains the OSM-only
    # comparison even after the production boundary incorporates population.
    cfg = merge_overrides(cfg, {"nightlights": {"enabled": False},
                                "population": {"enabled": True}})
    gdf, city = pipeline.run(cfg)
    if city.source != "osm":
        raise RuntimeError(f"Real OSM data required: {city.source_error}")
    baseline = landvalue.delineate_metro(
        merge_overrides(cfg, {"population": {"enabled": False}}), gdf)

    s = population.summarise(gdf)

    metro = gdf["in_metro"]
    uc = gdf["is_urban_centre"]
    both = int((metro & uc).sum())
    union = int((metro | uc).sum())
    s["jaccard"] = (both / union) if union else 0.0
    s["metro_cells"] = int(metro.sum())
    s["uc_cells"] = int(uc.sum())
    poi_metro = baseline["in_metro"]
    s["poi_jaccard"] = int((poi_metro & uc).sum()) / max(1, int((poi_metro | uc).sum()))
    s["poi_metro_km2"] = float(gdf.loc[poi_metro, "cell_area_km2"].sum())
    metric = gdf.to_crs(city.boundary.estimate_utm_crs())
    s["admin_km2"] = float(city.boundary.to_crs(metric.crs).geometry.union_all().area / 1e6)
    s["metro_projected_km2"] = float(metric.loc[metro].geometry.union_all().area / 1e6)
    polygon = mapviz.polygon_geojson(gdf)
    s["metro_polygon_km2"] = polygon["features"][0]["properties"]["area_km2"] if polygon["features"] else 0.
    s["ratio_pct"] = 100 * s["metro_polygon_km2"] / s["admin_km2"]
    s["poi_count"] = len(city.pois)
    s["place"] = place
    return gdf, s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--places", nargs="*", help="Cities (default: manifest).")
    ap.add_argument("--detail", help="Print a per-class breakdown for one city.")
    ap.add_argument("--output-json", type=Path, help="Optional generated comparison report.")
    args = ap.parse_args()

    cfg = load_config()
    cities = manifest_cities()
    if args.places:
        wanted = {p.lower() for p in args.places}
        chosen = [c for c in cities if c["place"].lower() in wanted or c["name"].lower() in wanted]
        chosen = chosen or [{"place": p, "osm_id": None, "name": p} for p in args.places]
    elif args.detail:
        chosen = [c for c in cities if args.detail.lower() in (c["name"] + c["place"]).lower()]
    else:
        chosen = cities
    if not chosen:
        print("No cities found. Build/export one first.")
        return

    print(f"{'City':22} {'metro km²':>10} {'metro pop':>12} {'UC km²':>9} "
          f"{'UC pop':>12} {'total pop':>12} {'IoU':>6} {'OSM IoU':>8} {'admin km²':>10} {'ratio %':>8}")
    print("-" * 88)
    rows = []
    for c in chosen:
        try:
            gdf, s = analyse(cfg, c["place"], c.get("osm_id"))
        except Exception as e:  # keep going across a batch
            print(f"{c['name'][:22]:22} ERROR {type(e).__name__}: {e}")
            continue
        rows.append((c, s, gdf))
        print(f"{c['name'][:22]:22} {s.get('metro_km2',0):10.1f} {s.get('metro_population',0):12,.0f} "
              f"{s.get('urban_centre_km2',0):9.1f} {s.get('urban_centre_population',0):12,.0f} "
              f"{s['total_population']:12,.0f} {s['jaccard']:6.2f} {s['poi_jaccard']:8.2f} {s['admin_km2']:10.1f} {s['ratio_pct']:8.1f}")

    if args.output_json:
        args.output_json.write_text(json.dumps([s for _, s, _ in rows], indent=2))
    if len(rows) != len(chosen):
        raise SystemExit("Some city comparisons failed; the table is incomplete.")
    if args.detail and rows:
        c, s, gdf = rows[0]
        print(f"\nDEGURBA breakdown — {c['name']}")
        g = gdf.groupby("degurba").agg(
            cells=("population", "size"),
            km2=("cell_area_km2", "sum"),
            pop=("population", "sum"))
        g["pop_share_%"] = (g["pop"] / g["pop"].sum() * 100).round(1)
        print(g.round(1).to_string())
        print(f"\nCells in combined metro but rural by DEGURBA: "
              f"{int((gdf['in_metro'] & gdf['degurba'].eq('rural')).sum())}")
        print(f"Cells in urban centre but outside combined metro: "
              f"{int((gdf['is_urban_centre'] & ~gdf['in_metro']).sum())}")


if __name__ == "__main__":
    main()
