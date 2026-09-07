"""Attach independent raster evidence before delineation, with per-grid caches.

Population failures stop a real build: silently omitting an enabled boundary
input would change the result. Night lights are optional corroboration; failure
is reported and retried next run. Synthetic cities never request real rasters.
"""
from __future__ import annotations

import hashlib
import json
import warnings

import pandas as pd

from . import nightlights, population


def _cached_layer(cfg, gdf, kind, settings, raster, columns, build, rebuild):
    signature = {
        "version": 1, "cells": sorted(gdf.index), "settings": settings,
        "raster": (str(raster.resolve()), raster.stat().st_size, raster.stat().st_mtime_ns)
        if raster is not None and raster.exists() else None,
    }
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    directory = cfg.data_dir / "raster_cache"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}_{digest}.parquet"
    if path.exists() and not rebuild:
        cached = pd.read_parquet(path).reindex(gdf.index)
        out = gdf.copy()
        for col in columns:
            out[col] = cached[col]
        if kind == "nightlights":
            out.attrs["ntl_source"] = str(cached["ntl_source"].iloc[0])
        return out
    out = build()
    cached = pd.DataFrame(out[columns])
    if kind == "nightlights":
        cached["ntl_source"] = out.attrs["ntl_source"]
    # Atomic publication: concurrent builds must never read half a parquet.
    import tempfile
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".parquet", delete=False) as tmp:
        temporary = type(path)(tmp.name)
    try:
        cached.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return out


def attach_rasters(cfg, gdf, *, synthetic=False, rebuild=False, progress=None):
    gdf = gdf.copy()
    gdf.attrs["population_source"] = "synthetic_skipped" if synthetic else "disabled"
    gdf.attrs["ntl_source"] = "synthetic_skipped" if synthetic else "disabled"
    if synthetic:
        return gdf
    if cfg.get("population", {}).get("enabled", False):
        raster = population.ensure_raster(cfg, progress=progress)
        gdf = _cached_layer(
            cfg, gdf, "population", dict(cfg["population"]), raster,
            ["population", "cell_area_km2", "pop_density_km2"],
            lambda: population.add_population(cfg, gdf, progress=progress), rebuild,
        )
        # Classification is cheap and always recomputed for current thresholds.
        gdf = population.degurba(cfg, gdf)
        gdf.attrs["population_source"] = raster.name
    if cfg.get("nightlights", {}).get("enabled", False):
        try:
            gdf = _cached_layer(
                cfg, gdf, "nightlights", dict(cfg["nightlights"]), nightlights.raster_path(cfg),
                ["ntl", "ntl_norm"],
                lambda: nightlights.add_nightlights(cfg, gdf, progress=progress), rebuild,
            )
        except Exception as exc:
            # If lights are configured as a boundary input, fail closed too.
            if cfg["metro"].get("min_calibrated_ntl_for_road_cell") is not None:
                raise
            warnings.warn(f"Night lights unavailable: {exc}", stacklevel=2)
            gdf.attrs["ntl_source"] = "unavailable"
            gdf.attrs["ntl_error"] = str(exc)
    return gdf
