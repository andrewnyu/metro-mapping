/* Metro-Mapping — metro area & calibrated commercial-land estimates (MapLibre GL) */
const state = {
  city: null, view: "metro", metric: "land_value", scale: "linear",
  weights: [], selected: null, hover: null,
  metroOnly: true, showMetro: true, showConnectors: false, showWater: false, showPois: false,
};
let MAN, CITY, CELLS, METRO, POIS, WATER, LV = {}, map, colorScale, chart, cbdMarker;

const $ = s => document.querySelector(s);
const CAT_COLORS = {
  mall: "#e6194B", office: "#4363d8", transport: "#3cb44b", school: "#f58231",
  hospital: "#911eb4", government: "#42d4f4", bank: "#bfef45", leisure: "#469990",
};
const fmt = (v, d = 1) => v == null || !isFinite(v) ? "—" : d3.format("," + "." + d + "f")(v);
const fmtMoney = v => v == null || !isFinite(v) ? "—" : "₱" + d3.format(",.0f")(v);
const fmtMetric = (v, m = metric()) => m?.key === "land_price" ? fmtMoney(v) : fmt(v, m?.key === "land_value" ? 0 : 1);
const metric = () => MAN.metrics.find(m => m.key === state.metric);

init();

async function init() {
  MAN = await fetch("data/manifest.json").then(r => r.json());
  state.weights = MAN.components.map(c => MAN.weights_default[c]);

  refreshCitySelect();

  // The tabs own the displayed metric. Metro delineation never depends on
  // the optional price model or its generated fields.
  $("#weights").innerHTML = MAN.component_labels.map((lbl, i) =>
    `<div class="wrow"><label>${lbl}</label>` +
    `<input type="range" min="0" max="1" step="0.05" value="${state.weights[i]}" data-i="${i}">` +
    `<span class="wval" id="wval${i}">${state.weights[i].toFixed(2)}</span></div>`).join("");

  buildMap();
  wireControls();
}

function refreshCitySelect(selectSlug) {
  const cs = $("#citySelect");
  cs.innerHTML = MAN.cities.map(c => `<option value="${c.slug}">${c.name}</option>`).join("");
  if (selectSlug) cs.value = selectSlug;
  // the dropdown is only useful with >1 city, but keep it visible so users
  // can see which city is active once they've generated more.
  $("#cityControl").classList.toggle("hidden", MAN.cities.length < 2);
}

/* ---------------- map ---------------- */
function buildMap() {
  // Start with a network-free style (just a background) so the map's `load`
  // event fires immediately and the app never hangs on a slow/blocked basemap
  // CDN. The raster basemap is added inside `load` and streams in when ready.
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8, sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#eaf0f6" } }],
    },
    center: [123.9, 10.3], zoom: 10.5, attributionControl: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

  map.on("load", () => {
    map.addSource("basemap", {
      type: "raster", tileSize: 256,
      tiles: ["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
              "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
              "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
      attribution: "© OpenStreetMap © CARTO",
    });
    map.addLayer({ id: "basemap", type: "raster", source: "basemap" });

    map.addSource("water", { type: "geojson", data: emptyFC(), promoteId: "id" });
    map.addLayer({
      id: "water-fill", type: "fill", source: "water",
      layout: { visibility: "none" },
      paint: { "fill-color": "#9fc5e8", "fill-opacity": 0.58 },
    });
    map.addLayer({
      id: "water-line", type: "line", source: "water",
      layout: { visibility: "none" },
      paint: { "line-color": "#ffffff", "line-width": 0.25, "line-opacity": 0.45 },
    });

    map.addSource("cells", { type: "geojson", data: emptyFC(), promoteId: "id" });
    map.addLayer({
      id: "cells-fill", type: "fill", source: "cells",
      paint: {
        "fill-color": ["coalesce", ["feature-state", "fill"], "#dfe4ea"],
        "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.92, 0.62],
        "fill-opacity-transition": { duration: 120 },
      },
    });
    map.addLayer({
      id: "cells-line", type: "line", source: "cells",
      paint: {
        "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#202124", "#ffffff"],
        "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 2.4, 0.3],
        "line-opacity": 0.5,
      },
    });
    map.addLayer({
      id: "connector-fill", type: "fill", source: "cells",
      filter: ["==", ["get", "cn"], 1],
      layout: { visibility: "none" },
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.26 },
    });
    map.addLayer({
      id: "connector-line", type: "line", source: "cells",
      filter: ["==", ["get", "cn"], 1],
      layout: { visibility: "none" },
      paint: { "line-color": "#1d4ed8", "line-width": 2.2, "line-opacity": 0.95 },
    });

    map.addSource("metro", { type: "geojson", data: emptyFC() });
    map.addLayer({
      id: "metro-line", type: "line", source: "metro",
      paint: { "line-color": "#c5221f", "line-width": 3, "line-dasharray": [2, 1] },
    });

    map.addSource("pois", { type: "geojson", data: emptyFC() });
    map.addLayer({
      id: "pois-pt", type: "circle", source: "pois",
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2, 14, 5],
        "circle-color": catColorExpr(), "circle-opacity": 0.85,
        "circle-stroke-width": 0.4, "circle-stroke-color": "#fff",
      },
    });

    map.on("mousemove", "cells-fill", onHover);
    map.on("mouseleave", "cells-fill", clearHover);
    map.on("click", "cells-fill", e => { if (e.features.length) select(e.features[0].id, true); });

    loadCity(MAN.cities[0].slug);
  });
}

function catColorExpr() {
  const expr = ["match", ["get", "cat"]];
  for (const [k, v] of Object.entries(CAT_COLORS)) expr.push(k, v);
  expr.push("#888");
  return expr;
}
const emptyFC = () => ({ type: "FeatureCollection", features: [] });

async function loadCity(slug) {
  state.city = CITY = MAN.cities.find(c => c.slug === slug);
  $("#loading").classList.remove("hidden");
  POIS = null; WATER = null;
  map.getSource("pois").setData(emptyFC());
  map.getSource("water").setData(emptyFC());

  const [cells, metro] = await Promise.all([
    fetch("data/" + CITY.cells).then(r => r.json()),
    fetch("data/" + CITY.metro).then(r => r.json()),
  ]);
  CELLS = cells; METRO = metro;
  map.getSource("cells").setData(CELLS);
  map.getSource("metro").setData(METRO);
  applyLayerState();

  if (cbdMarker) cbdMarker.remove();
  const el = document.createElement("div"); el.className = "cbd-marker";
  cbdMarker = new maplibregl.Marker({ element: el }).setLngLat(CITY.center).addTo(map);

  renderMatchNote();
  renderCityStats();
  renderPriceModelCard();

  clearSelection();
  recolor();
  const useMetroBounds = state.view === "prices" || (state.view === "metro" && state.metroOnly);
  const bounds = useMetroBounds ? geojsonBounds(METRO) : null;
  map.fitBounds(bounds || [[CITY.bbox[0], CITY.bbox[1]], [CITY.bbox[2], CITY.bbox[3]]],
    { padding: 36, duration: 600 });
  $("#loading").classList.add("hidden");
  if (state.showWater) ensureWaterLoaded();
  if (state.showPois) ensurePoisLoaded();
}

function activateView(view, fit = true) {
  state.view = view;
  state.metric = view === "prices" ? "land_price" : "land_value";
  state.scale = view === "prices" ? "log" : "linear";
  setActive("#viewTabs", $(`#viewTabs button[data-view="${view}"]`));
  $("#weightsControl").classList.toggle("hidden", view !== "metro");
  document.querySelectorAll(".metro-option").forEach(el =>
    el.classList.toggle("hidden", view !== "metro"));
  $("#priceModelCard").classList.toggle("hidden", view !== "prices");
  $("#cityMetricsLabel").textContent = view === "prices" ? "Price-market features" : "Metro metrics";
  if (CITY && CELLS) {
    applyLayerState();
    renderCityStats();
    renderPriceModelCard();
    renderMatchNote();
    clearSelection();
    recolor();
    if (fit) {
      const useMetroBounds = view === "prices" || (view === "metro" && state.metroOnly);
      const bounds = useMetroBounds ? geojsonBounds(METRO) : null;
      map.fitBounds(bounds || [[CITY.bbox[0], CITY.bbox[1]], [CITY.bbox[2], CITY.bbox[3]]],
        { padding: 36, duration: 450 });
    }
  }
}

function renderMatchNote() {
  if (!CITY) return;
  const source = CITY.source === "synthetic" ? "Synthetic demo data" : "OpenStreetMap";
  if (state.view === "prices") {
    const note = CITY.price_model_status === "trained"
      ? (CITY.price_market_baseline_source === "top_market_anchor"
        ? `${(CITY.price_anchor_n_observations || 0).toLocaleString()} local top-market listings · index-applied`
        : CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities"
        ? `${(CITY.price_comparable_city_donors || []).length} comparable metros · deposits/cell scaled · index-applied`
        : `${(CITY.price_model_n_market_observations || 0).toLocaleString()} markets · ` +
          `${(CITY.price_model_n_labels || 0).toLocaleString()} underlying listings`)
      : (CITY.price_model_status === "no_metro_cells"
        ? "commercial price withheld · no functional metro cells"
        : CITY.price_model_status === "insufficient_economic_evidence"
        ? "commercial price withheld · no matched bank-deposit evidence"
        : CITY.price_model_status === "insufficient_commercial_evidence"
        ? "commercial price withheld · insufficient local listings"
        : "commercial price model unavailable");
    $("#matchNote").textContent = `${source} geometry · ${note}`;
  } else {
    $("#matchNote").textContent = `${source} · ${CITY.n_pois.toLocaleString()} POIs`;
  }
}

function renderPriceModelCard() {
  if (!CITY || state.view !== "prices") return;
  const card = $("#priceModelCard");
  if (CITY.price_model_status !== "trained") {
    const insufficient = CITY.price_model_status === "insufficient_commercial_evidence";
    const noEconomic = CITY.price_model_status === "insufficient_economic_evidence";
    const noMetro = CITY.price_model_status === "no_metro_cells";
    card.innerHTML = `<div class="model-title">Commercial price unavailable</div>` +
      `<div>${noMetro
        ? "No cells currently qualify for the functional metro, so there is no supported pricing footprint."
        : noEconomic
        ? "No matched bank-deposit amount is available, so the comparable-city ratio cannot be calculated."
        : insufficient
        ? "This metro does not have enough deduplicated local commercial-lot listings to set a defensible peso anchor."
        : "Build the trained commercial-land artifact before using this view."}</div>`;
    return;
  }
  const isTopAnchor = CITY.price_market_baseline_source === "top_market_anchor";
  const isComparable = CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities";
  const donors = CITY.price_comparable_city_donors || [];
  const donorSummary = donors.map(d =>
    `${d.city} ×${fmt(d.deposit_density_ratio, 2)}`).join(", ");
  const anchor = isTopAnchor
    ? `${(CITY.price_anchor_n_observations || 0).toLocaleString()} deduplicated listings in ` +
      `${(CITY.price_anchor_market_areas || []).join(", ") || "listing-rich top markets"}`
    : isComparable
      ? `scaled from ${donorSummary || "similar anchored metros"} using the target/donor bank-deposits-per-land-cell ratio`
    : (CITY.price_market_baseline_source === "observed_market_calibration"
      ? "calibrated to observed local commercial-lot markets"
      : "predicted from the economic market model");
  const learnedWeights = MAN.weights_model?.status === "trained"
    ? MAN.component_labels.map((label, i) =>
        `${label}: ${fmt((MAN.weights_default[MAN.components[i]] || 0) * 100, 0)}%`).join(" · ")
    : "configured spatial weights";
  card.innerHTML =
    `<div class="model-title">${isTopAnchor ? "Top-market commercial-land anchor" : isComparable ? "Comparable-city commercial estimate" : "Commercial vacant-land baseline"}</div>` +
    `<div class="model-number">${fmtMoney(CITY.price_market_baseline_php_sqm)}/m²</div>` +
    `<div>${anchor}. ${isTopAnchor
      ? `The anchor sits at the ${fmt((CITY.price_anchor_score_quantile || 0.9) * 100, 0)}th score percentile; the index spreads it across cells.`
      : isComparable
      ? "The inferred baseline is the score-weighted average across metro cells; each cell receives its relative accessibility share."
      : "Cell prices follow the area-normalized accessibility score."}</div>` +
    `<div class="model-weights">Spatial weights: ${learnedWeights}. Prices are shown only inside the functional metro.</div>` +
    `<div class="warn">${isTopAnchor
      ? "The tighter interval estimates anchor uncertainty, not the full spread of parcel asking prices."
      : isComparable
      ? "Comparable-city estimates depend on the bank-deposit-per-cell relationship and donor similarity."
      : `Held-out-city MAE: ${fmtMoney(CITY.price_model_mae_php_sqm)}/m².`} ` +
    `Commercial vacant-land asking-price model, not an appraisal.</div>`;
}

function renderCityStats() {
  const nMetro = CITY.n_metro ?? CELLS.features.filter(f => f.properties.mt).length;
  const nConnectors = CITY.n_connectors ?? CELLS.features.filter(f => f.properties.cn).length;
  const stats = state.view === "prices" ? [
    ["Population", fmt(CITY.population, 0)],
    ["Bank deposits", CITY.bank_deposits_php ? "₱" + d3.format(".3s")(CITY.bank_deposits_php) : "—"],
    [CITY.price_market_baseline_source === "top_market_anchor" ? "Local anchors"
      : CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities" ? "Comparable metros"
      : "Training markets",
      (CITY.price_market_baseline_source === "top_market_anchor"
        ? CITY.price_anchor_n_observations
        : CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities"
        ? (CITY.price_comparable_city_donors || []).length
        : CITY.price_model_n_market_observations || 0).toLocaleString()],
    [CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities"
      ? "Deposits / land cell" : "Held-out cities",
      CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities"
        ? (CITY.price_target_bank_deposits_per_land_cell_php
          ? "₱" + d3.format(".3s")(CITY.price_target_bank_deposits_per_land_cell_php) : "—")
        : (CITY.price_model_n_cities || 0).toLocaleString()],
    ["Priced metro cells", (CITY.n_price_cells || 0).toLocaleString()],
  ] : [
    ["Metro cells", nMetro.toLocaleString()],
    ["Metro area", fmt(CITY.metro_km2, 0) + " km²"],
    ["Connectors", nConnectors.toLocaleString()],
    ["Land cells", CITY.n_land.toLocaleString()],
    ["Water cells", (CITY.n_water ?? 0).toLocaleString()],
    ["City area", fmt(CITY.city_km2, 0) + " km²"],
    ["Study area", fmt(CITY.study_km2, 0) + " km²"],
  ];
  $("#cityStats").innerHTML = stats.map(([k, v]) =>
    `<div class="mini-stat"><div class="k">${k}</div><div class="v">${v}</div></div>`
  ).join("");
}

/* ---------------- colour + values ---------------- */
function computeLV() {
  const w = state.weights, sw = w.reduce((a, b) => a + b, 0) || 1;
  let mn = Infinity, mx = -Infinity; const raw = {};
  for (const f of CELLS.features) {
    const p = f.properties; let s = 0;
    for (let i = 0; i < 5; i++) s += w[i] * p["c" + i];
    s /= sw; raw[p.id] = s; if (s < mn) mn = s; if (s > mx) mx = s;
  }
  const d = (mx - mn) || 1;
  LV = {}; for (const id in raw) LV[id] = (raw[id] - mn) / d * 100;
}
const cellVal = p => state.metric === "land_value" ? LV[p.id] : p[metric().prop];

function recolor() {
  computeLV();
  const m = metric();
  const vals = CELLS.features.map(f => cellVal(f.properties)).filter(v => v != null && isFinite(v));
  if (!vals.length) {
    for (const f of CELLS.features) {
      map.setFeatureState({ source: "cells", id: f.properties.id }, { fill: "#e6ebf0" });
    }
    $("#legend").innerHTML = `<div class="cap">${m.label}</div><div class="hint">No values for this city.</div>`;
    renderList(); return;
  }
  let mn = d3.min(vals), mx = d3.max(vals);
  const interp = d3.interpolateYlOrRd;
  if (state.scale === "log") {
    const pos = vals.filter(v => v > 0); mn = Math.max(d3.min(pos), mx / 1000);
    colorScale = d3.scaleSequentialLog(interp).domain(m.reverse ? [mx, mn] : [mn, mx]).clamp(true);
  } else {
    colorScale = d3.scaleSequential(interp).domain(m.reverse ? [mx, mn] : [mn, mx]).clamp(true);
  }
  for (const f of CELLS.features) {
    const v = cellVal(f.properties);
    const col = (v == null || !isFinite(v)) ? "#e6ebf0"
      : colorScale(state.scale === "log" ? Math.max(v, mn) : v);
    map.setFeatureState({ source: "cells", id: f.properties.id }, { fill: col });
  }
  renderLegend(mn, mx);
  renderList();
  if (state.selected) renderDetail();
}

function renderLegend(mn, mx) {
  const m = metric();
  const stops = d3.range(0, 1.001, 1 / 40).map(t => {
    const v = state.scale === "log"
      ? Math.exp(Math.log(mn) + t * (Math.log(mx) - Math.log(mn))) : mn + t * (mx - mn);
    return colorScale(v);
  });
  let ticks = state.scale === "log"
    ? d3.ticks(Math.log10(mn), Math.log10(mx), 4).map(t => 10 ** t)
    : d3.ticks(mn, mx, 4);
  const pos = t => state.scale === "log"
    ? (Math.log(t) - Math.log(mn)) / (Math.log(mx) - Math.log(mn)) * 100 : (t - mn) / (mx - mn) * 100;
  const grad = m.reverse ? stops.slice().reverse() : stops;
  $("#legend").innerHTML =
    `<div class="cap">${m.label}${m.reverse ? " (near = high)" : ""}</div>` +
    `<div class="bar" style="background:linear-gradient(90deg,${grad.join(",")})"></div>` +
    `<div class="ticks">${ticks.map(t => `<span>${fmtMetric(t, m)}</span>`).join("")}</div>`;
}

/* ---------------- ranked list ---------------- */
function renderList() {
  const m = metric();
  $("#listMetricLabel").textContent = m.label.toLowerCase();
  const rows = CELLS.features.map(f => ({ id: f.properties.id, v: cellVal(f.properties), p: f.properties }))
    .filter(r => r.v != null && isFinite(r.v))
    .sort((a, b) => m.reverse ? a.v - b.v : b.v - a.v).slice(0, 12);
  $("#topList").innerHTML = rows.map(r =>
    `<li data-id="${r.id}"><span class="swatch" style="background:${colorScale(r.v)}"></span>` +
    `<span class="nm" title="${r.id}">${r.id.slice(0, 8)}… · ${r.p.pc} POIs</span>` +
    `<span class="vl">${fmtMetric(r.v, m)}</span></li>`).join("");
  $("#topList").querySelectorAll("li").forEach(li => li.onclick = () => select(li.dataset.id, true));
}

/* ---------------- hover / tooltip ---------------- */
function onHover(e) {
  if (!e.features.length) return;
  const id = e.features[0].id;
  if (state.hover && state.hover !== id) map.setFeatureState({ source: "cells", id: state.hover }, { hover: false });
  state.hover = id;
  map.setFeatureState({ source: "cells", id }, { hover: true });
  map.getCanvas().style.cursor = "pointer";
  const p = e.features[0].properties, t = $("#tooltip");
  const activeValue = cellVal(p);
  const priceLine = state.view === "prices" && p.ppsm != null
    ? `<div class="t-name">${fmtMoney(p.ppsm)}/m² estimated</div>`
    : `<div class="t-name">Relative value ${fmt(LV[id], 0)}/100</div>`;
  t.innerHTML =
    priceLine +
    (activeValue != null && isFinite(activeValue)
      ? `<div class="t-val" style="font-size:12px">${metric().label}: ${fmtMetric(activeValue)}</div>`
      : `<div class="t-val" style="font-size:12px">Commercial price unavailable</div>`) +
    `<div class="t-sub">${fmt(p.dcbd, 1)} km to downtown · ${p.pc} POIs</div>` +
    `<div class="t-sub t-muted">${cellStatus(p)}</div>`;
  t.style.left = e.point.x + "px"; t.style.top = e.point.y + "px";
  t.classList.remove("hidden");
}
function clearHover() {
  if (state.hover) map.setFeatureState({ source: "cells", id: state.hover }, { hover: false });
  state.hover = null; map.getCanvas().style.cursor = ""; $("#tooltip").classList.add("hidden");
}

/* ---------------- selection + detail ---------------- */
function select(id, fly) {
  if (state.selected) map.setFeatureState({ source: "cells", id: state.selected }, { selected: false });
  state.selected = id;
  map.setFeatureState({ source: "cells", id }, { selected: true });
  if (fly) {
    const f = CELLS.features.find(x => x.properties.id === id);
    if (f) map.flyTo({ center: centroid(f), zoom: Math.max(map.getZoom(), 12.5), duration: 500 });
  }
  renderDetail();
}
function clearSelection() {
  if (state.selected) map.setFeatureState({ source: "cells", id: state.selected }, { selected: false });
  state.selected = null; $("#detail").classList.add("hidden");
}
function renderDetail() {
  const f = CELLS.features.find(x => x.properties.id === state.selected); if (!f) return;
  const p = f.properties;
  $("#detailHead").innerHTML =
    `<div class="d-name">Cell ${p.id.slice(0, 10)}…</div>` +
    `<div class="d-loc">${fmt(p.dcbd, 2)} km from downtown · ` +
    `<span class="${p.mt ? "up" : "down"}">${cellStatus(p)}</span></div>`;
  const spatialStats =
    stat("Relative value", fmt(LV[p.id], 0) + " <small>/100</small>") +
    stat("Value share", fmt((p.rvs || 0) * 100, 3) + "<small>%</small>") +
    stat("Establishments", fmt(p.ea, 1)) +
    stat("POIs in cell", p.pc) +
    stat("Road density", fmt(p.rdk, 2) + " <small>km</small>");
  $("#detailStats").innerHTML = state.view === "prices"
    ? (p.ppsm != null ? stat("Estimated price", fmtMoney(p.ppsm) + " <small>/m²</small>") : "") +
      (p.plo != null && p.phi != null ? stat(
        CITY.price_market_baseline_source === "top_market_anchor" ? "Anchor-based range"
          : CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities" ? "Comparable-city range"
          : "Indicative range",
        fmtMoney(p.plo) + "–" + fmtMoney(p.phi)) : "") +
      spatialStats
    : spatialStats;
  $("#detailFoot").textContent = state.view === "prices"
    ? (CITY.price_model_status !== "trained"
      ? "Commercial price is withheld because this city lacks a supported metro footprint or matched bank-deposit evidence."
      : CITY.price_market_baseline_source === "top_market_anchor"
      ? "A local commercial-market median anchors the price curve; learned positive spatial weights supply the metro-only multiplier."
      : CITY.price_market_baseline_source === "deposit_per_cell_comparable_cities"
      ? "Similar anchored cities set the baseline through bank deposits per land cell; the normalized score allocates it across metro cells."
      : "The ML model estimates the city baseline; the chart explains the normalized spatial multiplier.")
    : "The chart explains the relative accessibility score used by the metro analysis.";
  $("#detail").classList.remove("hidden");
  drawCompChart(p);
}
const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
const cellStatus = p => p.cn ? "connector cell" : (p.mt ? "in metro" : "outside metro");

function drawCompChart(p) {
  const w = state.weights, sw = w.reduce((a, b) => a + b, 0) || 1;
  const contrib = w.map((wi, i) => wi * p["c" + i] / sw * 100);
  if (chart) chart.destroy();
  chart = new Chart($("#compChart"), {
    type: "bar",
    data: {
      labels: MAN.component_labels,
      datasets: [{ data: contrib, backgroundColor: "#1a73e8", borderRadius: 3 }],
    },
    options: {
      indexAxis: "y", maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => "+" + c.parsed.x.toFixed(1) + " pts" } } },
      scales: {
        x: { ticks: { font: { size: 9 } }, grid: { color: "#eef1f4" }, title: { display: true, text: "contribution to land value", font: { size: 9 } } },
        y: { ticks: { font: { size: 10 } }, grid: { display: false } },
      },
    },
  });
}

/* ---------------- geometry helpers ---------------- */
function centroid(f) {
  let x = 0, y = 0, n = 0;
  const ring = f.geometry.coordinates[0];
  for (const c of ring) { x += c[0]; y += c[1]; n++; }
  return [x / n, y / n];
}

function geojsonBounds(fc) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const visit = coords => {
    if (!Array.isArray(coords)) return;
    if (typeof coords[0] === "number" && typeof coords[1] === "number") {
      minX = Math.min(minX, coords[0]); minY = Math.min(minY, coords[1]);
      maxX = Math.max(maxX, coords[0]); maxY = Math.max(maxY, coords[1]);
      return;
    }
    for (const c of coords) visit(c);
  };
  for (const f of fc?.features || []) visit(f.geometry?.coordinates);
  return isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null;
}

/* ---------------- controls ---------------- */
function wireControls() {
  $("#citySelect").addEventListener("change", e => loadCity(e.target.value));
  $("#viewTabs").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    activateView(b.dataset.view);
  });

  $("#weights").addEventListener("input", e => {
    const i = +e.target.dataset.i; state.weights[i] = +e.target.value;
    $("#wval" + i).textContent = (+e.target.value).toFixed(2);
    recolor();
  });
  $("#resetWeights").addEventListener("click", () => {
    state.weights = MAN.components.map(c => MAN.weights_default[c]);
    state.weights.forEach((v, i) => {
      $(`#weights input[data-i="${i}"]`).value = v; $("#wval" + i).textContent = v.toFixed(2);
    });
    recolor();
  });

  $("#tglMetro").addEventListener("change", e =>
    { state.showMetro = e.target.checked; applyLayerState(); });
  $("#tglConnectors").addEventListener("change", e =>
    { state.showConnectors = e.target.checked; applyLayerState(); });
  $("#tglWater").addEventListener("change", async e => {
    state.showWater = e.target.checked;
    if (state.showWater) await ensureWaterLoaded();
    applyLayerState();
  });
  $("#tglMetroOnly").addEventListener("change", e => {
    state.metroOnly = e.target.checked;
    applyLayerState();
  });
  $("#tglPois").addEventListener("change", async e => {
    state.showPois = e.target.checked;
    if (state.showPois) await ensurePoisLoaded();
    applyLayerState();
  });

  $("#detailClose").addEventListener("click", clearSelection);

  $("#addCityBtn").addEventListener("click", () => generateCity($("#cityInput").value, $("#osmIdInput").value));
  $("#cityInput").addEventListener("keydown", e => { if (e.key === "Enter") generateCity($("#cityInput").value, $("#osmIdInput").value); });
  $("#osmIdInput").addEventListener("keydown", e => { if (e.key === "Enter") generateCity($("#cityInput").value, $("#osmIdInput").value); });
}

async function ensureWaterLoaded() {
  if (WATER || !CITY?.water) return;
  WATER = await fetch("data/" + CITY.water).then(r => r.json()).catch(() => emptyFC());
  map.getSource("water").setData(WATER);
}

async function ensurePoisLoaded() {
  if (POIS || !CITY?.pois) return;
  POIS = await fetch("data/" + CITY.pois).then(r => r.json()).catch(() => emptyFC());
  map.getSource("pois").setData(POIS);
}

function applyLayerState() {
  if (!map?.getLayer("metro-line")) return;
  const metroView = state.view === "metro";
  const cellFilter = !metroView || state.metroOnly ? ["==", ["get", "mt"], 1] : null;
  map.setFilter("cells-fill", cellFilter);
  map.setFilter("cells-line", cellFilter);
  map.setLayoutProperty("metro-line", "visibility", metroView && state.showMetro ? "visible" : "none");
  const connectorVis = metroView && state.showConnectors ? "visible" : "none";
  map.setLayoutProperty("connector-fill", "visibility", connectorVis);
  map.setLayoutProperty("connector-line", "visibility", connectorVis);
  const waterVis = state.showWater ? "visible" : "none";
  map.setLayoutProperty("water-fill", "visibility", waterVis);
  map.setLayoutProperty("water-line", "visibility", waterVis);
  map.setLayoutProperty("pois-pt", "visibility", state.showPois ? "visible" : "none");
}

/* ---------------- generate a new city (backend build over SSE) ---------------- */
function generateCity(place, osmId) {
  place = (place || "").trim();
  osmId = (osmId || "").trim();
  if (!place) return;
  const btn = $("#addCityBtn"), box = $("#buildProgress"), fill = $("#pfill"), msg = $("#pmsg");
  btn.disabled = true;
  box.classList.remove("hidden"); msg.classList.remove("err");
  fill.style.width = "2%"; msg.textContent = "Starting…";

  let finished = false;
  const params = new URLSearchParams({ place });
  if (osmId) params.set("osm_id", osmId);
  const es = new EventSource("/api/build?" + params.toString());
  const finish = () => { finished = true; es.close(); btn.disabled = false; };

  es.onmessage = async ev => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.error) {
      msg.textContent = d.error; msg.classList.add("err"); fill.style.width = "0"; finish(); return;
    }
    if (d.done) {
      fill.style.width = "100%"; msg.textContent = "Loaded " + d.city.name;
      MAN = await fetch("data/manifest.json?t=" + Date.now()).then(r => r.json());
      refreshCitySelect(d.city.slug);
      await loadCity(d.city.slug);
      setTimeout(() => {
        box.classList.add("hidden");
        $("#cityInput").value = "";
        $("#osmIdInput").value = "";
      }, 1000);
      finish(); return;
    }
    if (typeof d.frac === "number") {
      fill.style.width = Math.max(2, d.frac * 100).toFixed(0) + "%";
      msg.textContent = d.msg || "";
    }
  };
  es.onerror = () => {
    if (finished) return;   // normal close after 'done'/'error'
    msg.textContent = "Build server not reachable — run: bash webapp/serve.sh";
    msg.classList.add("err"); finish();
  };
}

function setActive(group, btn) {
  document.querySelectorAll(group + " button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
}
