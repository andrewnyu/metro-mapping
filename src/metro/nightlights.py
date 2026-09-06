"""Night-time lights per H3 cell.

Why
---
The metro rule is POI-driven and therefore inherits OpenStreetMap's uneven
mapping effort. Night-time lights are an absolute, mapping-independent proxy
for electrified, economically active land — useful both as a corroborating
urban signal and (via multi-year composites) as a measure of metro *growth*.

Aggregation
-----------
Radiance is an **intensity**, not a count, so cells take the *mean* of the
pixels whose centre falls inside them — unlike population, which is summed.

Sources (in priority order)
---------------------------
1. **A calibrated raster you supply** — VIIRS VNL annual (EOG) or Black Marble
   VNP46A (NASA LAADS). Both need a free account, so they cannot be fetched
   unattended; drop the GeoTIFF in ``data/ntl_cache/`` (or set
   ``nightlights.raster_url``) and it is used automatically. This is the option
   to use for real analysis: values are calibrated radiance
   (nW·cm⁻²·sr⁻¹) and support absolute thresholds.
2. **NASA GIBS Black Marble WMS** — open, no credentials, fetched per study
   area. This is an 8-bit RGB *visualisation*: it saturates in bright cores and
   carries background glow, so treat it as a **relative** brightness index for
   prototyping, never as calibrated radiance.

``ntl_source`` on the returned frame records which was used.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

from . import grid
from .config import Config

GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
GIBS_LAYER = "VIIRS_Black_Marble"
GIBS_TIME = "2016-01-01"


def _ncfg(cfg: Config) -> dict:
    return dict(cfg.get("nightlights", {}) or {})


def raster_path(cfg: Config) -> Path | None:
    n = _ncfg(cfg)
    fname = n.get("raster_file")
    if not fname:
        return None
    d = cfg.data_dir / n.get("cache_subdir", "ntl_cache")
    d.mkdir(parents=True, exist_ok=True)
    return d / fname


# ----------------------------------------------------------------------
def _from_raster(cfg: Config, gdf, path: Path) -> pd.Series:
    """Zonal mean from a calibrated GeoTIFF."""
    import rasterio
    from rasterio.windows import from_bounds

    res = cfg["grid"]["h3_resolution"]
    minx, miny, maxx, maxy = gdf.total_bounds
    pad = 0.02
    with rasterio.open(path) as src:
        want = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, src.transform)
        full = rasterio.windows.Window(0, 0, src.width, src.height)
        try:
            window = want.intersection(full)
        except rasterio.errors.WindowError:
            raise LookupError(
                "Study area falls outside the night-lights raster — check the city geocode."
            ) from None
        arr = src.read(1, window=window).astype("float64")
        t = src.window_transform(window)
        nodata = src.nodata
    if arr.size == 0:
        return pd.Series(np.nan, index=gdf.index)
    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != nodata
    rows, cols = np.nonzero(valid)
    if rows.size == 0:
        return pd.Series(np.nan, index=gdf.index)
    lons = t.c + (cols + 0.5) * t.a
    lats = t.f + (rows + 0.5) * t.e
    return _mean_by_cell(arr[rows, cols], lats, lons, res, gdf.index)


def _from_gibs(cfg: Config, gdf) -> pd.Series:
    """Relative brightness from the open NASA GIBS Black Marble WMS."""
    import requests
    from PIL import Image

    n = _ncfg(cfg)
    res = cfg["grid"]["h3_resolution"]
    minx, miny, maxx, maxy = gdf.total_bounds
    pad = 0.01
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad

    deg_per_px = float(n.get("gibs_deg_per_px", 0.00225))  # ~250 m
    max_px = int(n.get("gibs_max_px", 2048))
    width = max(16, min(max_px, int(round((maxx - minx) / deg_per_px))))
    height = max(16, min(max_px, int(round((maxy - miny) / deg_per_px))))

    params = {
        "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0",
        "LAYERS": n.get("gibs_layer", GIBS_LAYER), "CRS": "EPSG:4326",
        # WMS 1.3.0 + EPSG:4326 uses lat,lon axis order
        "BBOX": f"{miny},{minx},{maxy},{maxx}",
        "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png",
        "TIME": n.get("gibs_time", GIBS_TIME),
    }
    r = requests.get(n.get("gibs_url", GIBS_WMS), params=params, timeout=120)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("L")
    arr = np.asarray(img).astype("float64")

    rows, cols = np.nonzero(np.isfinite(arr))
    lons = minx + (cols + 0.5) * (maxx - minx) / width
    lats = maxy - (rows + 0.5) * (maxy - miny) / height
    return _mean_by_cell(arr[rows, cols], lats, lons, res, gdf.index)


def _mean_by_cell(vals, lats, lons, res: int, index) -> pd.Series:
    cells = [grid.latlng_to_cell(la, lo, res) for la, lo in zip(lats, lons)]
    return pd.Series(vals).groupby(pd.Index(cells)).mean().reindex(index)


# ----------------------------------------------------------------------
def add_nightlights(cfg: Config, gdf, progress=None):
    """Attach `ntl` (per-cell mean) and `ntl_norm` (0-1 within the study area)."""
    gdf = gdf.copy()
    path = raster_path(cfg)
    if path is not None and path.exists():
        if progress:
            progress(0.3, "Reading calibrated night-lights raster…")
        vals = _from_raster(cfg, gdf, path)
        source = f"calibrated:{path.name}"
    else:
        if progress:
            progress(0.3, "Fetching NASA GIBS Black Marble…")
        vals = _from_gibs(cfg, gdf)
        source = "gibs_black_marble_relative"

    gdf["ntl"] = vals.values
    lo, hi = np.nanmin(gdf["ntl"]), np.nanmax(gdf["ntl"])
    span = (hi - lo) if np.isfinite(hi - lo) and hi > lo else 1.0
    gdf["ntl_norm"] = ((gdf["ntl"] - lo) / span).values
    gdf.attrs["ntl_source"] = source
    return gdf
