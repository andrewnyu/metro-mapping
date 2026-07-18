#!/usr/bin/env python3
"""Train positive spatial-index weights from in-metro market-area labels."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro import grid, landvalue, pipeline  # noqa: E402
from metro.config import load_config  # noqa: E402
from metro.weight_training import (  # noqa: E402
    artifact_path, fit_weight_model, labels_path, save_artifact,
)


def build_training_frame(cfg, labels: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    missing_cache = outside_grid = outside_metro = 0
    for place, place_labels in labels.groupby("place", sort=True):
        city_cfg = copy.deepcopy(cfg)
        city_cfg["city"]["place"] = str(place)
        feature_path = pipeline.features_path(city_cfg)
        if not feature_path.exists():
            missing_cache += len(place_labels)
            continue
        cells = gpd.read_parquet(feature_path).set_index("h3", drop=False)
        cells = landvalue.run_model(city_cfg, cells)
        for _, label in place_labels.iterrows():
            cell = grid.latlng_to_cell(label.geometry.y, label.geometry.x,
                                       int(city_cfg["grid"]["h3_resolution"]))
            if cell not in cells.index:
                outside_grid += 1
                continue
            feature = cells.loc[cell]
            if not bool(feature["in_metro"]):
                outside_metro += 1
                continue
            record = {
                "label_id": label["label_id"], "place": place,
                "market_area": label["market_area"], "h3": cell,
                "price_per_sqm_php": float(label["price_per_sqm_php"]),
                "listing_observations": int(label["listing_observations"]),
                "price_source_url": label["price_source_url"],
                "location_source_url": label["location_source_url"],
            }
            for name in landvalue.COMPONENT_NAMES:
                record[f"norm_{name}"] = float(feature[f"norm_{name}"])
            rows.append(record)
    return pd.DataFrame(rows), {
        "input_labels": int(len(labels)), "matched_in_metro": int(len(rows)),
        "missing_feature_cache": int(missing_cache),
        "outside_grid": int(outside_grid), "outside_metro": int(outside_metro),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", type=Path, help="Override spatial-label GeoJSON.")
    ap.add_argument("--output", type=Path, help="Override learned-weight artifact.")
    args = ap.parse_args()

    cfg = load_config()
    source = args.labels or labels_path(cfg)
    if not source.exists():
        raise SystemExit(f"No spatial label file: {source}")
    labels = gpd.read_file(source)
    required = {
        "label_id", "place", "market_area", "price_per_sqm_php",
        "listing_observations", "property_type", "price_source_url",
        "location_source_url",
    }
    missing = required - set(labels.columns)
    if missing:
        raise SystemExit(f"Spatial label file missing columns: {sorted(missing)}")
    kind = labels["property_type"].fillna("").astype(str).str.lower()
    commercial = (
        kind.str.contains("commercial")
        & kind.str.contains(r"land|lot", regex=True)
        & ~kind.str.contains(r"house|condo|building|improvement", regex=True)
    )
    rejected = int((~commercial).sum())
    labels = labels.loc[commercial].copy()
    if rejected:
        print(f"Rejected {rejected} non-commercial or improved spatial labels")
    training, stats = build_training_frame(cfg, labels)
    print("Spatial join:", ", ".join(f"{k}={v}" for k, v in stats.items()))
    metadata = fit_weight_model(cfg, training)
    metadata["spatial_join"] = stats
    output = args.output or artifact_path(cfg)
    save_artifact(metadata, output)

    print("Learned weights:")
    for name, value in metadata["weights"].items():
        print(f"  {name:24} {value:.1%}")
    print(f"Validation: {metadata['validation']}; "
          f"MAE P{metadata['mae_php_sqm']:,.0f}/sqm; "
          f"median APE {metadata['median_ape']:.1%}")
    print(f"Artifact -> {output}")


if __name__ == "__main__":
    main()
