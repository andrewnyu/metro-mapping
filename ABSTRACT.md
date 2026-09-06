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
hexagons (~0.46 km²). From OpenStreetMap we extract the administrative boundary,
weighted points of interest, the drivable road network and water polygons.
Cells over mapped water, and unreachable open-sea cells, are removed by a
reachability rule. The central business district is detected as the peak of
neighbourhood-smoothed weighted POI density rather than being pinned by hand.

The delineation itself rests on two deliberate choices. First, a cell is judged
**urban on an absolute bar** — a minimum count of establishments, or a dense
road grid that is corroborated by nearby establishment gravity — rather than by
its percentile rank within its own city. A relative cut necessarily returns a
fixed fraction of whatever city it is pointed at, and cannot distinguish a
compact metro from a sprawling rural jurisdiction. Second, the metro is the set
of urban cells **contiguously connected to the CBD on the H3 lattice**, where
contiguity may bridge short non-land gaps so that districts separated by a
channel or bay (for example Mactan across the Cebu channel) remain attached.

Applied to 17 cities, the method contracts sharply for large rural
jurisdictions — recovering 1.3% of Puerto Princesa's and 3.4% of Zamboanga
City's administrative area — while expanding *beyond* city limits where genuine
conurbation exists, reaching 105% of Cebu City's and 182% of Iloilo City's
administrative area by absorbing adjacent municipalities. The resulting
footprints are intended as a base layer for commute-shed and travel-demand
estimation, utility and infrastructure planning, and market sizing.

Cell-level land valuation is treated as **future work**: usable Philippine
transaction data is scarce, and asking-price listings are thin and unevenly
distributed. The repository retains an interpretable relative accessibility
index and an experimental commercial price baseline, but these are not claims
about market value.

## Selected results

Metro footprint versus administrative area (H3 res 8, 12 km study buffer):

| City | Admin area (km²) | Metro area (km²) | Metro / admin |
| --- | ---: | ---: | ---: |
| Puerto Princesa | 4,926.6 | 63.6 | 1.3% |
| Zamboanga City | 4,053.2 | 139.3 | 3.4% |
| Davao City | 2,489.9 | 473.6 | 19.0% |
| Cagayan de Oro | 533.5 | 216.1 | 40.5% |
| Bacolod City | 313.6 | 285.5 | 91.0% |
| Cebu City | 320.9 | 338.3 | 105.4% |
| Iloilo City | 109.5 | 199.3 | 182.0% |

The spread is the point: a single absolute rule reproduces both the
"huge jurisdiction, small city" and the "small jurisdiction, spilled metro"
cases without per-city tuning.

## Status and scope

- **In scope now:** metro delineation, the land/water mask, CBD detection,
  per-cell accessibility features, and the browser map.
- **Deferred (future project):** peso-per-square-metre land valuation. Kept in
  the repository as an experiment, not a headline result.
- **Next data layers under consideration:** electrification and grid
  infrastructure (especially relevant to the Visayas supply situation),
  night-time lights, gridded population, terrain buildability, and
  network travel time. See "Roadmap" in `README.md`.

## Limitations

- OpenStreetMap POI completeness varies by city, so the absolute urban bar is
  sensitive to mapping effort; road density with establishment corroboration
  partly compensates.
- Accessibility uses straight-line distance, not network travel time.
- The 12 km buffer bounds how far a metro can extend and should be raised for
  the largest conurbations.
- Administrative areas reported by the pipeline depend on the OSM boundary
  resolving to a polygon; a few cities fall back to a point buffer.
