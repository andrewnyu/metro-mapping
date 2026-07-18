#!/usr/bin/env python3
"""Build the city/metro economic feature table used by Metro-Mapping.

Bank inputs are the clean outputs of the sibling ph-bank-deposits project.
Population is a small, source-linked PSA seed included in this repository.
An optional BLGF file can add local tax receipts using the canonical columns
documented by ``--help``.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from metro.economics import normalize_area_name  # noqa: E402


CORE_TO_METRO = {
    "angeles": "Metro Angeles (Clark)",
    "bacolod": "Metro Bacolod",
    "baguio": "Metro Baguio (BLISTT)",
    "batangas": "Metro Batangas",
    "cagayan de oro": "Metro Cagayan de Oro",
    "cebu": "Metro Cebu",
    "dagupan": "Metro Dagupan",
    "davao": "Metro Davao",
    "iloilo": "Metro Iloilo-Guimaras",
    "manila": "Metro Manila (NCR)",
    "naga": "Metro Naga",
    "tacloban": "Metro Tacloban",
}


def _latest(df: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    out = df.copy()
    out["period"] = pd.to_datetime(out["period"], errors="raise")
    out = out.sort_values("period").drop_duplicates(groups + ["period"])
    return out.groupby(groups, as_index=False).tail(1)


def _city_rows(path: Path) -> pd.DataFrame:
    df = _latest(pd.read_csv(path), ["key"])
    return pd.DataFrame({
        "core_city": df["area"],
        "province": df.get("province"),
        "deposit_scope": "city",
        "deposit_area": df["area"],
        "period": df["period"].dt.date.astype(str),
        "bank_deposits_php": df["deposits_b"] * 1_000_000_000,
        "bank_accounts": pd.NA,
        "bank_offices": pd.NA,
        "bank_source": "PDIC/BSP via ph-bank-deposits",
    })


def _metro_rows(path: Path) -> pd.DataFrame:
    df = _latest(pd.read_csv(path), ["metro"]).set_index("metro")
    rows = []
    for core_key, metro in CORE_TO_METRO.items():
        if metro not in df.index:
            continue
        row = df.loc[metro]
        rows.append({
            "core_city": core_key.title(),
            "province": pd.NA,
            "deposit_scope": "metro",
            "deposit_area": metro,
            "period": row["period"].date().isoformat(),
            "bank_deposits_php": float(row["deposits_b"]) * 1_000_000_000,
            "bank_accounts": row.get("accounts"),
            "bank_offices": row.get("offices"),
            "bank_source": "PDIC/BSP via ph-bank-deposits",
        })
    return pd.DataFrame(rows)


def _merge_population(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    pop = pd.read_csv(path)
    pop["area_key"] = pop["core_city"].map(normalize_area_name)
    return df.merge(
        pop[["area_key", "population", "population_year", "source_url"]]
        .rename(columns={"population": "city_population", "source_url": "population_source"}),
        on="area_key", how="left",
    )


def _merge_fiscal(df: pd.DataFrame, path: Path | None) -> pd.DataFrame:
    cols = ["local_tax_revenue_php", "real_property_tax_php", "business_tax_php",
            "fiscal_year", "fiscal_source"]
    if path is None:
        for col in cols:
            df[col] = pd.NA
        return df
    fiscal = pd.read_csv(path)
    required = {"core_city", "local_tax_revenue_php"}
    missing = required - set(fiscal.columns)
    if missing:
        raise ValueError(f"Fiscal CSV missing columns: {sorted(missing)}")
    fiscal["area_key"] = fiscal["core_city"].map(normalize_area_name)
    for col in cols:
        if col not in fiscal:
            fiscal[col] = pd.NA
    fiscal = fiscal.sort_values("fiscal_year").drop_duplicates("area_key", keep="last")
    return df.merge(fiscal[["area_key", *cols]], on="area_key", how="left")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank-project", type=Path, required=True,
                    help="Path to ph-bank-deposits (must contain data/clean/*.csv).")
    ap.add_argument("--population", type=Path,
                    default=ROOT / "reference_data" / "psa_city_population_2024.csv")
    ap.add_argument("--fiscal-csv", type=Path,
                    help="Optional canonical BLGF CSV: core_city, local_tax_revenue_php, "
                         "real_property_tax_php, business_tax_php, fiscal_year, fiscal_source.")
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "economic_features.csv")
    args = ap.parse_args()

    clean = args.bank_project / "data" / "clean"
    city = _city_rows(clean / "city_summary.csv")
    metro = _metro_rows(clean / "metro_summary.csv")

    # Keep city rows for broad coverage, but replace a core city's deposits
    # with the explicitly supplied metro aggregate.
    city["area_key"] = city["core_city"].map(normalize_area_name)
    metro["area_key"] = metro["core_city"].map(normalize_area_name)
    city = city.loc[~city["area_key"].isin(set(metro["area_key"]))]
    # pandas 2.x warns about a future dtype choice when one input has an
    # intentionally all-missing column (city summaries lack account/office
    # counts). The values are left missing and normalized on CSV read.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning,
                                message=".*DataFrame concatenation with empty or all-NA entries.*")
        out = pd.concat([metro, city], ignore_index=True, sort=False)
    out = _merge_population(out, args.population)
    out = _merge_fiscal(out, args.fiscal_csv)
    out = out.sort_values(["area_key", "deposit_scope"]).drop(columns="area_key")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"Wrote {len(out):,} economic rows -> {args.output}")
    print(f"Metro deposit rows: {(out['deposit_scope'] == 'metro').sum():,}")
    print(f"Rows with population: {out['city_population'].notna().sum():,}")
    print(f"Rows with fiscal data: {out['local_tax_revenue_php'].notna().sum():,}")


if __name__ == "__main__":
    main()
