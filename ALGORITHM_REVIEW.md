# Metro delineation review — 2026-09-08

The method is useful for exploratory **built-up activity footprints**. It is
not yet validated well enough to treat the precise edge as an authoritative
functional metropolitan boundary. The distinction matters: the
[EU–OECD functional urban area](https://www.oecd.org/en/publications/the-eu-oecd-definition-of-a-functional-urban-area_d58cb34d-en.html)
includes a city and its commuting zone. Density and establishments alone do
not measure commuting integration.

## What is working

- Absolute eligibility can contract a rural chartered city and expand a small
  conurbated city. Puerto Princesa versus Iloilo remains a useful qualitative
  sanity check, rather than a numerical target to tune toward.
- Population adds urban evidence where OSM activity is missing. Butuan's fetch
  repair and its independent 141k population centre demonstrate why data
  quality must be checked before interpreting a zero metro.
- CBD connectivity and the rural-gap guard stop arbitrary remote urban cells
  being merged. Separate connector flags make exceptions inspectable.
- Automatic CBD candidates are constrained to the requested core-city boundary.
  The added-city audit caught San Fernando's initial seed outside its boundary;
  after correction, every audited city has an in-boundary seed.
- Population sums, night-light means, provenance, deterministic connectors,
  cache validation and regression tests make the pipeline reproducible.

## Priority findings

### 1. Physical road length is counted twice on most two-way roads

`data._fetch_roads()` exports directed drivable graph edges. Reverse-direction
edges remain in the GeoDataFrame, and `features._road_density()` sums every
edge after clipping. Comparing normalized geometry shows total length is
**1.92× unique geometry in Cebu, 1.98× in Butuan, and 1.94× in Iloilo**.
This affects road eligibility, connector support, the CBD and relative scores.
The current 4 km/cell setting is therefore approximately directed-edge km,
not 4 km of unique physical roadway.

Next: deduplicate physical segments while preserving distinct carriageways,
then recalibrate the urban and connector road bars and rerun all cities. Do
not simply deduplicate and claim the same thresholds preserve the old model.
No road features or thresholds were changed in this city-addition pass.

### 2. The boundary is materially sensitive to its OSM bars

The read-only audit changes both POI and road thresholds together, keeping
population thresholds and connectivity rules fixed:

- Base: 3 POI rows or 4 road km/cell with establishment corroboration.
- Looser: 2 POI rows / 3 road km/cell.
- Stricter: 4 POI rows / 5 road km/cell.

For the original 17-city set, Cebu changes **+23.5% / −15.9%**, Iloilo
**+49.1% / −25.8%**, Tagbilaran **+81.4% / −49.7%**, and Talibon
**+59.4% / −69.6%**. These are changes in summed H3 metro area, not estimation
errors or calibrated confidence intervals. The changes to two bars are
substantial, so this is a stress test rather than a tiny-perturbation test.
The second extension reinforces this concern: Tandag changes **+100% / −95.7%**
and Tacurong **+124.6% / −29.4%**. Cauayan was removed from the published app
because it had no qualifying population urban centre and was highly
threshold-sensitive.

Next: report a stable core and a sensitivity fringe, test one parameter at a
time, and calibrate against withheld cities. Repeat at multiple H3 resolutions:
raw per-cell counts/road lengths do not transfer unchanged across resolutions.

### 3. Agreement with population is no longer independent validation

Cebu's 0.68 OSM-only IoU was an independent signal comparison. Once urban
eligibility includes the same population centres, the rise to 0.75 combined
IoU is expected by construction; it is not an out-of-sample accuracy gain.
Likewise, the correct metro/admin ratio spread is a sanity check, not ground
truth. Seek independent built-up or PSA urban-area labels, followed by commuting
or travel-time evidence, and hold out entire cities during tuning.

The H3 classifier is an adaptation of DEGURBA thresholds; the
[reference approach uses a 1 km grid](https://publications.jrc.ec.europa.eu/repository/handle/JRC109075).
Its connectivity and hole-filling rules are not identical to the official grid
method. WorldPop 2020 and GIBS 2016 also describe different dates.

### 4. OR eligibility cannot reject OSM-only false positives

Talibon and Tandag remain OSM-supported without a qualifying ≥50k
population centre. That could represent a town's useful activity footprint,
but it does not establish metropolitan status. Similar disagreements in city
fringes deserve inspection. A 90th-percentile night-brightness cut would not solve this: it
always selects a share of each study area, and GIBS is not calibrated radiance.

Next: distinguish centre qualification from footprint delineation. Evaluate
built-up surface as corroboration for uncertain fringes; keep calibrated lights
optional until product-specific thresholds have been validated.

### 5. Check the study edge, seed and source geometry

The original-city audit finds **11 Cebu metro cells on the outer study-grid
edge**. The extensions find **54 for Metro Manila**, **17 for San Fernando,
Pampanga**, and **7 for Dagupan**. This flags possible clipping; it does not prove
how far the metro should extend. A 12 km envelope should be tested against a
larger envelope using the same criterion. Do not interpret an envelope edge as
a natural urban boundary.

The current CBD detector actually combines **80% ranked road density and 20%
ranked POI density**, then smooths over two H3 rings. Candidate cells now stay
inside the core-city boundary; this fixes the neighbouring-centre failure the
audit exposed for San Fernando. Earlier POI-peak descriptions overstated the
POI role. Eligibility is absolute, but seed selection still uses ranks, and a
polycentric urban region may eventually need multiple seed candidates.

POI counts are category rows, not guaranteed distinct establishments: one OSM
feature may match several categories, and the export discards original OSM IDs.
Preserve IDs and count distinct features for the absolute bar while retaining
category weights for accessibility. Population classification also occurs
after the OSM-derived land mask, so excluded land cannot be recovered by it.

OSM boundaries are not always official land-area denominators. Metro Manila's
relation contains offshore water; Surigao uses a point buffer. Compare ratios
only after inspecting that source geometry and its provenance.

## Reproduce the diagnostics

```bash
python scripts/audit_metros.py --output-json /tmp/metro-audit.json
python scripts/prototype_population.py --output-json /tmp/metro-population.json
```

The audit does not rewrite features or exports. It may fill raster aggregate
caches through the normal pipeline. Edge contact, signal disagreement and
threshold response are review signals, not automatic correctness judgments.
