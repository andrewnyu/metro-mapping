# 🛰️ Metro-Mapping

Define the **functional metro area** of a Philippine city (which is loosely
defined in practice) and model the **relative land value** of every area from
its proximity to downtown, major establishments and main roads.

Built the way a Maps/Uber spatial team would: an **H3 hexagonal grid** as the
unit of analysis, **OpenStreetMap** as the data source, a transparent
**accessibility-based land-value index**, and a **data-driven metro boundary**.

---

## Why this design

| Decision | Rationale |
|---|---|
| **H3 hexagons** (Uber H3) as the spatial unit | Equidistant neighbours → clean contiguity, spatial smoothing, and a native map layer. The industry standard for spatial value modelling. |
| **OpenStreetMap** via OSMnx | Free, global, good PH coverage for roads + POIs. Swappable for PSA / cadastral data later. |
| **Auto-detected CBD** | Downtown = peak of neighbourhood-smoothed commercial-POI density, not a hardcoded pin (override in `config.yaml`). |
| **Land value = weighted accessibility index** | Transparent and tunable now; a clean placeholder you replace with a supervised model once you have real ₱/m² labels — same feature table. |
| **Metro = contiguous urban core** | The block of "urban" cells (built-up score above a percentile) connected to downtown on the H3 lattice. Mirrors how urban extents / commuting zones are delineated. |
| **Water exclusion** | Coastal cities put many grid cells over the sea. OSM stores inland water (lakes/rivers/reservoirs) as polygons but **not the open ocean**, so a cell is kept as land only if it (or a neighbour) has road coverage, has a POI, or touches the boundary — that's what removes the sea. |
| **Per-city caching** | Every layer (boundary, POIs, roads, water) and the feature table are cached to `data/` keyed on city + buffer + resolution, so switching back to a city is instant (~2 s vs. minutes). |
| **Synthetic fallback** | If OSM is unreachable, a synthetic mono-centric city (with a bay) is generated so the whole pipeline + app still run. |

## Install

```bash
cd metro-mapping
python -m pip install -r requirements.txt
```

## Run

**1 — Build the feature table** (fetches OSM the first time, then caches):

```bash
python scripts/build_dataset.py                       # uses config.yaml (Cebu City)
python scripts/build_dataset.py --place "Davao City, Philippines"
python scripts/build_dataset.py --synthetic           # offline demo
python scripts/build_dataset.py --rebuild             # ignore cache
```

**2 — Launch the web app** (primary — a MapLibre static site, same framework as
the PH-bank-deposits explorer):

```bash
python scripts/export_webapp.py       # export static GeoJSON/JSON into webapp/data
bash webapp/serve.sh                   # → http://localhost:8010
```

`serve.sh` runs a tiny Python server (`webapp/serve.py`) that serves the static
site **and** a build endpoint, so you can **add cities straight from the browser**:
type a name in *"Add a city"* and watch a progress bar as it fetches OSM, builds
the model, and drops the new city into the switcher.

You can also pre-export several cities from the CLI:

```bash
python scripts/export_webapp.py --places "Cebu City, Philippines" "Iloilo City, Philippines"
```

## What the web app gives you

- An **H3 choropleth** on a real OpenStreetMap/CARTO basemap (street & place
  names for context), colour by land value, accessibility, POI/road density,
  distance to downtown, or built-up score.
- **Live land-value weights** — the index is recomputed *in the browser* from
  exported normalised components, so the sliders are instant with no server.
- The **metro boundary** (red dashed) + a "metro cells only" view.
- Downtown marker, POIs by category, colour-scale toggle, ranked top-cells list,
  and a click-through **detail card** with a per-factor contribution chart.
- A **city switcher**, plus an in-app **"Add a city"** generator with a live
  progress bar (builds a new city from OSM via `webapp/serve.py`).

## Layout

```
config.yaml               # the one place you tune city, grid, weights, thresholds
scripts/
  build_dataset.py        # CLI: build + cache the feature table, print a summary
  export_webapp.py        # CLI: export static GeoJSON/JSON for the web app
webapp/                   # MapLibre static site (index.html, style.css, app.js)
  serve.py                # static server + /api/build SSE city generator
  data/                   # exported per-city GeoJSON + manifest.json (gitignored)
src/metro/
  config.py               # YAML config loader (+ live overrides)
  data.py                 # OSM boundary/roads/POIs/water  +  synthetic fallback
  grid.py                 # H3 grid construction (v3/v4-safe wrappers)
  features.py             # per-cell features: dist-to-CBD, road/POI density, gravity access, water mask
  landvalue.py            # land-value index  +  metro delineation
  mapviz.py               # folium context map + metro polygon (GeoJSON) export
  pipeline.py             # glue: data → grid → features (cached) → model
data/                     # cached OSM + feature parquet (gitignored)
```

## The land-value model (current vs. next)

**Now (unsupervised, interpretable):**

```
value = Σ_i  weight_i · normalise(component_i)
components = { access_cbd, access_major_road, establishment_access,
              poi_density, road_density }
```

Distances become access via `exp(-dist / scale)` (closer = higher, diminishing
returns); density features are percentile-ranked. Weights live in `config.yaml`.

**Next (supervised):** collect real listing / transaction ₱/m² (Lamudi,
DOF zonal values, BIR), join to cells, and fit gradient boosting on the *same*
features. The app and feature pipeline don't change — only `landvalue.py`.

## Roadmap ideas

- Real price labels → supervised model + SHAP feature attribution.
- Network/travel-time access (isochrones) instead of straight-line distance.
- Night-lights / built-up area (VIIRS, WorldPop) to sharpen the urban mask.
- Multi-city run + a "metro-ness" score to compare delineations.
