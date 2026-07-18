"""Data loading: OpenStreetMap boundary, roads and POIs for a city.

Strategy
--------
1. Geocode the city, buffer it to a study region (a metro spills past the
   city line, so we analyse a buffered area).
2. Pull POIs and the drivable road network from OSM via OSMnx.
3. Cache everything to ``data/osm_cache`` as GeoParquet so re-runs are fast
   and reproducible offline.
4. If OSM is unreachable (no network) we fall back to a *synthetic* city so
   the rest of the pipeline + the web app still run end to end.

Everything downstream consumes the plain GeoDataFrames returned here, so a
real cadastral / PSA dataset can be swapped in later without touching the
feature or model code.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon

from .config import Config, normalise_osm_id

WGS84 = "EPSG:4326"


@dataclass
class CityData:
    """Bundle of everything the feature stage needs."""

    boundary: gpd.GeoDataFrame      # official city polygon(s)
    study_region: Polygon           # buffered analysis area (WGS84)
    pois: gpd.GeoDataFrame          # columns: category, weight, geometry(Point)
    roads: gpd.GeoDataFrame         # drivable edges, column 'highway'
    major_roads: gpd.GeoDataFrame   # subset: arterials only
    water: gpd.GeoDataFrame         # inland water polygons (lakes/rivers/bays)
    source: str                     # 'osm' or 'synthetic'
    source_error: str | None = None # failure that caused synthetic fallback

    @property
    def center(self) -> tuple[float, float]:
        c = self.study_region.centroid
        return (c.y, c.x)  # (lat, lng)


ProgressFn = "Callable[[float, str], None] | None"


def _p(progress, frac: float, msg: str) -> None:
    """Report progress if a callback was provided (no-op otherwise)."""
    if progress is not None:
        progress(frac, msg)


# ======================================================================
# Public entry point
# ======================================================================
def load_city_data(cfg: Config, use_cache: bool = True, force_synthetic: bool = False,
                   progress: ProgressFn = None) -> CityData:
    if force_synthetic:
        _p(progress, 0.4, "Generating synthetic city…")
        return _synthetic_city(cfg)
    try:
        return _load_from_osm(cfg, use_cache=use_cache, progress=progress)
    except Exception as exc:  # network down, geocode miss, etc.
        source_error = f"{type(exc).__name__}: {exc}"
        warnings.warn(
            f"OSM load failed ({source_error}). "
            "Falling back to a SYNTHETIC city so the app still runs.",
            stacklevel=2,
        )
        _p(progress, 0.4, "OSM unavailable — using synthetic city…")
        city = _synthetic_city(cfg)
        city.source_error = source_error
        return city


# ======================================================================
# OSM path
# ======================================================================
def _load_from_osm(cfg: Config, use_cache: bool, progress: ProgressFn = None) -> CityData:
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cfg.cache_dir / "osmnx")
    ox.settings.log_console = False
    ox.settings.requests_timeout = int(cfg.get("osm", {}).get("requests_timeout", 45))
    ox.settings.overpass_rate_limit = bool(cfg.get("osm", {}).get("overpass_rate_limit", False))
    ox.settings.overpass_url = _overpass_urls(cfg)[0]

    slug = cfg.city_cache_slug()
    buf = cfg["city"]["study_buffer_km"]
    cache = cfg.cache_dir
    f_bound = cache / f"{slug}_boundary.parquet"
    f_pois = cache / f"{slug}_pois_{buf:g}km.parquet"
    f_roads = cache / f"{slug}_roads_{buf:g}km.parquet"
    f_water = cache / f"{slug}_water_{buf:g}km.parquet"

    cached = all(f.exists() for f in (f_bound, f_pois, f_roads, f_water))
    if use_cache and cached:
        _p(progress, 0.6, "Loading cached city layers…")
        boundary = gpd.read_parquet(f_bound)
        pois = gpd.read_parquet(f_pois)
        roads = gpd.read_parquet(f_roads)
        water = gpd.read_parquet(f_water)
        study = _buffer_region(boundary, buf)
    else:
        _p(progress, 0.05, f"Geocoding {cfg['city']['place']}…")
        boundary = _geocode_boundary(ox, cfg).to_crs(WGS84)
        study = _buffer_region(boundary, buf)
        pois, roads, water = _fetch_osm_layers_with_fallbacks(ox, study, cfg, progress)
        _p(progress, 0.75, "Caching city layers to disk…")
        boundary.to_parquet(f_bound)
        pois.to_parquet(f_pois)
        roads.to_parquet(f_roads)
        water.to_parquet(f_water)

    major = roads[roads["is_major"]].copy()
    return CityData(boundary, study, pois, roads, major, water, source="osm")


def _overpass_urls(cfg: Config) -> list[str]:
    urls = cfg.get("osm", {}).get("overpass_urls") or ["https://overpass-api.de/api"]
    return [str(u).rstrip("/") for u in urls]


def _fetch_osm_layers_with_fallbacks(ox, study: Polygon, cfg: Config, progress: ProgressFn = None):
    errors = []
    urls = _overpass_urls(cfg)
    for i, url in enumerate(urls):
        ox.settings.overpass_url = url
        suffix = "" if i == 0 else f" via fallback {i + 1}/{len(urls)}"
        try:
            _p(progress, 0.15, f"Downloading points of interest{suffix}…")
            pois = _fetch_pois(ox, study, cfg)
            _p(progress, 0.40, f"Downloading road network (largest layer){suffix}…")
            roads = _fetch_roads(ox, study, cfg)
            _p(progress, 0.68, f"Downloading water bodies{suffix}…")
            water = _fetch_water(ox, study, cfg)
            return pois, roads, water
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            warnings.warn(
                f"Overpass endpoint failed ({url}): {type(exc).__name__}: {exc}",
                stacklevel=2,
            )
    raise ConnectionError("All Overpass endpoints failed. " + " | ".join(errors))


def _geocode_boundary(ox, cfg: Config) -> gpd.GeoDataFrame:
    """Resolve the city boundary by exact OSM ID, then text search fallback."""
    place = cfg["city"]["place"]
    osm_id = cfg["city"].get("osm_id")
    errors = []
    if osm_id:
        try:
            return _coerce_city_boundary(
                ox.geocode_to_gdf(normalise_osm_id(osm_id), by_osmid=True),
                cfg,
            )
        except Exception as osmid_exc:
            errors.append(f"OSM ID {osm_id!r}: {type(osmid_exc).__name__}: {osmid_exc}")
            warnings.warn(
                f"Exact OSM ID lookup failed for {osm_id!r} "
                f"({type(osmid_exc).__name__}: {osmid_exc}). "
                f"Trying place search for {place!r}.",
                stacklevel=2,
            )
    for which in [None, 1, 2, 3, 4, 5]:
        try:
            kwargs = {} if which is None else {"which_result": which}
            return _coerce_city_boundary(ox.geocode_to_gdf(place, **kwargs), cfg)
        except Exception as exc:
            label = "default" if which is None else f"result {which}"
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
    raise LookupError(f"No administrative city boundary found for {place!r}. " + " | ".join(errors))


def _coerce_city_boundary(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    """Accept admin boundaries or city points; reject schools/airports/malls."""
    gdf = gdf.to_crs(WGS84)
    if gdf.empty:
        raise LookupError("geocode returned no rows")
    geom_type = gdf.geometry.iloc[0].geom_type
    cls = str(gdf["class"].iloc[0]) if "class" in gdf.columns else ""
    typ = str(gdf["type"].iloc[0]) if "type" in gdf.columns else ""
    if cls == "boundary" and typ == "administrative":
        return gdf
    if geom_type == "Point" and cls in {"boundary", "place"} and typ in {"administrative", "city", "town"}:
        return _point_boundary(gdf, cfg)
    name = str(gdf["display_name"].iloc[0]) if "display_name" in gdf.columns else "result"
    raise LookupError(f"geocode matched {cls}/{typ} ({name}), not a city boundary")


def _point_boundary(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    """Create a small boundary around a geocoded city point when OSM lacks one."""
    radius_km = float(cfg.get("osm", {}).get("point_boundary_km", 8.0))
    metric = gdf.to_crs(gdf.estimate_utm_crs())
    out = metric.copy()
    out["geometry"] = metric.geometry.buffer(radius_km * 1000.0)
    out["boundary_source"] = "point_buffer"
    return out.to_crs(WGS84)

def _buffer_region(boundary: gpd.GeoDataFrame, buffer_km: float) -> Polygon:
    """Union + buffer the boundary by buffer_km, returned in WGS84."""
    metric = boundary.to_crs(boundary.estimate_utm_crs())
    buffered = metric.geometry.union_all().buffer(buffer_km * 1000.0)
    return gpd.GeoSeries([buffered], crs=metric.crs).to_crs(WGS84).iloc[0]


def _fetch_pois(ox, study: Polygon, cfg: Config) -> gpd.GeoDataFrame:
    frames = []
    failures = []
    for category, spec in cfg["poi_categories"].items():
        tags = dict(spec["tags"])
        try:
            gdf = ox.features_from_polygon(study, tags)
        except Exception as exc:
            failures.append(exc)
            continue
        if gdf.empty:
            continue
        gdf = gdf.to_crs(WGS84)
        pts = gdf.geometry.representative_point().reset_index(drop=True)  # polygons -> point
        frames.append(
            gpd.GeoDataFrame(
                {
                    "category": [category] * len(pts),
                    "weight": float(spec["weight"]),
                },
                geometry=pts.values,
                crs=WGS84,
            )
        )
    if not frames and len(failures) == len(cfg["poi_categories"]):
        raise failures[-1]
    if not frames:
        return gpd.GeoDataFrame(
            {"category": [], "weight": []}, geometry=[], crs=WGS84
        )
    out = pd.concat(frames, ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=WGS84)
    out["lng"] = out.geometry.x
    out["lat"] = out.geometry.y
    return out


def _fetch_roads(ox, study: Polygon, cfg: Config) -> gpd.GeoDataFrame:
    G = ox.graph_from_polygon(study, network_type="drive", simplify=True)
    edges = ox.graph_to_gdfs(G, nodes=False).reset_index(drop=True).to_crs(WGS84)
    edges["highway"] = edges["highway"].apply(_first_tag)
    major_classes = set(cfg["roads"]["major_classes"])
    edges["is_major"] = edges["highway"].isin(major_classes)
    return edges[["highway", "is_major", "geometry"]].copy()


def _fetch_water(ox, study: Polygon, cfg: Config) -> gpd.GeoDataFrame:
    """Inland water polygons (lakes, rivers-as-area, reservoirs, named bays).

    The open ocean is NOT a polygon in OSM, so this only catches mapped water;
    the sea is removed downstream by the reachability rule in features.py.
    """
    tags = {k: v for k, v in cfg["water"]["tags"].items()}
    try:
        gdf = ox.features_from_polygon(study, tags)
    except Exception:
        return gpd.GeoDataFrame({"kind": []}, geometry=[], crs=WGS84)
    if gdf.empty:
        return gpd.GeoDataFrame({"kind": []}, geometry=[], crs=WGS84)
    gdf = gdf.to_crs(WGS84)
    # Keep only (multi)polygon water; drop water tagged on points/lines.
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    return gpd.GeoDataFrame({"kind": ["water"] * len(gdf)}, geometry=gdf.geometry.values, crs=WGS84)


def _first_tag(v):
    return v[0] if isinstance(v, list) else v


# ======================================================================
# Synthetic fallback (offline / demo)
# ======================================================================
def _synthetic_city(cfg: Config, seed: int = 7) -> CityData:
    """A radial mono-centric city: dense core, arterials fanning out."""
    rng = np.random.default_rng(seed)
    # Default centre: Cebu City. Used only for plausible coordinates.
    clat, clng = 10.3157, 123.8854
    deg = 1.0 / 111.0  # ~km per degree

    radius_km = 8.0
    region = Point(clng, clat).buffer(radius_km * deg)
    boundary = gpd.GeoDataFrame(
        {"name": [cfg["city"]["place"]]},
        geometry=[Point(clng, clat).buffer(radius_km * 0.6 * deg)],
        crs=WGS84,
    )
    study = region

    # Radial arterials + a couple of ring roads.
    roads_geom, is_major = [], []
    for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        end = (clng + np.cos(ang) * radius_km * deg, clat + np.sin(ang) * radius_km * deg)
        roads_geom.append(LineString([(clng, clat), end]))
        is_major.append(True)
    for r in (2.5, 5.0):
        ring = Point(clng, clat).buffer(r * deg).exterior
        roads_geom.append(LineString(ring.coords))
        is_major.append(r == 5.0)
    # Minor local streets (random chords) to give road-density texture.
    for _ in range(120):
        a = rng.uniform(0, 2 * np.pi)
        rr = rng.uniform(0.3, radius_km) * deg
        p0 = (clng + np.cos(a) * rr, clat + np.sin(a) * rr)
        p1 = (p0[0] + rng.normal(0, 0.6) * deg, p0[1] + rng.normal(0, 0.6) * deg)
        roads_geom.append(LineString([p0, p1]))
        is_major.append(False)
    roads = gpd.GeoDataFrame(
        {"highway": ["primary" if m else "residential" for m in is_major],
         "is_major": is_major},
        geometry=roads_geom, crs=WGS84,
    )

    # A bay biting into the SE so water exclusion has something to remove.
    bay = Point(clng + 4.0 * deg, clat - 4.0 * deg).buffer(3.0 * deg)
    water = gpd.GeoDataFrame({"kind": ["bay"]}, geometry=[bay], crs=WGS84)
    region = region.difference(bay)
    study = region

    # POIs: gaussian cluster at the core + a few satellite sub-centres.
    centers = [(clat, clng, 1.5)] + [
        (clat + rng.normal(0, 3.5) * deg, clng + rng.normal(0, 3.5) * deg, 0.8)
        for _ in range(4)
    ]
    rows = []
    for category, spec in cfg["poi_categories"].items():
        n = rng.integers(20, 60)
        for _ in range(int(n)):
            clat0, clng0, spread = centers[rng.integers(0, len(centers))]
            plat = clat0 + rng.normal(0, spread) * deg
            plng = clng0 + rng.normal(0, spread) * deg
            if not region.contains(Point(plng, plat)):
                continue
            rows.append((category, float(spec["weight"]), plng, plat))
    pois = gpd.GeoDataFrame(
        {"category": [r[0] for r in rows], "weight": [r[1] for r in rows],
         "lng": [r[2] for r in rows], "lat": [r[3] for r in rows]},
        geometry=[Point(r[2], r[3]) for r in rows], crs=WGS84,
    )

    major = roads[roads["is_major"]].copy()
    return CityData(boundary, study, pois, roads, major, water, source="synthetic")
