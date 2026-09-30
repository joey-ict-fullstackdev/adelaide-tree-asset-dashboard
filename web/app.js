"use strict";

const DATA_URL = "data/trees.geojson";
const NEIGHBOUR_METRES = 2;
const MAX_POPUP_RECORDS = 6;
const COLOURS = { recorded: "#1f6fb2", missing: "#e4741a" };
const FLAGS = [
  { key: "missing_species_flag", label: "Species not recorded", swatch: true },
  { key: "species_genus_only_flag", label: "Species known to genus only" },
  { key: "missing_inspection_flag", label: "No inspection data" },
  { key: "duplicate_location_flag", label: "Shares a location with another tree" },
  { key: "age_size_anomaly_flag", label: "Age class contradicts trunk size" },
];
// Metres per degree near Adelaide (latitude -34.93). Good to about 0.2% at this scale.
const LAT_METRES = 110990;
const LNG_METRES = 91250;

const fmt = new Intl.NumberFormat("en-AU");
const escapes = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => escapes[c]);
const titleCase = (s) => s.replace(/_/g, " ").toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
const notRecorded = '<span class="none-text">not recorded</span>';
const value = (v, suffix = "") => (v == null ? notRecorded : esc(v) + suffix);

// zoomSnap 0.25 lets the fit use a fractional zoom, so the trees fill the map at any window size.
const map = L.map("map", { preferCanvas: true, zoomSnap: 0.25 }).setView([-34.9285, 138.6007], 14);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);
const treeLayer = L.featureGroup().addTo(map);
let records = [];

// Re-fit to the trees when the window resizes or rotates, unless the user has moved the map.
let userMoved = false;
["pointerdown", "wheel", "keydown"].forEach((type) =>
  map.getContainer().addEventListener(type, () => (userMoved = true), { passive: true }),
);
const fitToTrees = () => map.fitBounds(treeLayer.getBounds(), { padding: [24, 24], animate: false });
map.on("resize", () => {
  if (!userMoved && treeLayer.getLayers().length) fitToTrees();
});

function toRecords(geojson) {
  return geojson.features.map((f) => ({
    p: f.properties,
    lng: f.geometry.coordinates[0],
    lat: f.geometry.coordinates[1],
    near: [],
  }));
}

// Trees within NEIGHBOUR_METRES of each other share a popup, so stacked markers stay reachable.
function linkNeighbours(records) {
  const cell = (v) => Math.floor(v / NEIGHBOUR_METRES);
  const grid = new Map();
  records.forEach((r, i) => {
    r.x = r.lng * LNG_METRES;
    r.y = r.lat * LAT_METRES;
    const key = `${cell(r.x)},${cell(r.y)}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(i);
  });
  records.forEach((r, i) => {
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (const j of grid.get(`${cell(r.x) + dx},${cell(r.y) + dy}`) ?? []) {
          if (j !== i && Math.hypot(r.x - records[j].x, r.y - records[j].y) <= NEIGHBOUR_METRES) {
            r.near.push(j);
          }
        }
      }
    }
  });
}

function recordHtml(r) {
  const p = r.p;
  const title = p.species
    ? `<span class="sp">${esc(p.species)}</span>`
    : '<span class="none-text">Species not recorded</span>';
  const common = p.common_name ? `<p class="rec-sub">${esc(p.common_name)}</p>` : "";
  const flags = FLAGS.filter((f) => p[f.key]);
  return `<div class="rec">
    <h3 class="rec-title">${title}</h3>${common}
    <dl class="facts">
      <dt>Asset</dt><dd>${esc(p.asset_id)}</dd>
      <dt>Road</dt><dd>${value(p.road && titleCase(p.road))}</dd>
      <dt>Suburb</dt><dd>${value(p.suburb_name && titleCase(p.suburb_name))}</dd>
      <dt>Age</dt><dd>${value(p.age_class)}</dd>
      <dt>Height</dt><dd>${value(p.height, " m")}</dd>
      <dt>Trunk</dt><dd>${value(p.circumference)}</dd>
      <dt>Vigour</dt><dd>${value(p.vigour)}</dd>
      <dt>Structure</dt><dd>${value(p.structure)}</dd>
      <dt>Completeness</dt><dd>${Math.round(p.data_completeness_score * 100)}%</dd>
    </dl>
    ${flags.length ? `<ul class="flags">${flags.map((f) => `<li>${f.label}</li>`).join("")}</ul>` : ""}
  </div>`;
}

function popupHtml(r, records) {
  const group = [r, ...r.near.map((j) => records[j]).filter((n) => n.visible)];
  const shown = group.slice(0, MAX_POPUP_RECORDS);
  const head = group.length > 1 ? `<p class="stack">${group.length} records within ${NEIGHBOUR_METRES} m</p>` : "";
  const more = group.length > shown.length ? `<p class="more">+${group.length - shown.length} more</p>` : "";
  return head + shown.map(recordHtml).join("") + more;
}

function createMarkers(records) {
  records.forEach((r) => {
    r.marker = L.circleMarker([r.lat, r.lng], {
      radius: 3.5,
      weight: 1,
      color: "#ffffff",
      fillColor: r.p.missing_species_flag ? COLOURS.missing : COLOURS.recorded,
      fillOpacity: 0.9,
    }).bindPopup(() => popupHtml(r, records), { maxWidth: 300, maxHeight: 340 });
  });
}

// Shows markers for `list` only. Missing-species trees are drawn last so the orange markers stay on top.
function showRecords(list) {
  treeLayer.clearLayers();
  records.forEach((r) => (r.visible = false));
  [...list]
    .sort((a, b) => a.p.missing_species_flag - b.p.missing_species_flag)
    .forEach((r) => {
      r.visible = true;
      treeLayer.addLayer(r.marker);
    });
}

function drawTrees(records) {
  createMarkers(records);
  showRecords(records);
  fitToTrees();
}

function countBy(records, pick) {
  const counts = new Map();
  for (const r of records) {
    const key = pick(r.p);
    if (key != null) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function barList(el, rows, labelHtml) {
  const max = rows[0]?.[1] ?? 1;
  el.innerHTML = rows
    .map(
      ([label, n]) => `<li>
        <div class="row"><span>${labelHtml(label)}</span><span class="n">${fmt.format(n)}</span></div>
        <div class="bar" style="--w:${((n / max) * 100).toFixed(1)}%"></div>
      </li>`,
    )
    .join("");
}

function percent(n, total) {
  if (!total) return "0.0%";
  const pct = (n / total) * 100;
  return n > 0 && pct < 0.1 ? "<0.1%" : `${pct.toFixed(1)}%`;
}

// Takes any list of records (all trees or a search's matches). `all` is the size of the full data set.
function render(records, all = records.length) {
  const total = records.length;
  document.getElementById("lede").textContent =
    total === all
      ? `${fmt.format(total)} street trees from City of Adelaide open data, checked for missing and inconsistent records.`
      : `Showing ${fmt.format(total)} of ${fmt.format(all)} street trees that match your search.`;
  barList(document.getElementById("suburbs"), countBy(records, (p) => p.suburb_name), (l) => esc(titleCase(l)));
  barList(
    document.getElementById("species"),
    countBy(records, (p) => p.species).slice(0, 10),
    (l) => `<span class="sp">${esc(l)}</span>`,
  );
  document.getElementById("quality").innerHTML = FLAGS.map((f) => {
    const n = records.filter((r) => r.p[f.key]).length;
    const mark = f.swatch ? '<span class="dot dot-missing"></span>' : '<span class="none"></span>';
    return `<li>${mark}<span>${f.label}</span><span class="n">${fmt.format(n)}<span class="pct">${percent(n, total)}</span></span></li>`;
  }).join("");
}

function showStatus(message) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.hidden = false;
}

const hideStatus = () => (document.getElementById("status").hidden = true);

// Search: the server turns a question into a filter (web/api/query.py). The filter is applied here.
const LIST_KEYS = { age_class: "age", vigour: "vigour", structure: "structure", circumference: "trunk" };
const searchForm = document.getElementById("search");
const searchInput = document.getElementById("q");
const searchButton = searchForm.querySelector("button");
const summary = document.getElementById("summary");

function matches(p, f) {
  if (f.suburb_name && p.suburb_name !== f.suburb_name) return false;
  if (f.species_text) {
    const text = String(f.species_text).toLowerCase();
    if (![p.species, p.common_name].some((s) => (s ?? "").toLowerCase().includes(text))) return false;
  }
  for (const key of Object.keys(LIST_KEYS)) {
    if (Array.isArray(f[key]) && !f[key].includes(p[key])) return false;
  }
  if (typeof f.height_min === "number" && !(p.height != null && p.height >= f.height_min)) return false;
  if (typeof f.height_max === "number" && !(p.height != null && p.height <= f.height_max)) return false;
  if (typeof f.completeness_max === "number" && !(p.data_completeness_score <= f.completeness_max)) return false;
  return FLAGS.every(({ key }) => typeof f[key] !== "boolean" || p[key] === f[key]);
}

// Plain-English list of the conditions matches() will apply, so the user can check what was understood.
function describe(f) {
  const parts = [];
  if (f.suburb_name) parts.push(titleCase(f.suburb_name));
  if (f.species_text) parts.push(`species or common name contains "${f.species_text}"`);
  for (const [key, label] of Object.entries(LIST_KEYS)) {
    if (Array.isArray(f[key])) parts.push(`${label} ${f[key].join(" or ")}`);
  }
  if (typeof f.height_min === "number") parts.push(`height ${f.height_min} m or more`);
  if (typeof f.height_max === "number") parts.push(`height ${f.height_max} m or less`);
  if (typeof f.completeness_max === "number") parts.push(`completeness ${Math.round(f.completeness_max * 100)}% or less`);
  for (const { key, label } of FLAGS) {
    if (typeof f[key] === "boolean") parts.push(f[key] ? label.toLowerCase() : `not: ${label.toLowerCase()}`);
  }
  return parts.join("; ");
}

function applyFilter(filter) {
  const active = records.filter((r) => matches(r.p, filter));
  showRecords(active);
  render(active, records.length);
  if (active.length) {
    map.fitBounds(L.latLngBounds(active.map((r) => [r.lat, r.lng])), { padding: [24, 24], maxZoom: 18, animate: false });
  }
  userMoved = false;
  return active.length;
}

function showResult(filter, unsupported) {
  const understood = describe(filter);
  const note = document.getElementById("summary-note");
  note.textContent = unsupported ?? "";
  note.hidden = !unsupported;
  document.getElementById("summary-text").textContent = understood
    ? `Understood as: ${understood}. ${fmt.format(applyFilter(filter))} of ${fmt.format(records.length)} trees.`
    : "No search applied. The map is unchanged.";
  summary.hidden = false;
}

function clearSearch() {
  applyFilter({});
  summary.hidden = true;
  searchInput.value = "";
  hideStatus();
}

function setBusy(busy) {
  searchButton.disabled = busy;
  searchButton.textContent = busy ? "Searching…" : "Search";
}

async function search(question) {
  hideStatus();
  setBusy(true);
  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `Search failed (HTTP ${res.status}).`);
    showResult(body.filter ?? {}, body.unsupported);
  } catch (err) {
    showStatus(err.message);
  } finally {
    setBusy(false);
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = searchInput.value.trim();
  if (question && records.length) search(question);
});
document.getElementById("clear").addEventListener("click", clearSearch);

async function main() {
  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    records = toRecords(await res.json());
    linkNeighbours(records);
    drawTrees(records);
    render(records);
  } catch (err) {
    document.getElementById("lede").textContent = "Tree data unavailable.";
    showStatus(`Could not load the tree data (${err.message}). Serve the web folder over HTTP and check that data/trees.geojson exists.`);
    console.error(err);
  }
}

main();
