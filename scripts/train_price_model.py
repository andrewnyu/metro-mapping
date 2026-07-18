#!/usr/bin/env python3
"""Train the city-market commercial vacant-land PHP/m² baseline model."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro.config import load_config  # noqa: E402
from metro.pricing import (  # noqa: E402
    artifact_path, build_donor_city_profiles, build_market_training_frame,
    build_top_market_calibrations,
    fit_market_price_model, market_observations_path, read_market_observations,
    save_artifact, top_market_anchors_path,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", type=Path,
                    help="Override config price_model.market_observations_file.")
    ap.add_argument("--output", type=Path, help="Override config price_model.artifact.")
    ap.add_argument("--allow-small-sample", action="store_true",
                    help="Testing only: lower the market observation safety guard.")
    args = ap.parse_args()

    cfg = load_config()
    label_file = args.labels or market_observations_path(cfg)
    if not label_file.exists():
        raise SystemExit(f"No market observation file: {label_file}.")

    observations = read_market_observations(label_file, cfg)
    training, join_stats = build_market_training_frame(cfg, observations)
    print("Economic join:", ", ".join(f"{k}={v}" for k, v in join_stats.items()))
    bundle = fit_market_price_model(
        cfg, training, allow_small_sample=args.allow_small_sample)
    bundle["metadata"]["economic_join"] = join_stats
    anchor_file = top_market_anchors_path(cfg)
    if anchor_file.exists():
        anchors = read_market_observations(anchor_file, cfg)
        calibrations = build_top_market_calibrations(cfg, anchors)
        bundle["metadata"]["top_market_calibrations"] = calibrations
        bundle["metadata"]["donor_city_profiles"] = build_donor_city_profiles(
            cfg, calibrations)
        bundle["metadata"]["n_top_anchor_observations"] = int(len(anchors))
        bundle["metadata"]["top_anchor_cities"] = sorted(calibrations)
        print(
            f"Top-market anchors: {len(anchors):,} observations; "
            f"{len(calibrations):,} qualifying cities"
        )
    else:
        bundle["metadata"]["top_market_calibrations"] = {}
        bundle["metadata"]["donor_city_profiles"] = {}
        bundle["metadata"]["n_top_anchor_observations"] = 0
        bundle["metadata"]["top_anchor_cities"] = []
    output = args.output or artifact_path(cfg)
    save_artifact(bundle, output)

    m = bundle["metadata"]
    print(f"Trained {m['n_market_observations']:,} market observations "
          f"({m['n_labels']:,} underlying listings) across {m['n_cities']} cities")
    print(f"Validation: {m['validation']}")
    print(f"MAE: P{m['mae_php_sqm']:,.0f}/sqm; median APE: {m['median_ape']:.1%}; "
          f"log R2: {m['r2_log']:.3f}")
    print(f"Artifact -> {output}")


if __name__ == "__main__":
    main()
