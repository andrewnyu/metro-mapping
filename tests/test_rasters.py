import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from metro import grid, nightlights, population, rasters
from metro.config import load_config
from test_delineation import cells_frame


def test_population_sums_counts_while_lights_average_intensity(tmp_path):
    cfg = load_config()
    cfg["paths"]["data_dir"] = str(tmp_path)
    cfg["population"].update(cache_subdir="", raster_file="tiny.tif")
    cfg["nightlights"].update(cache_subdir="", raster_file="tiny.tif")
    seed, g = cells_frame(0)
    lat, lng = grid.cell_to_latlng(seed)
    with rasterio.open(tmp_path / "tiny.tif", "w", driver="GTiff", height=2, width=2,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(lng-.0001, lat+.0001, .0001, .0001)) as dst:
        dst.write(np.array([[1, 2], [3, 4]], dtype="float32"), 1)
    assert population.population_per_cell(cfg, g).iloc[0] == 10
    assert nightlights.add_nightlights(cfg, g).ntl.iloc[0] == 2.5


def test_raster_cache_invalidates_for_changed_source_and_grid(tmp_path):
    cfg = load_config()
    cfg["paths"]["data_dir"] = str(tmp_path)
    _, g = cells_frame(1)
    source = tmp_path / "raster.tif"
    source.write_bytes(b"first")
    calls = []
    def build():
        calls.append(1)
        return g.assign(population=float(len(calls)))
    def cached(frame=g, settings=None, rebuild=False):
        return rasters._cached_layer(cfg, frame, "population", settings or {}, source,
                                     ["population"], build, rebuild)
    assert cached().population.eq(1).all()
    assert cached(g.iloc[::-1]).population.eq(1).all()
    assert len(calls) == 1
    source.write_bytes(b"second, changed")
    assert cached().population.eq(2).all()
    cached(g.iloc[:1])
    cached(settings={"new": True})
    cached(rebuild=True)
    assert len(calls) == 5


def test_population_failure_cannot_silently_change_boundary(monkeypatch):
    cfg = load_config()
    _, g = cells_frame(0)
    def fail(*a, **kw):
        raise ConnectionError("population unavailable")
    monkeypatch.setattr(population, "ensure_raster", fail)
    with pytest.raises(ConnectionError):
        rasters.attach_rasters(cfg, g)


def test_optional_lights_failure_is_reported_and_not_cached(monkeypatch, tmp_path):
    cfg = load_config()
    cfg["paths"]["data_dir"] = str(tmp_path)
    cfg["population"]["enabled"] = False
    _, g = cells_frame(0)
    def fail(*a, **kw):
        raise ConnectionError("lights unavailable")
    monkeypatch.setattr(nightlights, "add_nightlights", fail)
    with pytest.warns(UserWarning):
        out = rasters.attach_rasters(cfg, g)
    assert out.attrs["ntl_source"] == "unavailable"
    assert not list(tmp_path.rglob("*.parquet"))
    cfg["metro"]["min_calibrated_ntl_for_road_cell"] = 1
    with pytest.raises(ConnectionError):
        rasters.attach_rasters(cfg, g)
