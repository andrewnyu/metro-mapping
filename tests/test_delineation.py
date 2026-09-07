import geopandas as gpd
import pandas as pd
import pytest

from metro import grid, landvalue, population, rasters
from metro.config import load_config


def cells_frame(radius=3):
    seed = grid.latlng_to_cell(10.3, 123.9, 8)
    cells = sorted(grid.grid_disk(seed, radius))
    coords = grid.cells_to_latlng(cells)
    g = gpd.GeoDataFrame({"h3": cells, "lat": coords[:, 0], "lng": coords[:, 1],
                         "poi_count": 0, "poi_weighted_density": 0.,
                         "road_density_km": 0., "establishment_access": 0.,
                         "dist_cbd_km": [grid.grid_distance(seed, c) for c in cells]},
                        geometry=[grid.cell_polygon(c) for c in cells], crs=4326, index=cells)
    g.attrs["cbd"] = grid.cell_to_latlng(seed)
    return seed, g


def test_population_recovers_unmapped_core_without_crossing_rural_land():
    cfg = load_config()
    cfg["metro"]["connector_gap"] = 0
    seed, g = cells_frame(5)
    core = set(grid.grid_disk(seed, 1))
    distant = next(c for c in g.index if grid.grid_distance(seed, c) == 5)
    g["is_urban_centre"] = g.index.isin(core | {distant})
    out = landvalue.delineate_metro(cfg, g)
    assert set(out.index[out.in_metro]) == core
    assert not out.osm_urban.any()
    cfg["population"]["enabled"] = False
    assert not landvalue.delineate_metro(cfg, g).in_metro.any()


def test_absolute_poi_bar_and_corroborated_roads():
    seed, g = cells_frame()
    cfg = load_config()
    g.loc[seed, "poi_count"] = 3
    neighbour = next(c for c in grid.grid_disk(seed, 1) if c != seed)
    g.loc[neighbour, "road_density_km"] = 5
    out = landvalue.delineate_metro(cfg, g)
    assert out.loc[seed, "in_metro"]
    assert not out.loc[neighbour, "is_urban"]
    g.loc[neighbour, "establishment_access"] = 3
    assert landvalue.delineate_metro(cfg, g).loc[neighbour, "in_metro"]


def test_bridge_water_but_never_rural_land():
    seed, g = cells_frame()
    end = next(c for c in g.index if grid.grid_distance(seed, c) == 2)
    path = grid.grid_path_cells(seed, end)
    urban = {seed, end}
    assert landvalue._connected_component(seed, urban, 2, set(path)) == {seed}
    assert landvalue._connected_component(seed, urban, 2, urban) == urban


def test_relative_gibs_cannot_decide_boundary_even_with_threshold():
    cfg = load_config()
    cfg["metro"]["min_calibrated_ntl_for_road_cell"] = 10
    seed, g = cells_frame()
    g["ntl"] = 255.
    g.loc[seed, "road_density_km"] = 5
    g.attrs["ntl_source"] = "gibs_black_marble_relative"
    assert not landvalue.delineate_metro(cfg, g).is_urban.any()
    g.attrs["ntl_source"] = "calibrated:test.tif"
    assert landvalue.delineate_metro(cfg, g).loc[seed, "in_metro"]


def test_population_requires_absolute_cluster_total():
    cfg = load_config()
    seed, g = cells_frame(1)
    g["population"] = 7000.
    g["pop_density_km2"] = 9000.
    assert not population.degurba(cfg, g).is_urban_centre.any()  # 49,000
    g.loc[seed, "population"] += 1000
    assert population.degurba(cfg, g).is_urban_centre.all()


def test_synthetic_and_disabled_layers_never_fetch(monkeypatch):
    cfg = load_config()
    _, g = cells_frame()
    def unexpected(*a, **kw):
        pytest.fail("Should not fetch raster")
    monkeypatch.setattr(population, "ensure_raster", unexpected)
    assert rasters.attach_rasters(cfg, g, synthetic=True).attrs["population_source"] == "synthetic_skipped"
    cfg["population"]["enabled"] = False
    cfg["nightlights"]["enabled"] = False
    assert rasters.attach_rasters(cfg, g).attrs["population_source"] == "disabled"


def test_equal_length_connector_ties_are_order_independent(monkeypatch):
    # Two supported paths of equal length; input iteration order must not
    # select different connector cells on separate processes.
    monkeypatch.setattr(grid, "grid_distance", lambda a, b: 2)
    monkeypatch.setattr(grid, "grid_path_cells", lambda a, b: [a, a + b, b])
    monkeypatch.setattr(landvalue, "_is_supported_connector_path", lambda *a: True)
    forward = landvalue._nearest_supported_path(None, ["a", "b"], ["c", "d"], set(), set(), 1, {})
    reverse = landvalue._nearest_supported_path(None, ["b", "a"], ["d", "c"], set(), set(), 1, {})
    assert forward == reverse == ["a", "ac", "c"]
