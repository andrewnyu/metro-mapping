# Metro-Mapping — Abstract

**Delineating functional metropolitan areas of Philippine cities from open data**

## Abstract

Outside Metro Manila, Philippine metropolitan areas have no statutory boundary.
"Metro Cebu", "Metro Davao" and "Metro Iloilo" are used administratively and
colloquially without a consistent, reproducible definition, and the obvious
fallback — the city's administrative limits — is a poor proxy in both
directions. Several chartered cities hold enormous, largely rural
jurisdictions (Puerto Princesa, 4,927 km²; Zamboanga City, 4,053 km²), while
others are small and heavily conurbated with their neighbours (Iloilo City,
110 km²). Any analysis keyed to city limits therefore mis-states the population,
travel demand and infrastructure load it is trying to measure.

We present a reproducible, open-data method for delineating the *functional*
built-up metro area of a Philippine city. The study region is the city boundary
plus a configurable buffer (default 12 km), tiled with Uber H3 resolution-8
hexagons (roughly 0.8 km² in the study cities, using actual cell areas). From
OpenStreetMap we extract the administrative boundary,
weighted points of interest, the drivable road network and water polygons.
Cells over mapped water, and unreachable open-sea cells, are removed by a
reachability rule. The central business district is detected from a two-ring
smoothed score of 80% ranked road density and 20% ranked POI density, with
automatic candidates restricted to the requested core-city boundary rather
than being pinned by hand. WorldPop 2020 pixel
counts are summed per cell; a DEGURBA-inspired H3 classifier identifies
contiguous centres at ≥1,500 people/km² and ≥50,000 people, with limited gap
filling. This adapts the thresholds to H3 rather than claiming equivalence to
the official 1 km² raster method. Night lights are averaged per cell and
retain their source provenance.

The delineation itself rests on two deliberate choices. First, a cell is judged
**urban on an absolute bar** — a minimum count of establishments, or a dense
road grid that is corroborated by nearby establishment gravity, **or**
membership in a population urban centre — rather than by
its percentile rank within its own city. A relative cut necessarily returns a
fixed fraction of whatever city it is pointed at, and cannot distinguish a
compact metro from a sprawling rural jurisdiction. Second, the metro is the set
of urban cells **contiguously connected to the CBD on the H3 lattice**, where
contiguity may bridge short non-land gaps so that districts separated by a
channel or bay (for example Mactan across the Cebu channel) remain attached.

Applied to 17 cities, the method contracts sharply for large rural
jurisdictions — recovering 1.3% of Puerto Princesa's and 3.9% of Zamboanga
City's administrative area — while expanding *beyond* city limits where genuine
conurbation exists, reaching 119% of Cebu City's and 193% of Iloilo City's
administrative area by absorbing adjacent municipalities. These were 105%
and 182% under the previous OSM-only rule; the contraction/expansion spread
is preserved without per-city threshold tuning. Correcting Tagbilaran's
boundary yields a further cross-boundary case at 278%. The resulting
footprints are intended as a base layer for commute-shed and travel-demand
estimation, utility and infrastructure planning, and market sizing.

Cell-level land valuation is treated as **future work**: usable Philippine
transaction data is scarce, and asking-price listings are thin and unevenly
distributed. The repository retains an interpretable relative accessibility
index and an experimental commercial price baseline, but these are not claims
about market value.

## Results — 17-city rerun (2026-09-06)

H3 res 8, 12 km study buffer. Areas and ratios use the exported, lightly
smoothed metro polygon in a local metric projection; the population diagnostic
also reports summed H3 area, which differs slightly. Population is the sum of
WorldPop 2020 counts in selected cells, not a new census estimate.

| City | Admin area (km²) | Metro area (km²) | Metro / admin | Metro population |
| --- | ---: | ---: | ---: | ---: |
| Bacolod City | 313.6 | 291.0 | 92.8% | 717,821 |
| Butuan City | 666.7 | 132.1 | 19.8% | 237,560 |
| Cagayan de Oro | 533.5 | 232.3 | 43.5% | 934,654 |
| Cebu City | 320.9 | 380.6 | 118.6% | 2,927,324 |
| Davao City | 2,489.9 | 485.9 | 19.5% | 1,686,742 |
| Dumaguete City | 103.2 | 123.2 | 119.4% | 274,717 |
| Iloilo City | 109.5 | 211.8 | 193.4% | 680,983 |
| Kalibo | 293.0 | 139.6 | 47.6% | 205,804 |
| Ormoc City | 545.1 | 66.6 | 12.2% | 133,000 |
| Puerto Princesa City | 4,926.6 | 66.5 | 1.3% | 189,156 |
| San Carlos City | 673.2 | 21.4 | 3.2% | 60,802 |
| Surigao City | 200.7 | 23.7 | 11.8% | 107,832 |
| Tacloban City | 162.2 | 92.7 | 57.1% | 285,780 |
| Tagbilaran City | 52.8 | 146.5 | 277.6% | 191,060 |
| Tagum City | 256.4 | 249.9 | 97.5% | 443,573 |
| Talibon bohol | 545.5 | 55.4 | 10.2% | 47,272 |
| Zamboanga City | 4,053.2 | 157.4 | 3.9% | 680,316 |

The original comparison anchors still span a strongly contracting Puerto
Princesa (1.3%) and an expanding Iloilo (193.4%, previously 182%). Tagbilaran's
277.6% ratio is now interpretable because its denominator is the verified
52.8 km² city relation, rather than a 0.037 km² mall. Ormoc now uses its
545.1 km² city relation rather than the 0.834 km² City Proper district.

A subsequent nine-city extension preserves those original comparison anchors.
It also exposes where the fixed 12 km study envelope is too tight: Metro Manila
has 54 selected cells on the outer grid edge and San Fernando, Pampanga has 17.
Those outputs are flagged as possibly clipped rather than interpreted as
natural metro edges.

Butuan's repaired Overpass fetch returns 906 classified POIs and a 132.1 km²
combined footprint, recovering from zero. Its independent population centre
remains 31.2 km² / 141,203 people. Cebu's population-centre estimate remains
2,857,350; its OSM-only IoU remains 0.68 and combined IoU is 0.75. The night-light
versus log population-density Spearman correlation remains 0.73.

The OR rule retains Talibon's OSM footprint despite no qualifying ≥50k
population centre. The browser flags that disagreement for review; this is
not a claim that the population standard classifies Talibon as a metropolis.
Equal-length connector paths now use deterministic H3 ordering, so repeated
runs cannot change selected connector populations through Python set order.

## Status and scope

- **In scope now:** metro delineation, the land/water mask, CBD detection,
  per-cell accessibility/population features, source-labelled night lights,
  and the browser map.
- **Deferred (future project):** peso-per-square-metre land valuation. Kept in
  the repository as an experiment, not a headline result.
- **Next data layers under consideration:** electrification and grid
  infrastructure (especially relevant to the Visayas supply situation),
  calibrated multi-year night lights, built-up surface, terrain buildability, and
  network travel time. See "Roadmap" in `README.md`.

## Limitations

- OpenStreetMap POI completeness varies by city, so the absolute urban bar is
  sensitive to mapping effort; road density with establishment corroboration
  partly compensates; population centres add independent eligibility but do not
  veto OSM-only false positives.
- Accessibility uses straight-line distance, not network travel time.
- The 12 km buffer bounds how far a metro can extend and should be raised for
  the largest conurbations.
- Administrative areas depend on OSM geometry. Surigao still uses an explicit
  point buffer, so its 200.7 km² denominator is an analysis proxy, not an
  official administrative area. Ormoc and Tagbilaran now use verified relations.
- WorldPop counts are from 2020, while the default GIBS imagery is a 2016
  relative visualisation. GIBS never sets urban eligibility. A calibrated VIIRS
  road-corroboration threshold is optional and disabled until validated.
- Population classification runs inside the OSM-derived land mask and study
  envelope. Excluded cells and clipped clusters can suppress real centres.
- These are built-up activity footprints, not measured commuting zones;
  functional metro claims still require travel-flow validation.

For the subsequent city extensions and methodological audit, see
[`ALGORITHM_REVIEW.md`](ALGORITHM_REVIEW.md). The review distinguishes the
built-up footprint from a validated commuting-based functional urban area.
