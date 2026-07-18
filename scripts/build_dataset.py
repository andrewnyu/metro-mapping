#!/usr/bin/env python3
"""Build the cell feature table for a city and save it to data/.

Usage:
    python scripts/build_dataset.py                 # use config.yaml
    python scripts/build_dataset.py --place "Davao City, Philippines"
    python scripts/build_dataset.py --rebuild        # ignore cache
    python scripts/build_dataset.py --synthetic      # offline demo data
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metro.config import load_config  # noqa: E402
from metro import pipeline, landvalue  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Build metro-mapping feature table.")
    ap.add_argument("--place", help="Override city.place (OSM geocodable name).")
    ap.add_argument("--resolution", type=int, help="Override H3 resolution.")
    ap.add_argument("--rebuild", action="store_true", help="Ignore cached features.")
    ap.add_argument("--synthetic", action="store_true", help="Force synthetic data.")
    args = ap.parse_args()

    cfg = load_config()
    if args.place:
        cfg["city"]["place"] = args.place
    if args.resolution:
        cfg["grid"]["h3_resolution"] = args.resolution

    print(f"City        : {cfg['city']['place']}")
    print(f"H3 res      : {cfg['grid']['h3_resolution']}")
    print(f"Study buffer: {cfg['city']['study_buffer_km']} km")
    print("Building (this fetches OSM the first time)…")

    gdf, city = pipeline.run(cfg, rebuild=args.rebuild, force_synthetic=args.synthetic)
    outputs = pipeline.build_context_outputs(
        cfg, gdf, city, rebuild=args.rebuild, synthetic=args.synthetic)

    out = pipeline.features_path(cfg, synthetic=args.synthetic)

    def rel(p):
        return p.relative_to(Path.cwd()) if p.is_relative_to(Path.cwd()) else p

    print(f"\nSource        : {city.source}")
    print(f"Land cells    : {len(gdf):,}")
    print(f"Water excluded: {gdf.attrs.get('n_water_excluded', 0):,} "
          f"(of {gdf.attrs.get('n_grid_cells', len(gdf)):,} grid cells)")
    print(f"POIs          : {len(city.pois):,}")
    print(f"Road edges    : {len(city.roads):,}")
    print(f"Water polys   : {len(city.water):,}")
    print(f"CBD (lat,lng) : {gdf.attrs.get('cbd')}")
    print(f"In-metro cells: {int(gdf['in_metro'].sum()):,} / {len(gdf):,}")
    print(f"\nSaved features -> {rel(out)}")
    print(f"Saved polygon  -> {rel(outputs['geojson'])}")
    print(f"Saved map      -> {rel(outputs['map'])}  (open in a browser)")

    econ = gdf.attrs.get("economics", {})
    print(f"Economic data : {econ.get('status', 'unmatched')}"
          f" ({econ.get('deposit_scope', 'no deposit scope')})")
    price_meta = gdf.attrs.get("price_model", {})
    if price_meta.get("status") == "trained":
        top = gdf.sort_values("land_price_php_sqm", ascending=False).head(5)
        print(f"Price model   : {price_meta.get('n_labels', 0):,} labels, "
              f"grouped MAE P{price_meta.get('mae_php_sqm', 0):,.0f}/sqm")
        print("\nTop estimated-price cells:")
        print(top[["h3", "dist_cbd_km", "poi_count", "land_price_php_sqm"]].to_string(index=False))
    else:
        top = gdf.sort_values("land_value_index", ascending=False).head(5)
        print("Price model   : not trained (relative index remains available)")
        print("\nTop relative land-value cells:")
        print(top[["h3", "dist_cbd_km", "poi_count", "land_value_index"]].to_string(index=False))


if __name__ == "__main__":
    main()
