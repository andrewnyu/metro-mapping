# AGENTS.md

This file is a handoff guide for Codex or another agent picking up work in this
repository.

## Current State

Metro-Mapping is a working Python geospatial pipeline plus static browser app.
It builds H3 cell features for a Philippine city from OpenStreetMap, computes a
relative land-value index, delineates a functional metro area, attaches
area-level economic context, optionally applies a trained PHP/m² commercial vacant-land
model, and exports compact GeoJSON for MapLibre.

The worktree was clean when this handoff document was created. The main docs
were refreshed in `README.md`, and this `AGENTS.md` was added as the operational
agent guide.

## First Commands To Run

```bash
git status --short
python -m pip install -r requirements.txt
python scripts/build_dataset.py --synthetic
python scripts/train_price_model.py
python scripts/export_webapp.py --places "Cebu City, Philippines"
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
- `src/metro/economics.py`: area-level population, bank-deposit, and BLGF fiscal
  feature matching.
- `src/metro/pricing.py`: market-observation validation, city-grouped
  extra-trees training, score/area calibration, uncertainty bounds, and
  artifact inference.
- `src/metro/pipeline.py`: cache paths and end-to-end glue.
- `scripts/build_dataset.py`: CLI build/report path.
- `scripts/export_webapp.py`: web app export contract and manifest writing.
- `scripts/build_economic_reference.py`: builds the ignored economic reference
  from the sibling bank project, PSA seed, and optional canonical BLGF CSV.
- `scripts/train_price_model.py`: fits the PHP/m² market-baseline artifact from
  the tracked source-linked observations. `import_land_prices.py` remains an
  optional future cell-level path.
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
   `in_metro`. It uses a compatible learned positive-weight artifact when
   present, otherwise the transparent config weights.
8. Cached feature tables are validated against the loaded city geometry before
   reuse. This prevents old synthetic fallback outputs from being reused for a
   later successful OSM build with the same slug.
9. `export_webapp.py` writes compact `webapp/data/*` GeoJSON and
   `manifest.json`.
10. The export includes `<slug>_water.geojson`, built from cells excluded by the
   land mask plus mapped water polygons. It is rendered only as visual context.
11. `pipeline.run()` attaches area economics and applies the trained market
    baseline. A qualifying listing-rich top-market sample overrides only the
    local anchor, which is placed at a configured score quantile and spread
    across cells with the relative land-value index. Local anchor-confidence
    intervals replace the much wider global residual band for those cities.
    A city without listings uses the three closest anchored donors by bank
    deposits per analyzed land cell; donor anchors are scaled by the exact
    target/donor deposit-density ratio and similarity-weighted. Price columns
    are populated only where `in_metro` is true.
12. `webapp/app.js` keeps **Metro Area** and **Commercial Prices** in separate tabs.
    The metro tab recomputes the relative score from `c0` through `c4`; pricing
    never changes `is_urban`, `in_metro`, or the metro polygon.

## Generated Files

Do not treat generated data as source unless the user explicitly asks to commit
it. These paths are normally gitignored:

- `data/osm_cache/`
- `data/*_features_*.parquet`
- `data/*_metro_*.geojson`
- `data/*_context_map_*.html`
- `data/economic_features.csv`
- `data/commercial_land_price_listings.csv`
- `data/models/land_price_model.{joblib,json}`
- `data/models/landvalue_weight_model.json`
- `webapp/data/*.geojson`
- `webapp/data/manifest.json`
- `scripts/__pycache__/`

Keep `.gitkeep` files in generated directories.

## Verification Recipes

Fast offline smoke test:

```bash
python scripts/build_dataset.py --synthetic
```

Synthetic web exports use a distinct `_synthetic` slug and upsert their
manifest entry, so they must never be changed back to overwrite the real city
filenames or replace the whole manifest.

Normal real-city exports also upsert the manifest and reject accidental
synthetic fallback before writing browser files. `--replace-manifest` is the
only path that intentionally replaces the saved-city list.

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
- "Metro Area" and "Commercial Prices" are separate tabs
- H3 cells are colored
- city summary appears in the sidebar
- weight sliders update colors immediately
- "Metro cells only" filters cells
- "Bodies of water" fills excluded cells/polygons so the map has no unexplained
  holes
- "POIs" toggles points
- clicking a cell opens the detail panel
- the Commercial Prices tab shows the local anchor or comparable-city donors,
  learned positive spatial weights, interval method, and PHP/m² only for metro
  cells
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
    property keys `c0..c4`, `ea`, `pwd`, `rdk`, `dcbd`, `pc`, `bs`, `mt`,
    `rvs`, `ppsm`, `plo`, `phi` and the manifest fields the frontend reads)
- Keep `reference_data/commercial_land_top_market_anchors.json` separate from the
  cross-city training observations. It is a local calibration input, not a way
  to duplicate one city's rows inside the economic ML model.
- Keep spatial weight labels source-backed in
  `reference_data/commercial_land_spatial_markets.geojson`. Training must exclude
  unmatched and outside-metro points, retain non-negative/simplex constraints,
  report leave-one-market-area-out diagnostics, and preserve the configured
  Philippine domain floors for major-road, establishment, and POI effects.
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
  the contiguous component connected to the CBD. Preserve `metro.bridge_gap`,
  but only as a bridge across excluded non-land cells; do not let it hop over
  ordinary rural/mountain land, because that attaches places like Toledo to
  Metro Cebu. Connector cells may bridge short supported land gaps only when
  they attach a sizable nearby urban component, and must stay separately flagged
  for web-app highlighting/audit. Relative ranks (`builtup_score`, land-value)
  are for price/display only and must not decide the boundary.
- Keep population/deposits/tax receipts explicitly area-level. They matter only
  in a pooled multi-city price model and must be evaluated with city-grouped
  validation. Never report a random listing-row split as the primary score.
- `relative_value_share` is proportional to `score * cell_area`. For PHP/m²,
  normalize by the area-weighted mean score; dividing only by total score makes
  the unit price depend on the number of H3 cells.
- Train only on commercial vacant-land rows. Residential/subdivision labels and
  improved properties must be rejected. Preserve listing source URL/date and
  treat advertised prices as estimates, not completed transactions.
- If a city has sparse POI data, CBD detection should still use the road-density
  core rather than falling back to an arbitrary H3 cell.
- `src/metro/data.py` fetches POIs in one combined Overpass request, then
  classifies rows into local categories. Preserve the category-by-category
  fallback, but avoid reintroducing category-by-category as the default because
  it slows first-time generation.
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

- The unit suite covers economics, pricing, and web-manifest preservation;
  metro delineation still needs dedicated regression tests.
- First-time real OSM builds can be slow and require network access.
- Public Overpass instances can refuse connections or hang. `config.yaml` has
  `osm.overpass_urls` fallbacks plus a shorter request timeout.
- Dataset CLI builds are intentionally forgiving and may fall back to synthetic
  data on OSM failures. Web exports and the browser builder reject accidental
  synthetic fallback for real city requests before writing browser outputs.
- Synthetic fallback should not be cached under a normal real-city feature
  path. If a city renders in the wrong geography, inspect the feature parquet
  bounds against `data/osm_cache/<slug>_boundary.parquet` and rebuild with
  `--rebuild`.
- `config.yaml` includes hardcoded web-builder OSM ID fallbacks for names that
  fuzzy geocoding may match to the wrong object: Bacolod City (`R11349321`),
  Puerto Princesa City (`R9481097`), and Zamboanga City (`R3617877`).
- The Commercial Prices tab uses a trained advertised-price model, not a
  transaction-price appraisal. The pooled artifact currently has about PHP
  42,475/m² held-out-city MAE and 59.6% median percentage error. Prefer local
  anchors; for unlisted cities expose the comparable donors and exact
  deposits-per-land-cell ratios. Anchor and donor confidence intervals are not
  the full range of parcel asking prices; keep that distinction visible.
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
- Add completed-sale/registry labels and more current commercial-lot markets.
- Add network/travel-time accessibility using the road graph.
- Improve mobile layout verification with Playwright once a browser test setup
  exists.
