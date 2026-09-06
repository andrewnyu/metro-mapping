#!/usr/bin/env python3
"""PROTOTYPE: night-time lights per H3 cell, validated against population.

Night lights are an absolute, OSM-independent signal. This script checks
whether they agree with gridded population (the trusted reference) and whether
they light up cells the POI-driven metro rule misses. Read-only.

Usage:
    python scripts/prototype_nightlights.py                 # all manifest cities
    python scripts/prototype_nightlights.py --places "Butuan City"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro import nightlights, pipeline, population  # noqa: E402
from metro.config import load_config  # noqa: E402

MANIFEST = ROOT / "webapp" / "data" / "manifest.json"


def analyse(cfg, place: str, osm_id: str | None) -> dict:
    cfg["city"]["place"] = place
    cfg["city"]["osm_id"] = osm_id
    gdf, _ = pipeline.run(cfg)
    gdf = population.add_population(cfg, gdf)
    gdf = population.degurba(cfg, gdf)
    gdf = nightlights.add_nightlights(cfg, gdf)

    ntl = gdf["ntl"].astype(float)
    dens = gdf["pop_density_km2"].astype(float)
    ok = np.isfinite(ntl) & np.isfinite(dens)
    # Spearman via ranks: monotonic, robust to the 8-bit saturation.
    if ok.sum() > 10:
        r = np.corrcoef(ntl[ok].rank(), np.log1p(dens[ok]).rank())[0, 1]
    else:
        r = float("nan")

    metro = gdf["in_metro"].astype(bool)
    bright = ntl >= ntl.quantile(0.90)
    return {
        "source": gdf.attrs.get("ntl_source", "?"),
        "rho": r,
        "ntl_metro": float(ntl[metro].mean()) if metro.any() else float("nan"),
        "ntl_rural": float(ntl[~metro].mean()) if (~metro).any() else float("nan"),
        "bright_outside_metro": int((bright & ~metro).sum()),
        "bright_cells": int(bright.sum()),
        "uc_cells": int(gdf["is_urban_centre"].sum()),
        "metro_cells": int(metro.sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--places", nargs="*")
    args = ap.parse_args()

    cfg = load_config()
    cities = json.load(open(MANIFEST))["cities"] if MANIFEST.exists() else []
    if args.places:
        want = {p.lower() for p in args.places}
        cities = [c for c in cities
                  if c["name"].lower() in want or c["place"].lower() in want] or \
                 [{"name": p, "place": p, "osm_id": None} for p in args.places]

    print(f"{'City':22} {'ρ(NTL,logPop)':>14} {'NTL metro':>10} {'NTL rural':>10} "
          f"{'bright!∈metro':>13} {'metro':>7} {'UC':>6}")
    print("-" * 88)
    for c in cities:
        try:
            s = analyse(cfg, c["place"], c.get("osm_id"))
        except Exception as e:
            print(f"{c['name'][:22]:22} ERROR {type(e).__name__}: {str(e)[:50]}")
            continue
        print(f"{c['name'][:22]:22} {s['rho']:14.2f} {s['ntl_metro']:10.1f} "
              f"{s['ntl_rural']:10.1f} {s['bright_outside_metro']:13d} "
              f"{s['metro_cells']:7d} {s['uc_cells']:6d}")
    print(f"\nsource: {s['source'] if cities else 'n/a'}  "
          "(ρ = Spearman rank correlation of night lights vs log population density)")


if __name__ == "__main__":
    main()
