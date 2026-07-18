# Metro-Mapping

Metro-Mapping is a Python geospatial pipeline and static MapLibre web app for
estimating the functional metro area of a Philippine city and visualizing a
relative land-value index over an H3 hex grid.

The project is built around a simple idea: fetch city-scale OpenStreetMap data,
turn the study region into H3 cells, compute accessibility and built-up
features per cell, use those features to produce an interpretable 0-100
land-value index, then export compact GeoJSON for a browser map.

## What It Does

- Builds an H3 grid over a city boundary plus configurable buffer.
- Fetches OpenStreetMap boundary, points of interest, roads, and water polygons
  with OSMnx.
- Detects a downtown/CBD from smoothed weighted POI density unless manually
  pinned in `config.yaml`.
- Computes per-cell spatial features:
  - distance to CBD
  - distance to nearest major road
  - POI count and weighted POI density
  - road density
  - establishment gravity access
- Drops mapped water and likely open-sea cells with a reachability rule.
- Computes an interpretable land-value proxy from normalized accessibility
  components.
- Delineates the metro area as the contiguous built-up H3 component connected
  to downtown.
- Exports a static web app dataset with live browser-side weight sliders.
- Exports excluded water/open-sea cells so the web map can fill visual holes
  with a toggleable water layer.

If OSM is unavailable, the CLI pipeline can fall back to a synthetic radial city
so the rest of the pipeline and app still run end to end.

## Repository Layout

```text
config.yaml               Main city, grid, OSM, water, model, and path settings.
requirements.txt          Python dependencies.

scripts/
  build_dataset.py        Build feature parquet plus context outputs.
  export_webapp.py        Export compact GeoJSON/JSON for the web app.

src/metro/
  config.py               YAML loader and Config helper.
  data.py                 OSM fetch/cache layer plus synthetic fallback.
  grid.py                 H3 v3/v4-compatible helpers.
  features.py             Cell feature engineering and water masking.
  landvalue.py            Land-value index and metro delineation.
  mapviz.py               Folium map and metro polygon GeoJSON export.
  pipeline.py             End-to-end orchestration and cache paths.

webapp/
  index.html              Static app shell.
  app.js                  MapLibre UI, live weighting, city build flow.
  style.css               App styling.
  serve.py                Static server plus /api/build SSE endpoint.
  serve.sh                Convenience launcher, defaulting to port 8010.
  data/                   Generated cells, water, metro, POI, and manifest
                          exports; gitignored except .gitkeep.

data/                     Generated OSM cache, feature parquet, maps, and polygons;
                          gitignored except .gitkeep.
```

## Setup

Use a virtual environment if possible:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The pipeline needs network access for first-time real OSM builds. Re-runs use
the cache under `data/osm_cache/` when possible.

## Build A Dataset

Build the configured city from `config.yaml`:

```bash
python scripts/build_dataset.py
```

Build a specific city:

```bash
python scripts/build_dataset.py --place "Davao City, Philippines"
```

Force a fresh OSM/feature rebuild:

```bash
python scripts/build_dataset.py --rebuild
```

Run an offline synthetic demo:

```bash
python scripts/build_dataset.py --synthetic
```

`build_dataset.py` writes:

- feature parquet: `data/<city>_features_res<res>_<buffer>km.parquet`
- metro polygon: `data/<city>_metro_res<res>.geojson`
- folium context map: `data/<city>_context_map_res<res>.html`

## Export And Serve The Web App

Export the configured city:

```bash
python scripts/export_webapp.py
```

Export multiple cities:

```bash
python scripts/export_webapp.py --places "Cebu City, Philippines" "Iloilo City, Philippines"
```

Serve the app:

```bash
bash webapp/serve.sh
```

Then open:

```text
http://localhost:8010
```

The server serves static files and exposes:

```text
GET /api/build?place=<city name>
```

That endpoint streams Server-Sent Events while it builds a city, writes the
new `webapp/data/*` files, and upserts the city into `webapp/data/manifest.json`.
The browser uses that endpoint for the "Add a city" control.

The web app includes a "Bodies of water" layer toggle. This layer is generated
from cells excluded by the land mask plus mapped OSM water polygons, so bays,
rivers, and open-water gaps read as water instead of empty holes.

For cities where text search is unreliable, the app also accepts an optional
exact OpenStreetMap object ID. Use `R...` for relations, `W...` for ways, or
`N...` for nodes. Bare numeric IDs are treated as relations, and OpenStreetMap
URLs are accepted too.

## Web Data Contract

The browser app is fed entirely by `webapp/data/`, written by
`scripts/export_webapp.py`. Property names are shortened to keep the payload
small; if you add or rename a field, change it in `export_webapp.py`
(`COMPONENTS` / `METRICS` / the `ex[...]` assignments) **and** in `webapp/app.js`.

`manifest.json`

- `cities[]`: one entry per city, each with `slug`, `name`, `place`, `osm_id`,
  `center` (`[lng, lat]`), `bbox` (`[minx, miny, maxx, maxy]`), the four layer
  filenames (`cells`, `metro`, `pois`, `water`), the counts `n_land`, `n_water`,
  `n_metro`, `n_pois`, the areas `metro_km2` / `city_km2` / `study_km2` /
  `land_km2`, `source` (`osm` or `synthetic`), and `source_error`.
- `components`: the five model component keys, in slider order.
- `component_labels`: human labels for those five.
- `weights_default`: default component weights (also the reset target).
- `metrics`: the "Colour by" options, each `{key, label, prop, log, reverse}`.
- `poi_categories`: category names (match POI `cat`).

`<slug>_cells.geojson` — one feature per land cell:

| key | meaning |
| --- | --- |
| `id` | H3 index (feature id via `promoteId`) |
| `lv` | precomputed land-value index (default weights) |
| `c0..c4` | normalized components, order = `manifest.components` |
| `ea` | establishment access (gravity) |
| `pwd` | POI weighted density |
| `rdk` | road density, km per cell |
| `dcbd` | distance to downtown, km |
| `pc` | POI count in cell |
| `bs` | built-up score |
| `mt` | in metro (0/1) |

The land-value index is recomputed **in the browser** from `c0..c4` and the
weight sliders (`Σ wᵢ·cᵢ / Σ wᵢ`, then min-max to 0-100), so tuning is instant
and server-free. `<slug>_water.geojson` features carry `id` + `kind`
(`excluded_cell` or `mapped_water`); `<slug>_pois.geojson` features carry `cat`.

## Configuration

Most project behavior is controlled from `config.yaml`.

Important fields:

- `city.place`: OSM-geocodable city name.
- `city.osm_id`: optional exact OSM boundary object tried before fuzzy place
  search.
- `city.osm_id_fallbacks`: hardcoded fallback IDs for known troublesome city
  names in the web app builder. `zamboanga city` currently maps to `R3617877`.
- `city.place_aliases`: simple name aliases for ambiguous searches. `surigao`
  currently maps to `Surigao City, Philippines`.
- `city.study_buffer_km`: buffer around the official city boundary.
- `grid.h3_resolution`: H3 resolution. Resolution 8 is the current city-scale
  default.
- `cbd.lat` / `cbd.lng`: optional manual CBD override. Leave both `null` for
  auto-detection.
- `poi_categories`: OSM tags and weights used for establishment pull.
- `roads.major_classes`: OSM highway classes treated as arterials.
- `water.require_reachable`: when true, removes likely sea cells that have no
  nearby roads, POIs, or boundary contact.
- `landvalue.decay_scale_km`: distance decay scales for accessibility.
- `landvalue.weights`: model component weights.
- `metro.urban_percentile`: built-up threshold for metro delineation.
- `osm.overpass_urls`: Overpass endpoints tried in order when downloading OSM
  roads, POIs, and water.
- `osm.point_boundary_km`: radius used when OSM has a city point but no boundary
  polygon.

## Metro Principles

- The administrative boundary is only the study envelope.
- Downtown is detected from the smoothed built-up core, with road density
  carrying the signal when POI data is sparse.
- The metro area is the contiguous urban core connected to downtown.
- Empty cells with no POIs or roads stay non-urban.
- Large rural or island portions of a city should remain outside the metro
  unless they have enough built-up signal.

## Model Summary

The current model is intentionally unsupervised and interpretable:

```text
value = sum(weight_i * normalize(component_i)) / sum(weight_i)
```

Components:

```text
access_cbd
access_major_road
establishment_access
poi_density
road_density
```

Distance features become access scores with exponential decay. Density and
gravity-style features are percentile ranked to reduce the impact of skew and
outliers. The final `land_value_index` is min-max scaled to 0-100.

The metro footprint is a graph problem over the H3 lattice:

1. Compute built-up score from POI density and road density, preserving true
   zero values for cells with no signal.
2. Mark positive-signal cells above `metro.urban_percentile` as urban.
3. Find the H3 cell containing the CBD, or the nearest urban cell to it.
4. Keep the contiguous urban component connected to that seed.

## Data And Generated Files

Generated files are intentionally not committed:

- `data/osm_cache/*.parquet`
- `data/*_features_*.parquet`
- `data/*_metro_*.geojson`
- `data/*_context_map_*.html`
- `webapp/data/*.geojson`
- `webapp/data/manifest.json`

Keep `data/.gitkeep` and `webapp/data/.gitkeep`.

## Development Notes

- The code imports local modules by inserting `src/` into `sys.path` from the
  scripts. There is no package install step yet.
- `src/metro/grid.py` wraps the H3 API so both H3 v3 and v4-style names can be
  tolerated.
- `src/metro/data.py` uses exact OSM ID lookup with `by_osmid=True` when
  `city.osm_id` is provided, then falls back to text search if the exact lookup
  fails.
- `pipeline.run()` always reruns the cheap land-value and metro model after
  loading cached features. Cached parquet stores feature-stage outputs only.
- Cached feature tables are checked against the loaded city geometry before
  reuse, so old synthetic fallback outputs cannot silently place a real city in
  the wrong geography.
- OSM ID-backed caches include the ID in their cache key so exact lookups do not
  reuse fuzzy-search caches for the same city name.
- Some OSM places only geocode to a point, not a polygon. For those, the backend
  creates a small point-buffer boundary so the app can still build a city-scale
  study area instead of falling back to a province.
- `export_webapp.py` writes `<slug>_water.geojson` for the water toggle. It is
  visual context only; water cells are still excluded from the land-value model.
- `export_webapp.py` shortens property names for browser payload size. If you
  add or rename model fields, update both `COMPONENTS`/`METRICS` there and the
  matching logic in `webapp/app.js`. See "Web Data Contract" above.
- `webapp/index.html` loads `style.css?v=N` and `app.js?v=N` with a version
  query. After editing either asset, bump `N` in both `<link>`/`<script>` tags,
  or browsers may serve a stale cached copy on reload.
- CLI builds may silently fall back to synthetic data when OSM fails. The
  browser city builder rejects accidental synthetic fallback for real city
  requests.

## Suggested Next Work

- Add tests for H3 grid construction, water masking, land-value scaling, and
  manifest upsert behavior.
- Add a supervised model path once real price labels are available.
- Add travel-time or network accessibility in place of straight-line distance.
- Add night-lights, population, or built-up raster data to improve the urban
  mask.
- Package the project with `pyproject.toml` so scripts can avoid manual
  `sys.path` insertion.
