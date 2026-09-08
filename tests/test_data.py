from types import SimpleNamespace
from unittest.mock import Mock

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from metro import data
from metro.config import load_config


def test_all_poi_requests_failing_does_not_become_empty_success():
    ox = SimpleNamespace(features_from_polygon=Mock(side_effect=ConnectionError("offline")))
    cfg = load_config()
    with pytest.warns(UserWarning), pytest.raises(ConnectionError, match="offline"):
        data._fetch_pois(ox, box(125, 8, 126, 9), cfg)
    assert ox.features_from_polygon.call_count == 1 + len(cfg["poi_categories"])


def test_combined_request_stays_default_and_valid_empty_is_supported():
    empty = gpd.GeoDataFrame(geometry=[], crs=4326)
    ox = SimpleNamespace(features_from_polygon=Mock(return_value=empty))
    assert data._fetch_pois(ox, box(125, 8, 126, 9), load_config()).empty
    ox.features_from_polygon.assert_called_once()


def test_failed_endpoint_retries_next_endpoint(monkeypatch):
    cfg = load_config()
    cfg["osm"]["overpass_urls"] = ["https://first", "https://second"]
    ox = SimpleNamespace(settings=SimpleNamespace())
    fetch = Mock(side_effect=[ConnectionError("failed"), "pois"])
    monkeypatch.setattr(data, "_fetch_pois", fetch)
    monkeypatch.setattr(data, "_fetch_roads", lambda *a: "roads")
    monkeypatch.setattr(data, "_fetch_water", lambda *a: "water")
    with pytest.warns(UserWarning):
        assert data._fetch_osm_layers_with_fallbacks(ox, None, cfg) == ("pois", "roads", "water")
    assert ox.settings.overpass_url == "https://second"


@pytest.mark.parametrize("cls,typ,address,geom", [
    ("landuse", "retail", "retail", box(123.8, 9.6, 123.9, 9.7)),
    ("boundary", "administrative", "city_district", box(124.6, 11, 124.61, 11.01)),
    ("boundary", "administrative", "city", box(-69, 9, -68, 10)),
])
def test_reject_wrong_geocodes(cls, typ, address, geom):
    g = gpd.GeoDataFrame({"class": [cls], "type": [typ], "addresstype": [address]}, geometry=[geom], crs=4326)
    with pytest.raises(LookupError):
        data._coerce_city_boundary(g, load_config())


def test_administrative_point_gets_explicit_buffer():
    g = gpd.GeoDataFrame({"class": ["boundary"], "type": ["administrative"]}, geometry=[Point(125.5, 9.8)], crs=4326)
    out = data._coerce_city_boundary(g, load_config())
    assert out.geometry.iloc[0].geom_type == "Polygon"
    assert out.boundary_source.iloc[0] == "point_buffer"
    assert data._coerce_city_boundary(out, load_config()).geometry.equals(out.geometry)


def test_rebuild_bypasses_city_layer_cache(monkeypatch, tmp_path):
    from metro import pipeline
    cfg = load_config()
    cfg["paths"]["data_dir"] = str(tmp_path)
    city = SimpleNamespace(source="osm")
    load = Mock(return_value=city)
    monkeypatch.setattr(pipeline, "load_city_data", load)
    monkeypatch.setattr(pipeline, "build_features", lambda *a, **kw: gpd.GeoDataFrame(geometry=[Point(125, 9)], crs=4326))
    pipeline.load_or_build_features(cfg, rebuild=True)
    assert load.call_args.kwargs["use_cache"] is False


def test_network_timeout_skips_category_retry_cascade():
    import requests
    ox = SimpleNamespace(features_from_polygon=Mock(side_effect=requests.exceptions.ReadTimeout("timed out")))
    with pytest.raises(requests.exceptions.ReadTimeout):
        data._fetch_pois(ox, box(125, 8, 126, 9), load_config())
    ox.features_from_polygon.assert_called_once()
