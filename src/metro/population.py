"""Gridded population per H3 cell + EU/UN Degree of Urbanisation (DEGURBA).

Why this exists
---------------
The metro rule is POI-driven, so it inherits OpenStreetMap's uneven mapping
effort (a poorly-mapped city looks rural). Gridded population is an absolute,
mapping-effort-independent signal, and it unlocks the *international standard*
definition of an urban area instead of a bespoke rule.

Aggregation
-----------
WorldPop rasters are per-pixel population **counts**, so the correct H3
aggregation is the SUM of pixels whose centre falls inside the cell — not an
area-weighted zonal mean (that would double count or dilute). We window-read
the raster over the study bounds, keep only populated pixels, map each pixel
centre to its H3 cell, and group-sum.

DEGURBA (Degree of Urbanisation, level 1)
-----------------------------------------
The EU/UN/GHSL definition, applied on the H3 lattice:

* **Urban centre**  — contiguous cells with >= 1,500 inhabitants/km², whose
  cluster totals >= 50,000 people (holes filled).
* **Urban cluster** — contiguous cells with >= 300 inhabitants/km², whose
  cluster totals >= 5,000 people.
* **Rural**         — everything else.

This is an H3 adaptation of the thresholds, not an official DEGURBA product.
The reference grid is 1 km²; H3 res-8 area varies with location. Thresholds
use each cell's actual area. Hexagon adjacency and limited hole filling differ
from the reference raster method, so results require independent validation.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from . import grid
from .config import Config

DEFAULT_RASTER_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/"
    "2020/BSGM/PHL/phl_ppp_2020_UNadj_constrained.tif"
)
DEFAULT_RASTER_FILE = "phl_ppp_2020_UNadj_constrained.tif"


def _pcfg(cfg: Config) -> dict:
    return dict(cfg.get("population", {}) or {})


def raster_path(cfg: Config) -> Path:
    p = _pcfg(cfg)
    sub = p.get("cache_subdir", "pop_cache")
    d = cfg.data_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    return d / p.get("raster_file", DEFAULT_RASTER_FILE)


def ensure_raster(cfg: Config, progress=None) -> Path:
    """Download the population raster once and cache it."""
    path = raster_path(cfg)
    if path.exists():
        return path
    url = _pcfg(cfg).get("raster_url", DEFAULT_RASTER_URL)
    if progress:
        progress(0.05, "Downloading population raster (one time)…")
    import requests

    tmp = path.with_suffix(path.suffix + ".part")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(path)
    return path


# ----------------------------------------------------------------------
def population_per_cell(cfg: Config, gdf, progress=None) -> pd.Series:
    """Population per H3 cell, summed from raster pixel centres."""
    import rasterio
    from rasterio.windows import from_bounds

    path = ensure_raster(cfg, progress=progress)
    res = cfg["grid"]["h3_resolution"]
    minx, miny, maxx, maxy = gdf.total_bounds
    pad = 0.02  # degrees, so edge cells get their pixels

    if progress:
        progress(0.4, "Reading population raster…")
    with rasterio.open(path) as src:
        if src.crs != rasterio.crs.CRS.from_epsg(4326):
            raise ValueError("Raster must use EPSG:4326 pixel coordinates")
        if abs(src.transform.b) > 1e-12 or abs(src.transform.d) > 1e-12:
            raise ValueError("Rotated rasters are not supported")
        want = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad,
                           src.transform)
        full = rasterio.windows.Window(0, 0, src.width, src.height)
        try:
            window = want.intersection(full)
        except rasterio.errors.WindowError:
            # The study area does not overlap the raster at all — almost always
            # a geocoding miss (e.g. an ambiguous city name resolving to another
            # country), not a data problem. Surface it instead of crashing.
            raise LookupError(
                f"Study area {tuple(round(v, 3) for v in (minx, miny, maxx, maxy))} "
                f"falls outside the population raster {tuple(round(v, 3) for v in src.bounds)}. "
                "Check the city geocode — the name may have resolved to another country."
            ) from None
        arr = src.read(1, window=window)
        t = src.window_transform(window)
        nodata = src.nodata

    if arr.size == 0:
        return pd.Series(0.0, index=gdf.index)

    # North-up, unrotated rasters only (WorldPop is); guard the assumption.
    if abs(t.b) > 1e-12 or abs(t.d) > 1e-12:
        raise ValueError("Rotated rasters are not supported")

    valid = np.isfinite(arr) & (arr > 0)
    if nodata is not None:
        valid &= arr != nodata
    rows, cols = np.nonzero(valid)
    if rows.size == 0:
        return pd.Series(0.0, index=gdf.index)
    vals = arr[rows, cols].astype("float64")

    # Vectorised pixel-centre coordinates.
    lons = t.c + (cols + 0.5) * t.a
    lats = t.f + (rows + 0.5) * t.e

    if progress:
        progress(0.7, f"Assigning {rows.size:,} populated pixels to cells…")
    cells = [grid.latlng_to_cell(la, lo, res) for la, lo in zip(lats, lons)]
    summed = pd.Series(vals).groupby(pd.Index(cells)).sum()
    return summed.reindex(gdf.index).fillna(0.0)


def cell_areas_km2(gdf) -> pd.Series:
    import h3

    return pd.Series(
        [h3.cell_area(c, unit="km^2") for c in gdf.index], index=gdf.index
    )


def add_population(cfg: Config, gdf, progress=None):
    """Attach `population`, `cell_area_km2` and `pop_density_km2`."""
    gdf = gdf.copy()
    gdf["cell_area_km2"] = cell_areas_km2(gdf).values
    gdf["population"] = population_per_cell(cfg, gdf, progress=progress).values
    gdf["pop_density_km2"] = (gdf["population"] / gdf["cell_area_km2"]).values
    return gdf


# ----------------------------------------------------------------------
def _components(cells: set[str]) -> list[set[str]]:
    """Connected components over the H3 lattice (1-ring adjacency)."""
    seen: set[str] = set()
    out: list[set[str]] = []
    for start in cells:
        if start in seen:
            continue
        comp = {start}
        seen.add(start)
        q = deque([start])
        while q:
            c = q.popleft()
            for n in grid.grid_disk(c, 1):
                if n in cells and n not in seen:
                    seen.add(n)
                    comp.add(n)
                    q.append(n)
        out.append(comp)
    return out


def _fill_gaps(core: set[str], universe: set[str], min_neighbours: int = 5) -> set[str]:
    """Fill enclosed holes: non-core cells almost fully ringed by core."""
    filled = set(core)
    for _ in range(3):  # a few passes converge for realistic shapes
        added = set()
        for c in universe - filled:
            ring = [n for n in grid.grid_disk(c, 1) if n != c]
            if sum(1 for n in ring if n in filled) >= min_neighbours:
                added.add(c)
        if not added:
            break
        filled |= added
    return filled


def degurba(cfg: Config, gdf):
    """Classify cells as urban_centre / urban_cluster / rural (DEGURBA L1)."""
    gdf = gdf.copy()
    d = dict(_pcfg(cfg).get("degurba", {}) or {})
    uc_dens = float(d.get("urban_centre_density", 1500))
    uc_pop = float(d.get("urban_centre_min_pop", 50000))
    ucl_dens = float(d.get("urban_cluster_density", 300))
    ucl_pop = float(d.get("urban_cluster_min_pop", 5000))
    do_fill = bool(d.get("fill_gaps", True))

    dens = gdf["pop_density_km2"]
    pop = gdf["population"]
    universe = set(gdf.index)

    # --- urban centres -------------------------------------------------
    centre: set[str] = set()
    for comp in _components(set(gdf.index[dens >= uc_dens])):
        if pop.loc[list(comp)].sum() >= uc_pop:
            centre |= comp
    if do_fill and centre:
        centre = _fill_gaps(centre, universe)

    # --- urban clusters ------------------------------------------------
    cluster: set[str] = set()
    for comp in _components(set(gdf.index[dens >= ucl_dens])):
        if pop.loc[list(comp)].sum() >= ucl_pop:
            cluster |= comp

    label = pd.Series("rural", index=gdf.index, dtype=object)
    label.loc[list(cluster)] = "urban_cluster"
    label.loc[list(centre)] = "urban_centre"
    gdf["degurba"] = label.values
    gdf["is_urban_centre"] = gdf["degurba"].eq("urban_centre").values
    gdf.attrs["degurba_thresholds"] = {
        "urban_centre_density": uc_dens, "urban_centre_min_pop": uc_pop,
        "urban_cluster_density": ucl_dens, "urban_cluster_min_pop": ucl_pop,
    }
    return gdf


def summarise(gdf) -> dict:
    """Headline numbers for a modelled city GeoDataFrame."""
    area = gdf["cell_area_km2"]
    out = {
        "total_population": float(gdf["population"].sum()),
        "land_km2": float(area.sum()),
    }
    if "in_metro" in gdf.columns:
        m = gdf["in_metro"]
        out["metro_population"] = float(gdf.loc[m, "population"].sum())
        out["metro_km2"] = float(area[m].sum())
    if "degurba" in gdf.columns:
        for lab in ("urban_centre", "urban_cluster"):
            sel = gdf["degurba"].eq(lab)
            out[f"{lab}_population"] = float(gdf.loc[sel, "population"].sum())
            out[f"{lab}_km2"] = float(area[sel].sum())
    return out
