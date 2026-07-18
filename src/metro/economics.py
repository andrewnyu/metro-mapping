"""City/metro economic context used by the supervised land-price model.

The H3 accessibility features vary by cell.  Population, bank deposits, and
local tax receipts do not: they describe the wider city or metropolitan
market.  This module keeps that distinction explicit while attaching the
area-level values to every row so a pooled, multi-city estimator can consume
one rectangular training table.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config, REPO_ROOT


AREA_COLUMNS = (
    "city_population",
    "bank_deposits_php",
    "bank_accounts",
    "bank_offices",
    "local_tax_revenue_php",
    "real_property_tax_php",
    "business_tax_php",
)


def normalize_area_name(value: object) -> str:
    """Return a conservative key shared by OSM, PSA, PDIC/BSP, and BLGF."""
    if value is None or pd.isna(value):
        value = ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.split(",", 1)[0]
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"^city\s+of\s+", "", text)
    text = re.sub(r"\bcity\b", " ", text)
    text = re.sub(r"\bsto\.?\b", "santo", text)
    text = re.sub(r"\bsta\.?\b", "santa", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def reference_path(cfg: Config) -> Path:
    raw = Path(cfg.get("economics", {}).get(
        "reference_file", "data/economic_features.csv"))
    return raw if raw.is_absolute() else REPO_ROOT / raw


def load_reference(cfg: Config) -> pd.DataFrame:
    path = reference_path(cfg)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "core_city" not in df.columns:
        raise ValueError(f"Economic reference must contain core_city: {path}")
    df = df.copy()
    df["area_key"] = df["core_city"].map(normalize_area_name)
    if "deposit_scope" not in df:
        df["deposit_scope"] = "city"
    return df


def _match_row(
    cfg: Config, ref: pd.DataFrame, place: str | None = None
) -> pd.Series | None:
    if ref.empty:
        return None
    place = str(place or cfg["city"]["place"])
    aliases = cfg.get("economics", {}).get("place_aliases", {}) or {}
    key = normalize_area_name(place)
    key = normalize_area_name(aliases.get(key, key))
    matches = ref.loc[ref["area_key"] == key].copy()
    if matches.empty:
        return None

    # A metro aggregate is preferred for a known core city.  A province named
    # in the place string disambiguates repeated names such as Naga/Talisay.
    place_l = place.lower()
    if "province" in matches and matches["province"].notna().any():
        prov_match = matches["province"].fillna("").map(
            lambda p: str(p).lower() in place_l if str(p) else False)
        if prov_match.any():
            matches = matches.loc[prov_match]
    scope_rank = matches["deposit_scope"].map({"metro": 0, "city": 1}).fillna(2)
    matches = matches.assign(_scope_rank=scope_rank)
    if "period" in matches:
        matches["_period"] = pd.to_datetime(matches["period"], errors="coerce")
        matches = matches.sort_values(["_scope_rank", "_period"], ascending=[True, False])
    else:
        matches = matches.sort_values("_scope_rank")
    return matches.iloc[0]


def match_reference_row(cfg: Config, place: str) -> pd.Series | None:
    """Return the economic record for an explicit city/province market."""
    return _match_row(cfg, load_reference(cfg), place)


def attach_area_features(cfg: Config, gdf):
    """Attach one matched economic context row and record its provenance."""
    out = gdf.copy()
    ref = load_reference(cfg)
    row = _match_row(cfg, ref)
    for col in AREA_COLUMNS:
        value = np.nan if row is None else pd.to_numeric(row.get(col), errors="coerce")
        out[col] = float(value) if pd.notna(value) else np.nan

    population = out["city_population"]
    out["bank_deposits_per_capita_php"] = (
        out["bank_deposits_php"] / population.replace(0, np.nan))
    out["local_tax_revenue_per_capita_php"] = (
        out["local_tax_revenue_php"] / population.replace(0, np.nan))

    if row is None:
        out.attrs["economics"] = {
            "status": "unmatched",
            "reference_file": str(reference_path(cfg)),
        }
        return out

    def clean(value):
        return None if pd.isna(value) else value.item() if hasattr(value, "item") else value

    out.attrs["economics"] = {
        "status": "matched",
        "core_city": clean(row.get("core_city")),
        "deposit_scope": clean(row.get("deposit_scope")),
        "deposit_area": clean(row.get("deposit_area")),
        "deposit_period": clean(row.get("period")),
        "population_year": clean(row.get("population_year")),
        "fiscal_year": clean(row.get("fiscal_year")),
        "bank_source": clean(row.get("bank_source")),
        "population_source": clean(row.get("population_source")),
        "fiscal_source": clean(row.get("fiscal_source")),
    }
    return out
