"""H3 hexagonal grid utilities.

We use Uber's H3 grid as the spatial unit of analysis. Every metric (land
value, accessibility, urban score) is computed per hex cell. Hexagons are
preferred over a square lattice because every neighbour is equidistant,
which makes contiguity / spatial smoothing well-behaved.

This module wraps the h3 library so the rest of the codebase is insulated
from the h3 v3 -> v4 API rename.
"""
from __future__ import annotations

from typing import Iterable

import h3
import numpy as np
from shapely.geometry import Polygon, shape

_V4 = hasattr(h3, "latlng_to_cell")


# --- version-robust primitives ----------------------------------------
def latlng_to_cell(lat: float, lng: float, res: int) -> str:
    return h3.latlng_to_cell(lat, lng, res) if _V4 else h3.geo_to_h3(lat, lng, res)


def cell_to_latlng(cell: str) -> tuple[float, float]:
    return h3.cell_to_latlng(cell) if _V4 else h3.h3_to_geo(cell)


def cell_to_boundary(cell: str) -> list[tuple[float, float]]:
    """Vertices as (lat, lng) pairs."""
    return list(h3.cell_to_boundary(cell) if _V4 else h3.h3_to_geo_boundary(cell))


def grid_disk(cell: str, k: int = 1) -> list[str]:
    return list(h3.grid_disk(cell, k) if _V4 else h3.k_ring(cell, k))


def edge_length_km(res: int) -> float:
    if _V4:
        return h3.average_hexagon_edge_length(res, unit="km")
    return h3.edge_length(res, unit="km")


# --- grid construction ------------------------------------------------
def polygon_to_cells(polygon: Polygon, res: int) -> list[str]:
    """Fill a shapely polygon (lng/lat, EPSG:4326) with H3 cells."""
    if _V4:
        # h3 v4 wants a LatLngPoly built from (lat, lng) loops.
        outer = [(lat, lng) for lng, lat in polygon.exterior.coords]
        holes = [
            [(lat, lng) for lng, lat in ring.coords] for ring in polygon.interiors
        ]
        poly = h3.LatLngPoly(outer, *holes)
        return list(h3.h3shape_to_cells(poly, res))
    # v3 polyfill wants GeoJSON-style {lng,lat} coordinates.
    geojson = polygon.__geo_interface__
    return list(h3.polyfill(geojson, res, geo_json_conformant=True))


def cell_polygon(cell: str) -> Polygon:
    """Shapely polygon (lng, lat order) for one cell, ready for GeoDataFrames."""
    boundary = cell_to_boundary(cell)  # (lat, lng)
    return Polygon([(lng, lat) for lat, lng in boundary])


def build_grid(region: Polygon, res: int) -> list[str]:
    """All H3 cells whose interior intersects `region`.

    h3shape_to_cells only returns cells whose *centroid* is inside, so we
    also add a one-ring buffer to avoid clipping the study-area edge.
    """
    cells = set(polygon_to_cells(region, res))
    fringe: set[str] = set()
    for c in cells:
        fringe.update(grid_disk(c, 1))
    cells |= fringe
    return sorted(cells)


def cells_to_latlng(cells: Iterable[str]) -> np.ndarray:
    """(N, 2) array of cell-centroid (lat, lng)."""
    return np.array([cell_to_latlng(c) for c in cells], dtype=float)
