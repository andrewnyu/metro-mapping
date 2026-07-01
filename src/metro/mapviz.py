"""Human-readable context maps.

Renders the results on a real OpenStreetMap basemap (so you get street names
and the surrounding city for context), with:

  * the metro-area **polygon** (the headline deliverable),
  * the land-value choropleth,
  * POIs coloured by category,
  * mapped water,
  * the auto-detected downtown.

Output is a self-contained folium HTML map plus a GeoJSON of the polygon, both
cached per city so they're cheap to reopen.
"""
from __future__ import annotations

import json
from pathlib import Path

import branca.colormap as cm
import folium
import geopandas as gpd
from folium.plugins import MarkerCluster
from shapely.geometry import mapping

from .config import Config
from .data import CityData

WGS84 = "EPSG:4326"

POI_COLORS = {
    "mall": "#e6194B", "office": "#4363d8", "transport": "#3cb44b",
    "school": "#f58231", "hospital": "#911eb4", "government": "#42d4f4",
    "bank": "#bfef45", "leisure": "#469990",
}


# ----------------------------------------------------------------------
def metro_polygon(gdf: gpd.GeoDataFrame, smooth_m: float = 150.0):
    """Dissolve the in-metro hexes into one (multi)polygon, lightly smoothed."""
    metro = gdf[gdf["in_metro"]]
    if metro.empty:
        return None
    utm = metro.estimate_utm_crs()
    g = metro.to_crs(utm).geometry.union_all()
    if smooth_m:  # close pinholes / ragged hex edges
        g = g.buffer(smooth_m).buffer(-smooth_m)
    return gpd.GeoSeries([g], crs=utm).to_crs(WGS84).iloc[0]


def polygon_geojson(gdf: gpd.GeoDataFrame) -> dict:
    """Metro polygon as a GeoJSON FeatureCollection dict (no disk I/O)."""
    poly = metro_polygon(gdf)
    area_km2 = 0.0
    if poly is not None:
        gs = gpd.GeoSeries([poly], crs=WGS84)
        area_km2 = gs.to_crs(gs.estimate_utm_crs()).area.iloc[0] / 1e6
    return {
        "type": "FeatureCollection",
        "features": [] if poly is None else [{
            "type": "Feature",
            "properties": {"name": "metro_area", "cells": int(gdf["in_metro"].sum()),
                           "area_km2": round(area_km2, 1)},
            "geometry": mapping(poly),
        }],
    }


def save_polygon_geojson(gdf: gpd.GeoDataFrame, path: Path) -> Path:
    path.write_text(json.dumps(polygon_geojson(gdf)))
    return path


# ----------------------------------------------------------------------
def build_context_map(cfg: Config, gdf: gpd.GeoDataFrame, city: CityData,
                      max_pois: int = 4000) -> folium.Map:
    cbd_lat, cbd_lng = gdf.attrs.get("cbd", (gdf["lat"].mean(), gdf["lng"].mean()))
    m = folium.Map(location=[cbd_lat, cbd_lng], zoom_start=12, tiles="OpenStreetMap",
                   control_scale=True)

    # --- water (drawn first, underneath) -------------------------------
    if city.water is not None and not city.water.empty:
        folium.GeoJson(
            city.water.to_json(), name="Water",
            style_function=lambda f: {"color": "#5b9bd5", "weight": 0,
                                      "fillColor": "#aaccee", "fillOpacity": 0.45},
        ).add_to(m)

    # --- land-value choropleth -----------------------------------------
    colormap = cm.linear.YlOrRd_09.scale(0, 100)
    colormap.caption = "Relative land-value index (0–100)"
    cells = gdf[["geometry", "h3", "land_value_index", "dist_cbd_km", "poi_count", "in_metro"]]
    folium.GeoJson(
        cells.to_json(), name="Land value",
        style_function=lambda f: {
            "fillColor": colormap(f["properties"]["land_value_index"]),
            "color": "#00000022", "weight": 0.3, "fillOpacity": 0.55,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["land_value_index", "dist_cbd_km", "poi_count"],
            aliases=["Land value", "Dist to CBD (km)", "POIs"]),
    ).add_to(m)
    colormap.add_to(m)

    # --- metro polygon (the deliverable) -------------------------------
    poly = metro_polygon(gdf)
    if poly is not None:
        folium.GeoJson(
            mapping(poly), name="Metro boundary",
            style_function=lambda f: {"color": "#c00", "weight": 3.5, "fill": True,
                                      "fillColor": "#c00", "fillOpacity": 0.04},
        ).add_to(m)

    # --- POIs by category ----------------------------------------------
    if city.pois is not None and not city.pois.empty:
        pois = city.pois if len(city.pois) <= max_pois else city.pois.sample(max_pois, random_state=0)
        cluster = MarkerCluster(name="POIs", show=False).add_to(m)
        for _, r in pois.iterrows():
            folium.CircleMarker(
                location=[r["lat"], r["lng"]], radius=3,
                color=POI_COLORS.get(r["category"], "#444"), fill=True,
                fill_opacity=0.8, weight=0, popup=str(r["category"]),
            ).add_to(cluster)

    # --- downtown ------------------------------------------------------
    folium.Marker(
        [cbd_lat, cbd_lng], tooltip="Downtown (auto-detected)",
        icon=folium.Icon(color="red", icon="star"),
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    return m


def save_map(m: folium.Map, path: Path) -> Path:
    m.save(str(path))
    return path
