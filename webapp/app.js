/* Metro-Mapping — metro area & relative land value (MapLibre GL) */
const state = {
  city: null, metric: "land_value", scale: "linear",
  weights: [], selected: null, hover: null,
  metroOnly: false, showMetro: true, showPois: false,
};
let MAN, CITY, CELLS, METRO, POIS, LV = {}, map, colorScale, chart, cbdMarker;

const $ = s => document.querySelector(s);
const CAT_COLORS = {
  mall: "#e6194B", office: "#4363d8", transport: "#3cb44b", school: "#f58231",
  hospital: "#911eb4", government: "#42d4f4", bank: "#bfef45", leisure: "#469990",
};
const fmt = (v, d = 1) => v == null || !isFinite(v) ? "—" : d3.format("," + "." + d + "f")(v);
const metric = () => MAN.metrics.find(m => m.key === state.metric);

init();

async function init() {
  MAN = await fetch("data/manifest.json").then(r => r.json());
  state.weights = MAN.components.map(c => MAN.weights_default[c]);

  refreshCitySelect();

  // metric dropdown + weight sliders
  $("#metricSelect").innerHTML = MAN.metrics.map(m => `<option value="${m.key}">${m.label}</option>`).join("");
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
  const [cells, metro, pois] = await Promise.all([
    fetch("data/" + CITY.cells).then(r => r.json()),
    fetch("data/" + CITY.metro).then(r => r.json()),
    fetch("data/" + CITY.pois).then(r => r.json()),
  ]);
  CELLS = cells; METRO = metro; POIS = pois;
  map.getSource("cells").setData(CELLS);
  map.getSource("metro").setData(METRO);
  map.getSource("pois").setData(POIS);

  if (cbdMarker) cbdMarker.remove();
  const el = document.createElement("div"); el.className = "cbd-marker";
  cbdMarker = new maplibregl.Marker({ element: el }).setLngLat(CITY.center).addTo(map);

  $("#matchNote").textContent =
    `${CITY.n_land.toLocaleString()} land cells · ${CITY.n_water.toLocaleString()} water cells excluded · ` +
    `metro ≈ ${Math.round(CITY.metro_km2).toLocaleString()} km²` +
    (CITY.source === "synthetic" ? " · SYNTHETIC data" : "");

  clearSelection();
  recolor();
  map.fitBounds([[CITY.bbox[0], CITY.bbox[1]], [CITY.bbox[2], CITY.bbox[3]]],
    { padding: 30, duration: 600 });
  $("#loading").classList.add("hidden");
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
    ? [0.01, 0.1, 1, 10, 100, 1000, 10000].filter(t => t >= mn * .9 && t <= mx * 1.1)
    : d3.ticks(mn, mx, 4);
  const pos = t => state.scale === "log"
    ? (Math.log(t) - Math.log(mn)) / (Math.log(mx) - Math.log(mn)) * 100 : (t - mn) / (mx - mn) * 100;
  const grad = m.reverse ? stops.slice().reverse() : stops;
  $("#legend").innerHTML =
    `<div class="cap">${m.label}${m.reverse ? " (near = high)" : ""}</div>` +
    `<div class="bar" style="background:linear-gradient(90deg,${grad.join(",")})"></div>` +
    `<div class="ticks">${ticks.map(t => `<span>${fmt(t, t < 10 ? 1 : 0)}</span>`).join("")}</div>`;
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
    `<span class="vl">${fmt(r.v, m.key === "land_value" ? 0 : 1)}</span></li>`).join("");
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
  t.innerHTML =
    `<div class="t-name">Land value ${fmt(LV[id], 0)}</div>` +
    `<div class="t-val" style="font-size:12px">${metric().label}: ${fmt(cellVal(p), 2)}</div>` +
    `<div class="t-sub">${fmt(p.dcbd, 1)} km to downtown · ${p.pc} POIs</div>` +
    `<div class="t-sub t-muted">${p.mt ? "in metro" : "outside metro"}</div>`;
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
    `<span class="${p.mt ? "up" : "down"}">${p.mt ? "in metro" : "outside metro"}</span></div>`;
  $("#detailStats").innerHTML =
    stat("Land value", fmt(LV[p.id], 0) + " <small>/100</small>") +
    stat("Establishments", fmt(p.ea, 1)) +
    stat("POIs in cell", p.pc) +
    stat("Road density", fmt(p.rdk, 2) + " <small>km</small>");
  $("#detail").classList.remove("hidden");
  drawCompChart(p);
}
const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;

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

/* ---------------- controls ---------------- */
function wireControls() {
  $("#citySelect").addEventListener("change", e => loadCity(e.target.value));
  $("#metricSelect").addEventListener("change", e => { state.metric = e.target.value; recolor(); });

  $("#scaleToggle").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    setActive("#scaleToggle", b); state.scale = b.dataset.scale; recolor();
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
    map.setLayoutProperty("metro-line", "visibility", e.target.checked ? "visible" : "none"));
  $("#tglMetroOnly").addEventListener("change", e => {
    state.metroOnly = e.target.checked;
    const filt = state.metroOnly ? ["==", ["get", "mt"], 1] : null;
    map.setFilter("cells-fill", filt); map.setFilter("cells-line", filt);
  });
  $("#tglPois").addEventListener("change", e =>
    map.setLayoutProperty("pois-pt", "visibility", e.target.checked ? "visible" : "none"));

  $("#detailClose").addEventListener("click", clearSelection);

  $("#addCityBtn").addEventListener("click", () => generateCity($("#cityInput").value));
  $("#cityInput").addEventListener("keydown", e => { if (e.key === "Enter") generateCity($("#cityInput").value); });
}

/* ---------------- generate a new city (backend build over SSE) ---------------- */
function generateCity(place) {
  place = (place || "").trim();
  if (!place) return;
  const btn = $("#addCityBtn"), box = $("#buildProgress"), fill = $("#pfill"), msg = $("#pmsg");
  btn.disabled = true;
  box.classList.remove("hidden"); msg.classList.remove("err");
  fill.style.width = "2%"; msg.textContent = "Starting…";

  let finished = false;
  const es = new EventSource("/api/build?place=" + encodeURIComponent(place));
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
      setTimeout(() => { box.classList.add("hidden"); $("#cityInput").value = ""; }, 1000);
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
