# Metro-Mapping

Metro-Mapping is a Python geospatial pipeline and static MapLibre web app for
estimating the functional metro area of a Philippine city and mapping land
value over an H3 hex grid. It always provides an interpretable relative index;
after training on observed commercial vacant-land listings, it also provides
commercial peso-per-square-metre estimates with an indicative uncertainty band.

The project is built around a simple idea: fetch city-scale OpenStreetMap data,
turn the study region into H3 cells, compute accessibility and built-up
features per cell, combine them with city/metro economic context, and export
compact GeoJSON for a browser map. A pooled model is deliberately trained and
validated across cities so metro bank deposits, population, and local tax
receipts can explain market-level differences without pretending to be
cell-level measurements.

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
- Attaches metro bank deposits (PDIC/BSP), 2024 city population (PSA), and
  optional city local-tax receipts (BLGF) as area-level model features.
- Trains a commercial vacant-land market-baseline `PHP/m²` model from
  source-linked asking prices, holding out whole cities during validation and
  persisting model diagnostics.
- Allocates relative cell value in proportion to `score × cell area`, and uses
  the area-normalized score as the within-city price multiplier while
  preserving the ML market baseline as the area-weighted average price.
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
  build_economic_reference.py  Join bank, population, and optional BLGF data.
  import_land_prices.py   Optional future cell-level listing import path.
  train_price_model.py    Train the economic market-baseline model.
  train_landvalue_weights.py  Train constrained spatial-index weights.
  export_webapp.py        Export compact GeoJSON/JSON for the web app.

src/metro/
  config.py               YAML loader and Config helper.
  data.py                 OSM fetch/cache layer plus synthetic fallback.
  grid.py                 H3 v3/v4-compatible helpers.
  features.py             Cell feature engineering and water masking.
  landvalue.py            Land-value index and metro delineation.
  economics.py            Area-level economic matching and derived features.
  pricing.py              Listing validation, grouped training, and prediction.
  weight_training.py      Positive spatial-weight fitting and validation.
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
reference_data/           Source-linked PSA and commercial-land observations.
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

## Build Economic Features And The Peso Model

Build the economic reference from the supplied bank-deposit project:

```bash
python scripts/build_economic_reference.py \
  --bank-project /Users/andrewyu/prog/analytics/ph-bank-deposits
```

This writes `data/economic_features.csv`. Known core cities use the sibling
project's metro aggregate; other covered places retain their city-level bank
figure. The included main-metro population seed contains source-linked rows
from the PSA 2024 POPCEN city table. To add
fiscal data, pass `--fiscal-csv` with canonical BLGF columns:
`core_city`, `local_tax_revenue_php`, and optionally
`real_property_tax_php`, `business_tax_php`, `fiscal_year`, `fiscal_source`.
BLGF local receipts are used instead of BIR revenue-district collections
because revenue-district boundaries do not consistently equal LGU boundaries.

The tracked `reference_data/commercial_land_market_observations.json` contains
38 source-linked commercial vacant-land asking-price observations across ten
cities. Improved properties and residential/subdivision lots are rejected by
the normalization path. Train:

```bash
python scripts/train_price_model.py
```

The default guard requires at least 15 market observations across ten cities.
Extra-trees hyperparameters are selected inside each held-out-city
fold (nested grouped validation). The artifact and readable diagnostics are
written to `data/models/land_price_model.{joblib,json}`. `--allow-small-sample`
exists only for pipeline testing; a random listing-row score is not
decision-grade. An optional future coordinate-rich path remains available via
`commercial_land_price_listings_template.csv` and `import_land_prices.py`.

`reference_data/commercial_land_top_market_anchors.json` is a separate,
deduplicated
listing table for cities with a sufficiently deep prime-area sample. It does
not add repeated rows to or distort the cross-city ML fit. Instead, its robust
local median is placed at the configured top score quantile, then the
land-value index is applied across metro cells with a damped elasticity. The
included 26 prime-area observations qualify seven locally anchored metros.
For a metro without local listings, the three closest anchored cities in log
bank-deposits-per-analyzed-land-cell space become donors. Each donor anchor is
scaled by the target/donor deposit-per-cell ratio and similarity-weighted. A
city remains unpriced only when it has no functional metro cells or no matched
bank-deposit amount. The local-anchor interval estimates uncertainty in the
median anchor, not the full dispersion of parcel prices.

Train the interpretable spatial-index weights separately:

```bash
python scripts/train_landvalue_weights.py
```

The tracked `commercial_land_spatial_markets.geojson` matches source-backed commercial
medians to documented market-area or barangay points. Only labels that land in
the functional metro enter training. A positive simplex regression tunes the
five component weights with leave-one-market-area-out validation, while
Philippine domain floors prevent implausible negative or near-zero effects for
highway access, establishments, and POIs. The generated
`data/models/landvalue_weight_model.json` is applied to all cities; without it,
the configured transparent weights remain the fallback.

## Export And Serve The Web App

Export the configured city:

```bash
python scripts/export_webapp.py
```

Export multiple cities:

```bash
python scripts/export_webapp.py --places "Cebu City, Philippines" "Iloilo City, Philippines"
```

Exports upsert these cities into the existing manifest, so previously saved
places remain available. Use `--replace-manifest` only when intentionally
rebuilding the complete saved-place list from the supplied `--places` values.
Real-city exports refuse to register a synthetic fallback when OSM lookup
fails.

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

The web app keeps the functional footprint and pricing independent in two
tabs: **Metro Area** never reads the price model, while **Commercial Prices** displays
the trained PHP/m² surface only for cells inside that footprint. Outside-metro
cells intentionally have null price fields because current listing evidence
does not support defensible rural estimates. A metro without a qualifying
local commercial anchor also remains null. It also includes a "Bodies of
water" layer toggle generated from cells excluded by the land mask plus mapped
OSM water polygons, so bays, rivers, and open-water gaps read as water instead
of empty holes.

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
  `n_metro`, `n_connectors`, `n_pois`, the areas `metro_km2` / `city_km2` /
  `study_km2` / `land_km2`, `source` (`osm` or `synthetic`), and
  `source_error`, economic summary fields, price-model status/diagnostics, and
  local-anchor fields (`price_anchor_*`, baseline source, and interval method).
- `components`: the five model component keys, in slider order.
- `component_labels`: human labels for those five.
- `weights_default`: default component weights (also the reset target).
- `metrics`: exported metric definitions used by the two view tabs.
- `poi_categories`: category names (match POI `cat`).

`<slug>_cells.geojson` — one feature per land cell:

| key | meaning |
| --- | --- |
| `id` | H3 index (feature id via `promoteId`) |
| `lv` | precomputed land-value index (default weights) |
| `rvs` | cell share of scored study-area value (`score × area / total`) |
| `ppsm` | trained estimated commercial land price, PHP/m²; null outside the functional metro or without matched deposit evidence |
| `plo` / `phi` | price interval with the same metro-only scope: local anchor-confidence bounds where qualified, otherwise held-out-city residual bounds |
| `c0..c4` | normalized components, order = `manifest.components` |
| `ea` | establishment access (gravity) |
| `pwd` | POI weighted density |
| `rdk` | road density, km per cell |
| `dcbd` | distance to downtown, km |
| `pc` | POI count in cell |
| `bs` | built-up score |
| `mt` | in metro (0/1) |
| `cn` | connector cell used to bridge a supported land gap (0/1) |

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
  names in the web app builder. Current examples include Bacolod City
  (`R11349321`), Puerto Princesa City (`R9481097`), and Zamboanga City
  (`R3617877`).
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
- `weight_model.labels_file` / `weight_model.artifact`: source-backed spatial
  market labels and the learned positive-weight artifact.
- `weight_model.prior_weights` / `minimum_weights`: Philippine domain prior and
  monotonic floors used to stabilize the small spatial sample.
- `weight_model.regularization_grid`: candidates selected by the spatial
  training loop's leave-one-market-area-out validation.
- `economics.reference_file`: generated bank/population/fiscal feature table.
- `price_model.market_observations_file` / `price_model.artifact`: tracked
  cross-city market observations and trained artifact. `labels_file` is
  reserved for future coordinate-rich listing imports.
- `price_model.top_market_anchors_file`: deduplicated prime-area listings used
  only for qualifying local price anchors; these rows do not retrain or
  overweight the cross-city estimator.
- `price_model.minimum_market_observations` / `minimum_market_cities`: training
  safety guard.
- `price_model.minimum_top_anchor_observations`, `top_anchor_score_quantile`,
  and `relative_score_elasticity`: local-anchor quality gate and index-spread
  controls.
- `price_model.comparable_city_fallback`: donor count, deposit/cell similarity
  bandwidth, exact deposit-ratio exponent, and uncertainty limits used for
  metros without local commercial listings.
- `price_model.relative_score_blend`: how strongly the learned price surface is
  pulled toward the score-and-area relative allocation while preserving its
  citywide average.
- `metro.min_poi_per_cell` / `metro.min_road_km_per_cell`: the **absolute** bar
  for a cell to count as urban (establishments OR road density, per H3 cell).
- `metro.min_establishment_access_for_road_cell`: road-only cells need at least
  this much nearby establishment gravity before they count as urban.
- `metro.bridge_gap`: how many H3 rings the contiguous metro may jump to cross
  excluded non-land cells, such as water channels. It does not jump ordinary
  non-urban land.
- `metro.connector_gap`: maximum number of weak-but-supported land cells that
  can be added to attach the CBD-connected metro to a meaningful nearby urban
  component.
- `metro.connector_min_component_cells`: minimum size of an outside urban
  component before connector cells may attach it.
- `metro.connector_min_road_km_per_cell` /
  `metro.connector_min_establishment_access`: evidence required for each
  connector cell.
- `osm.overpass_urls`: Overpass endpoints tried in order when downloading OSM
  roads, POIs, and water.
- `osm.point_boundary_km`: radius used when OSM has a city point but no boundary
  polygon.

## Metro Principles

- The administrative boundary is only the study envelope.
- Downtown is detected from the smoothed built-up core, with road density
  carrying the signal when POI data is sparse.
- A cell is **urban** by an absolute bar — enough establishments *or* a
  dense-enough road grid with nearby establishment gravity — **not** a
  percentile of the city's own distribution. This keeps the metro from being
  capped at a fixed fraction of a city, while keeping rural road corridors in
  very large city limits out of the metro.
- The metro area is the urban cells **contiguously connected to downtown** on
  the H3 graph, allowing small **bridged gaps** only across excluded non-land
  cells so a district separated by a channel is not dropped, while mountain or
  rural land gaps do not attach a separate city.
- A short chain of **connector cells** may bridge ordinary land only when every
  connector has local road/access evidence and it joins the core to a sizable
  nearby urban component. Connector cells are flagged separately in the web app
  so they can be highlighted and audited.
- Relative land-value / density ranks are for **price prediction only**; the
  metro boundary never uses them.
- Empty cells with no POIs or roads stay non-urban; rural/island portions stay
  out unless they meet the absolute bar and connect to the core.

## Model Summary

The first layer is intentionally unsupervised and interpretable:

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
outliers. The final `land_value_index` is min-max scaled to 0-100. Its unit-safe
relative allocation is:

```text
cell_value_share_i = score_i * cell_area_i / sum(score_j * cell_area_j)
cell_price_multiplier_i = score_i / area_weighted_mean(score)
```

The second layer fits an extra-trees regressor to a commercial market-level
`log(PHP/m²)` baseline using population, metro/city deposits, deposit counts,
deposits per capita, and available BLGF tax receipts. It excludes the city name
itself and validates by holding out whole cities. When the target city has a
source-linked local market observation, that observed level calibrates the ML
baseline. Without local observations, the model selects the three anchored
cities with the closest bank deposits per analyzed land cell and calculates:

```text
donor_implied_price = donor_anchor_price
                    * (target_deposits / target_land_cells)
                    / (donor_deposits / donor_land_cells)
city_baseline = similarity_weighted_mean(donor_implied_price)
cell_price = city_baseline * cell_score / area_weighted_mean(metro_scores)
```

Thus the area-weighted mean of the metro-cell estimates equals the inferred
city baseline exactly; outside-metro cells remain null.

The target remains an advertised commercial vacant-land price unless true
transaction labels are supplied. It is an automated valuation estimate, not
an appraisal or a guaranteed sale price. The current 38-observation artifact
reports a held-out-city MAE of about PHP 42,475/m² and median absolute
percentage error of 59.6%. Local anchors therefore take precedence. Comparable-
city estimates disclose their donor cities and deposit/cell ratios in the web
app and should be treated as lower-confidence indicative estimates.

The metro footprint is a graph problem over the H3 lattice, kept independent of
the relative land-value score:

1. Mark a cell urban by an absolute bar: `poi_count >= metro.min_poi_per_cell`
   OR road density/access, where `road_density_km >= metro.min_road_km_per_cell`
   and `establishment_access >= metro.min_establishment_access_for_road_cell`.
2. Find the H3 cell containing the CBD, or the nearest urban cell to it.
3. Grow the contiguous urban component from that seed, allowing steps of up to
   `metro.bridge_gap` rings only when the skipped cells are outside the land
   grid, such as water/excluded cells.
4. Optionally add connector cells across short supported land gaps when they
   attach a sizable nearby urban component, then regrow the final component over
   urban plus connector cells.

(`builtup_score` is still computed as a relative rank, but only as a web-app
display metric — it no longer decides the boundary.)

## Data And Generated Files

Generated files are intentionally not committed:

- `data/osm_cache/*.parquet`
- `data/*_features_*.parquet`
- `data/*_metro_*.geojson`
- `data/*_context_map_*.html`
- `data/economic_features.csv`
- `data/commercial_land_price_listings.csv`
- `data/models/land_price_model.{joblib,json}`
- `data/models/landvalue_weight_model.json`
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
  fails. Text geocoding rejects non-administrative polygons such as schools,
  airports, malls, or parks before they can poison the city boundary cache.
- POIs are fetched with one combined Overpass query and then classified into
  local categories. If that combined request fails, the loader falls back to the
  older category-by-category fetch.
- `pipeline.run()` always reruns the cheap land-value and metro model after
  loading cached features, then attaches current economic context and applies a
  compatible price artifact when present. A compatible learned spatial-weight
  artifact replaces the fallback component weights before the score is built.
  Cached parquet stores feature-stage outputs only.
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
- Dataset CLI builds may silently fall back to synthetic data when OSM fails.
  Web exports and the browser city builder reject accidental synthetic fallback
  for real city requests before changing browser files or the manifest.

## Suggested Next Work

- Add tests for H3 grid construction, water masking, and land-value scaling.
- Add completed-sale/registry labels so asking-price bias can be measured and
  corrected; expand the current PSA population seed beyond the main metros.
- Add a source-specific, authorized collector for recurring commercial-lot exports
  and drift monitoring by observation date.
- Add travel-time or network accessibility in place of straight-line distance.
- Add night-lights or gridded population to improve within-city variation.
- Package the project with `pyproject.toml` so scripts can avoid manual
  `sys.path` insertion.
