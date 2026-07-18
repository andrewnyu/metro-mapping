#!/usr/bin/env python3
"""Normalize authorized commercial vacant-land exports into the label file."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro.config import load_config  # noqa: E402
from metro.pricing import labels_path, normalize_listings  # noqa: E402


def read_input(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".json", ".jsonl"}:
        return pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    return pd.read_csv(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", type=Path, nargs="+",
                    help="CSV/JSON exports with source URLs and coordinates.")
    ap.add_argument("--replace", action="store_true", help="Replace instead of append/deduplicate.")
    ap.add_argument("--output", type=Path, help="Override config price_model.labels_file.")
    args = ap.parse_args()

    cfg = load_config()
    frames = [read_input(path) for path in args.inputs]
    incoming = normalize_listings(pd.concat(frames, ignore_index=True), cfg)
    stats = incoming.attrs.get("normalization", {})
    output = args.output or labels_path(cfg)
    if output.exists() and not args.replace:
        existing = normalize_listings(pd.read_csv(output), cfg)
        incoming = normalize_listings(pd.concat([existing, incoming], ignore_index=True), cfg)
    output.parent.mkdir(parents=True, exist_ok=True)
    incoming.to_csv(output, index=False)
    print(f"Accepted {stats.get('accepted_rows', len(incoming)):,} of "
          f"{stats.get('input_rows', len(incoming)):,} incoming rows")
    print(f"Canonical deduplicated labels: {len(incoming):,} -> {output}")


if __name__ == "__main__":
    main()
