#!/usr/bin/env python3
"""Stage the static app and only manifest-referenced generated data for Vercel."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "webapp"
STATIC_FILES = (
    "index.html",
    "app.js",
    "style.css",
    "methodology.html",
    "methodology.css",
    "vercel.json",
)
CITY_FILES = ("cells", "metro", "pois", "water")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/metro-mapping-vercel"))
    args = parser.parse_args()

    manifest_path = WEBAPP / "data/manifest.json"
    if not manifest_path.exists():
        raise SystemExit("Build webapp/data/manifest.json before staging a deployment")
    manifest = json.loads(manifest_path.read_text())

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    data_output = output / "data"
    data_output.mkdir(parents=True)

    for name in STATIC_FILES:
        shutil.copy2(WEBAPP / name, output / name)
    shutil.copy2(manifest_path, data_output / "manifest.json")

    copied = set()
    for city in manifest["cities"]:
        for key in CITY_FILES:
            name = city.get(key)
            if name and name not in copied:
                shutil.copy2(WEBAPP / "data" / name, data_output / name)
                copied.add(name)

    size_mb = sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) / 1_000_000
    print(f"Staged {len(manifest['cities'])} cities / {len(copied)} data files in {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
