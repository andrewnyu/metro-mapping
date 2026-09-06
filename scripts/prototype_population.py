#!/usr/bin/env python3
"""PROTOTYPE: gridded population + EU/UN Degree of Urbanisation per city.

Attaches WorldPop population to the existing H3 feature table, classifies every
cell with the DEGURBA standard, and compares that against the current
POI-driven metro footprint. Read-only: it does not modify exports.

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

from metro import pipeline, population  # noqa: E402
from metro.config import load_config  # noqa: E402

MANIFEST = ROOT / "webapp" / "data" / "manifest.json"


def manifest_cities() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.load(open(MANIFEST))["cities"]


def analyse(cfg, place: str, osm_id: str | None):
    cfg["city"]["place"] = place
    cfg["city"]["osm_id"] = osm_id
    gdf, city = pipeline.run(cfg)
    gdf = population.add_population(cfg, gdf)
    gdf = population.degurba(cfg, gdf)
    s = population.summarise(gdf)

    metro = gdf["in_metro"]
    uc = gdf["is_urban_centre"]
    both = int((metro & uc).sum())
    union = int((metro | uc).sum())
    s["jaccard"] = (both / union) if union else 0.0
    s["metro_cells"] = int(metro.sum())
    s["uc_cells"] = int(uc.sum())
    s["place"] = place
    return gdf, s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--places", nargs="*", help="Cities (default: manifest).")
    ap.add_argument("--detail", help="Print a per-class breakdown for one city.")
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
          f"{'UC pop':>12} {'total pop':>12} {'IoU':>6}")
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
              f"{s['total_population']:12,.0f} {s['jaccard']:6.2f}")

    if args.detail and rows:
        c, s, gdf = rows[0]
        print(f"\nDEGURBA breakdown — {c['name']}")
        g = gdf.groupby("degurba").agg(
            cells=("population", "size"),
            km2=("cell_area_km2", "sum"),
            pop=("population", "sum"))
        g["pop_share_%"] = (g["pop"] / g["pop"].sum() * 100).round(1)
        print(g.round(1).to_string())
        print(f"\nCells in POI-metro but rural by DEGURBA: "
              f"{int((gdf['in_metro'] & gdf['degurba'].eq('rural')).sum())}")
        print(f"Cells in urban centre but outside POI-metro: "
              f"{int((gdf['is_urban_centre'] & ~gdf['in_metro']).sum())}")


if __name__ == "__main__":
    main()
