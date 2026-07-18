# AGENTS.md

This file is a handoff guide for Codex or another agent picking up work in this
repository.

## Current State

Metro-Mapping is a working Python geospatial pipeline plus static browser app.
It builds H3 cell features for a Philippine city from OpenStreetMap, computes a
relative land-value index, delineates a functional metro area, and exports
compact GeoJSON for MapLibre.

The worktree was clean when this handoff document was created. The main docs
were refreshed in `README.md`, and this `AGENTS.md` was added as the operational
agent guide.

## First Commands To Run

```bash
git status --short
python -m pip install -r requirements.txt
python scripts/build_dataset.py --synthetic
python scripts/export_webapp.py --synthetic
bash webapp/serve.sh
```

Open `http://localhost:8010` after serving. If the default port is busy, inspect
`webapp/serve.sh` or run `python webapp/serve.py <port>` from the repo root.

Real OSM builds need network access and can take minutes on first run:

```bash
python scripts/build_dataset.py --place "Cebu City, Philippines"
python scripts/export_webapp.py --places "Cebu City, Philippines"
```

## Important Files

- `config.yaml`: first place to adjust city, buffer, H3 resolution, POI tags,
  model weights, and metro threshold.
- `src/metro/data.py`: OSM geocode/fetch/cache layer and synthetic fallback.
- `src/metro/features.py`: feature engineering, CBD detection, water masking,
  road density, POI density, and establishment access.
- `src/metro/landvalue.py`: normalized land-value score and contiguous metro
  component logic.
- `src/metro/pipeline.py`: cache paths and end-to-end glue.
- `scripts/build_dataset.py`: CLI build/report path.
- `scripts/export_webapp.py`: web app export contract and manifest writing.
- `webapp/app.js`: MapLibre app, live land-value weight recomputation, city
  build UI, layer toggles, detail panel.
- `webapp/serve.py`: static server plus `/api/build` SSE endpoint.

## Architecture In One Pass

1. `load_config()` reads `config.yaml` into a thin `Config` dict wrapper.
2. `pipeline.run()` calls `load_or_build_features()`.
3. `data.load_city_data()` fetches or loads cached OSM boundary, POIs, roads,
   major roads, and water. If `city.osm_id` is set, exact OSM ID lookup is
   tried before fuzzy text search; on failure it returns synthetic data.
4. `grid.build_grid()` fills the study region with H3 cells and adds a one-ring
   fringe to reduce edge clipping.
5. `features.build_features()` computes POI counts, road density, water mask,
   CBD, distance/accessibility fields, and attrs used by downstream reporting.
6. Feature parquet is cached in `data/`.
7. `landvalue.run_model()` recomputes the cheap model outputs every run:
   normalized components, `land_value_index`, `builtup_score`, `is_urban`, and
   `in_metro`.
8. Cached feature tables are validated against the loaded city geometry before
   reuse. This prevents old synthetic fallback outputs from being reused for a
   later successful OSM build with the same slug.
9. `export_webapp.py` writes compact `webapp/data/*` GeoJSON and
   `manifest.json`.
10. The export includes `<slug>_water.geojson`, built from cells excluded by the
   land mask plus mapped water polygons. It is rendered only as visual context.
11. `webapp/app.js` renders the app and recomputes the land-value index in the
   browser from exported normalized components `c0` through `c4`.

## Generated Files

Do not treat generated data as source unless the user explicitly asks to commit
it. These paths are normally gitignored:

- `data/osm_cache/`
- `data/*_features_*.parquet`
- `data/*_metro_*.geojson`
- `data/*_context_map_*.html`
- `webapp/data/*.geojson`
- `webapp/data/manifest.json`
- `scripts/__pycache__/`

Keep `.gitkeep` files in generated directories.

## Verification Recipes

Fast offline smoke test:

```bash
python scripts/build_dataset.py --synthetic
python scripts/export_webapp.py --synthetic
```

Real city smoke test, network required:

```bash
python scripts/build_dataset.py --place "Cebu City, Philippines"
python scripts/export_webapp.py --places "Cebu City, Philippines"
```

Serve and manually check the UI:

```bash
bash webapp/serve.sh
```

Expected browser behavior:

- map loads even if the raster basemap CDN is slow or blocked
- H3 cells are colored
- city summary appears in the sidebar
- weight sliders update colors immediately
- "Metro cells only" filters cells
- "Bodies of water" fills excluded cells/polygons so the map has no unexplained
  holes
- "POIs" toggles points
- clicking a cell opens the detail panel
- "Add a city" streams progress and registers a city when OSM succeeds

## Change Guidelines

- Prefer small, focused changes that preserve the existing pipeline shape.
- Update `README.md` and this file when changing commands, generated files, or
  the frontend/backend data contract.
- If you add a feature column used by the web app, update:
  - `scripts/export_webapp.py`
  - `webapp/app.js`
  - any labels or metrics in the manifest
  - the "Web Data Contract" section in `README.md` (documents the compact
    property keys `c0..c4`, `ea`, `pwd`, `rdk`, `dcbd`, `pc`, `bs`, `mt` and the
    manifest fields the frontend reads)
- After editing `webapp/app.js` or `webapp/style.css`, bump the `?v=N` query on
  both the `<link>` and `<script>` tags in `webapp/index.html`. Without it,
  browsers (and the preview harness) can serve a stale cached asset on reload,
  which looks like "my change did nothing".
- If you touch water masking or web exports, keep the water layer contract in
  sync: manifest city entries include `water`, and `webapp/app.js` loads it
  under the land-value cells.
- If you touch metro delineation, keep it on an **absolute** urban bar, not a
  percentile of the city's own distribution — a relative cut caps the metro at a
  fixed fraction of any city and made Cebu far too small. A cell qualifies by
  `metro.min_poi_per_cell`, or by `metro.min_road_km_per_cell` only when backed
  by `metro.min_establishment_access_for_road_cell`; this keeps rural road
  corridors inside huge city limits from ballooning the metro. Keep the metro as
  the contiguous component connected to the CBD, and preserve `metro.bridge_gap`
  so districts split by a water channel/park (Mactan across the Cebu channel)
  stay attached. Relative ranks (`builtup_score`, land-value) are for
  price/display only and must not decide the boundary.
- If a city has sparse POI data, CBD detection should still use the road-density
  core rather than falling back to an arbitrary H3 cell.
- If you touch city search or the web app build endpoint, preserve exact OSM ID
  fallback support. `webapp/app.js` sends optional `osm_id`, `webapp/serve.py`
  forwards it, `scripts/export_webapp.py` also checks `city.osm_id_fallbacks`,
  and `src/metro/data.py` calls `ox.geocode_to_gdf(..., by_osmid=True)`.
- Preserve place aliases and point-buffer fallback for city names that OSM does
  not expose as boundary polygons. Example: `surigao` maps to
  `Surigao City, Philippines`, which geocodes as a point and uses
  `osm.point_boundary_km`.
- If you change `config.yaml` structure, update `src/metro/config.py` only if
  the loader/helper semantics must change; most config consumers read dict keys
  directly.
- If you touch H3 behavior, keep compatibility with both h3 v3 and v4 wrappers
  in `src/metro/grid.py`.
- If you touch water masking, test both a coastal real city and
  `--synthetic`, since synthetic data includes a bay specifically to exercise
  the mask.
- If you touch city-building in the UI, preserve the SSE response protocol in
  `webapp/serve.py`: progress events use `{"frac": ..., "msg": ...}`, success
  uses `{"done": true, "city": ...}`, and failures use `{"error": ...}`.

## Known Caveats

- There is no formal test suite yet.
- First-time real OSM builds can be slow and require network access.
- Public Overpass instances can refuse connections or hang. `config.yaml` has
  `osm.overpass_urls` fallbacks plus a shorter request timeout.
- CLI builds are intentionally forgiving and may fall back to synthetic data on
  OSM failures. The web city builder rejects accidental synthetic fallback for
  real city requests.
- Synthetic fallback should not be cached under a normal real-city feature
  path. If a city renders in the wrong geography, inspect the feature parquet
  bounds against `data/osm_cache/<slug>_boundary.parquet` and rebuild with
  `--rebuild`.
- `config.yaml` includes hardcoded web-builder OSM ID fallbacks for names that
  fuzzy geocoding may match to the wrong object: Bacolod City (`R11349321`),
  Puerto Princesa City (`R9481097`), and Zamboanga City (`R3617877`).
- The land-value model is a relative proxy, not a trained price model. Real
  transaction/listing data is needed before interpreting it as currency value.
- Straight-line distance is used today; network travel time would be a better
  accessibility measure.
- `export_webapp.py` mutates the loaded config's `city.place` while looping
  through places. That is fine for the current script flow but worth remembering
  if refactoring it into shared long-lived service code.

## Good Next Tasks

- Add `pyproject.toml` and installable package metadata.
- Add unit tests around `landvalue.compute_land_value()`,
  `landvalue.delineate_metro()`, manifest upsert, and H3 wrappers.
- Add an integration test using `--synthetic`.
- Add a real label ingestion path for supervised price modeling.
- Add network/travel-time accessibility using the road graph.
- Improve mobile layout verification with Playwright once a browser test setup
  exists.
