"use strict";
/**
 * SmartHydro control room dashboard.
 *
 * Served same-origin by the backend at /dashboard/, so every request below
 * targets the unprefixed control-room endpoints (fetch('/zones'), never
 * '/api/v1/...'). There is no WebSocket on this backend: everything that
 * changes over time is refreshed with setInterval polling.
 *
 * Data model reminders (see backend/app/features/zones/models.py):
 *  - A "Zone" is one physical SECTOR: department_number 1-5, sector_number
 *    1-2, at most 2 sectors per department, a single plant_species per
 *    production sector (department 5 has none — quarantine is a per-plant
 *    flag, not a zone attribute).
 *  - operational_state is one of exactly: Nominal, Degraded, EmergencyLockdown.
 *  - Every sector controls exactly 6 variables, each with one Strategy among
 *    exactly: Threshold, PID, Predictive.
 *  - Setpoint/Min/Max always come from the active recipe's active phase
 *    target and are read-only in the UI; only Strategy is editable.
 */

/* ------------------------------------------------------------------ */
/* Constants                                                          */
/* ------------------------------------------------------------------ */

const ZONES_POLL_MS = 6000;
const MODAL_POLL_MS = 4000;
const HOME_EXTRAS_MIN_INTERVAL_MS = 5000; // throttle for alerts/quarantine refetch
const RECIPES_POLL_MS = 20000;
const STATUS_POLL_MS = 20000;
const CHART_POLL_MS = 15000;
const COMMAND_POLL_MS = 1500;
const COMMAND_TIMEOUT_MS = 20000;

const VARIABLES = [
  { key: "soil_moisture", label: "Umidità del terriccio", unit: "%", sensorField: "soil_moisture_percent", decimals: 1 },
  { key: "light", label: "Luce (PPFD)", unit: "µmol/m²s", sensorField: "light_ppfd_umol_m2_s", decimals: 0 },
  { key: "ph", label: "pH", unit: "pH", sensorField: "ph", decimals: 2 },
  { key: "nitrogen", label: "Azoto (N)", unit: "mg/L", sensorField: "nitrogen_estimate_mg_per_liter", decimals: 1 },
  { key: "phosphorus", label: "Fosforo (P)", unit: "mg/L", sensorField: "phosphorus_estimate_mg_per_liter", decimals: 1 },
  { key: "potassium", label: "Potassio (K)", unit: "mg/L", sensorField: "potassium_estimate_mg_per_liter", decimals: 1 },
];
const VARIABLES_BY_KEY = Object.fromEntries(VARIABLES.map((v) => [v.key, v]));

const STRATEGIES = ["Threshold", "PID", "Predictive"];

const OP_META = {
  Nominal: { color: "#1f7a51" },
  Degraded: { color: "#c9803f" },
  EmergencyLockdown: { color: "#c15a4a" },
};

const DEPT_META = {
  1: { tint: "#e7f3ec", border: "#cfe6d9", accent: "#3f7a60" },
  2: { tint: "#e4f0f0", border: "#c8e0e0", accent: "#3a6d6d" },
  3: { tint: "#f2f5e6", border: "#e0e7cb", accent: "#6c7a45" },
  4: { tint: "#eef2e4", border: "#d9e2c6", accent: "#6b7550" },
  5: { tint: "#faf3ee", border: "#e6d7cd", accent: "#a06a37" },
};
// Only used when a department currently has zero registered zones and there
// is no live department_name to read from the API response.
const DEPT_FALLBACK_NAMES = {
  1: "Piante Tropicali e da Fogliame",
  2: "Piante da Fiore",
  3: "Piante Grasse e Succulente",
  4: "Piante da Frutto e Ortaggi",
  5: "Quarantena",
};

const ADMIN_LABELS = { active: "In produzione", inactive: "Non attivo", maintenance: "Manutenzione" };
const LIFECYCLE_LABELS = { Idle: "Inattivo", Running: "In esecuzione", Paused: "In pausa", Error: "Errore" };
const SUBSTRATE_LABELS = {
  "aerated-universal": "Universale aerato",
  "draining": "Drenante",
  "organic-retentive": "Organico ritentivo",
};

/* ------------------------------------------------------------------ */
/* Application state                                                  */
/* ------------------------------------------------------------------ */

const STATE = {
  view: "home",

  zones: [],

  recipes: [],
  recipesById: {},
  recipeQuery: "",
  currentRecipe: null,
  recipeDetailPhaseIdx: 0,

  controlFilters: { dept: "all", species: "all", strategy: "all" },

  alerts: [],
  quarantine: {},
  homeExtrasLoaded: false,
  homeExtrasLastRun: 0,

  modalZoneId: null,
  modalZone: null,
  modalTab: "summary",
  modalTelemetry: null,
  modalActuators: null,
  modalRecipe: null,
  modalRecipeId: null,
  modalChartVariable: "soil_moisture",
  modalChartData: [],

  strategyDrafts: {},
  strategyStatus: {},
  recipeHintVisible: false,
  phasePending: false,

  adhocIntervals: [],
};

/* ------------------------------------------------------------------ */
/* API layer — same-origin, no /api/v1 prefix                         */
/* ------------------------------------------------------------------ */

async function apiRequest(method, path, { params, body } = {}) {
  let url = path;
  if (params) {
    const usp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) usp.set(k, v);
    });
    const qs = usp.toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (e) {
      data = text;
    }
  }
  if (!res.ok) {
    let detail = res.statusText;
    if (data && typeof data === "object" && "detail" in data) {
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } else if (typeof data === "string" && data) {
      detail = data;
    }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}
const apiGet = (path, params) => apiRequest("GET", path, { params });
const apiPost = (path, body) => apiRequest("POST", path, { body });
const apiPatch = (path, body) => apiRequest("PATCH", path, { body });

/* ------------------------------------------------------------------ */
/* Small utilities                                                    */
/* ------------------------------------------------------------------ */

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
const escapeAttr = escapeHtml;

function fmtNum(value, decimals) {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  return Number(value).toFixed(decimals === undefined ? 1 : decimals);
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("it-IT");
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function uniqueSorted(values) {
  return Array.from(new Set(values)).sort((a, b) => String(a).localeCompare(String(b), "it"));
}

function zoneLabel(zone) {
  return `R${zone.department_number}·S${zone.sector_number}`;
}

function groupZonesByDepartment(zones) {
  const map = {};
  zones.forEach((z) => {
    (map[z.department_number] = map[z.department_number] || []).push(z);
  });
  Object.values(map).forEach((list) => list.sort((a, b) => a.sector_number - b.sector_number));
  return map;
}

function setPageTitle(title, subtitle) {
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle;
}

let toastTimer = null;
function showToast(message, kind) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (kind ? " " + kind : "");
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 5000);
}

function isFocusedInside(id) {
  const el = document.getElementById(id);
  return !!(el && document.activeElement && el.contains(document.activeElement) && el !== document.activeElement);
}

/* Poll manager: every entry is a named setInterval, replaced (not stacked)
   when re-armed, and cleared when a view/modal is left. */
const _intervals = {};
function setPoll(name, fn, ms) {
  clearPoll(name);
  fn();
  _intervals[name] = setInterval(fn, ms);
}
function clearPoll(name) {
  if (_intervals[name]) {
    clearInterval(_intervals[name]);
    delete _intervals[name];
  }
}

function updateZoneInState(zone) {
  const idx = STATE.zones.findIndex((z) => z.id === zone.id);
  if (idx >= 0) STATE.zones[idx] = zone;
  else STATE.zones.push(zone);
  if (STATE.modalZoneId === zone.id) STATE.modalZone = zone;
}

function findPhaseTarget(recipe, phaseName, variableKey) {
  if (!recipe || !phaseName || !Array.isArray(recipe.phases)) return null;
  const phase = recipe.phases.find((p) => p.name === phaseName);
  if (!phase) return null;
  return phase.targets.find((t) => t.variable === variableKey) || null;
}

function bandCalc(value, min, max) {
  if (value === null || value === undefined || !isFinite(value) || min === null || max === null) {
    return { hasBand: false, hasValue: value !== null && value !== undefined && isFinite(value), color: "var(--ink-faint)", pct: 0, bandLeft: 0, bandWidth: 0, flag: "n/d" };
  }
  const span = max - min || 1;
  const lo = min - span * 0.6;
  const hi = max + span * 0.6;
  const pos = (x) => Math.max(0, Math.min(100, ((x - lo) / (hi - lo)) * 100));
  const inBand = value >= min && value <= max;
  return {
    hasBand: true,
    hasValue: true,
    color: inBand ? "var(--nominal)" : "var(--warn)",
    pct: pos(value),
    bandLeft: pos(min),
    bandWidth: pos(max) - pos(min),
    flag: inBand ? "in banda" : "fuori banda",
  };
}

/* ------------------------------------------------------------------ */
/* Strategy-change command payloads                                   */
/*                                                                    */
/* The backend accepts an untyped JSON payload for ChangeStrategy     */
/* (see features/commands/models.py); the Edge Controller is the one  */
/* that interprets `parameters` against ThresholdConfig / PidConfig / */
/* PredictiveConfig. This dashboard only exposes a Strategy choice per */
/* variable (per the control-table spec), so it derives reasonable    */
/* default parameters from the recipe's phase target — mirroring the  */
/* defaults backend/app/features/recipes/catalog.py seeds recipes     */
/* with. Fine-tuning individual gains is out of scope for this UI.    */
/* ------------------------------------------------------------------ */

function buildStrategyParameters(strategy, target) {
  const setpoint = target ? target.setpoint : 0;
  const min = target ? target.allowed_range.minimum : 0;
  const max = target ? target.allowed_range.maximum : Math.max(setpoint + 1, 1);
  if (strategy === "Threshold") {
    return { lower_threshold: min, upper_threshold: max, direction: "increases", active_command: 1.0, inactive_command: 0.0, bidirectional: false };
  }
  if (strategy === "PID") {
    return { setpoint, proportional_gain: 1.0, integral_gain: 0.00001, derivative_gain: 0.0, command_minimum: -0.5, command_maximum: 0.5, direction: "increases" };
  }
  return {
    setpoint, prediction_horizon_steps: 4.0, response_gain: 0.05, neutral_command: 0.0,
    command_minimum: 0.0, command_maximum: Math.max(1.0, max), direction: "increases",
    water_dilution_gain: 1.0, cumulative_dose_gain: 0.02, substrate_gain: 2.0,
  };
}

/* ------------------------------------------------------------------ */
/* System status pill                                                 */
/* ------------------------------------------------------------------ */

async function tickSystemStatus() {
  const dot = document.getElementById("system-status-dot");
  const nameEl = document.getElementById("system-status-name");
  const detailEl = document.getElementById("system-status-detail");
  try {
    const [root, health] = await Promise.all([apiGet("/"), apiGet("/health")]);
    dot.className = "status-dot ok";
    nameEl.textContent = `${root.name} v${root.version}`;
    detailEl.textContent = health.status === "healthy" ? "operativo" : health.status;
  } catch (e) {
    dot.className = "status-dot down";
    nameEl.textContent = "SmartHydro Backend";
    detailEl.textContent = "non raggiungibile";
  }
}

/* ------------------------------------------------------------------ */
/* Home view: /zones grouped into 5 departments x <=2 sectors          */
/* ------------------------------------------------------------------ */

async function tickZones() {
  try {
    const zones = await apiGet("/zones");
    STATE.zones = zones;
    if (STATE.modalZoneId) {
      const z = zones.find((zz) => zz.id === STATE.modalZoneId);
      if (z) STATE.modalZone = z;
    }
    if (STATE.view === "home") renderHome();
    else if (STATE.view === "control") {
      if (!isFocusedInside("view-control")) renderControl();
    }
  } catch (e) {
    console.error("failed to refresh zones", e);
  }
}

function renderHome() {
  const zones = STATE.zones;
  const byDept = groupZonesByDepartment(zones);
  const total = zones.length;
  const online = zones.filter((z) => z.status === "online").length;
  const degraded = zones.filter((z) => z.operational_state === "Degraded").length;
  const emergency = zones.filter((z) => z.operational_state === "EmergencyLockdown").length;

  setPageTitle("Reparti della serra", `${total} settori registrati (massimo 10: 5 reparti × 2 settori) · ${online} online`);

  const deptCards = [1, 2, 3, 4, 5].map((n) => renderDeptCard(n, byDept[n] || [])).join("");

  document.getElementById("view-home").innerHTML = `
    <div class="home-layout">
      <div class="dept-grid">${deptCards}</div>
      <aside class="home-aside">
        <div class="side-card">
          <div class="side-card-title">Stato impianto</div>
          <div class="status-line"><span class="swatch" style="background:var(--nominal)"></span><span class="label">Settori online</span><b>${online}/${total}</b></div>
          <div class="status-line"><span class="swatch" style="background:var(--warn)"></span><span class="label">Settori Degraded</span><b>${degraded}</b></div>
          <div class="status-line"><span class="swatch" style="background:var(--danger)"></span><span class="label">EmergencyLockdown</span><b>${emergency}</b></div>
          <div class="status-sep"></div>
          <div class="status-hint">Ogni settore controlla 6 variabili con strategia Threshold, PID o Predictive. Setpoint e bande arrivano dalla fase attiva della ricetta assegnata.</div>
        </div>
        <div class="side-card">
          <div class="side-card-title">Allarmi recenti</div>
          <div id="alerts-list"></div>
        </div>
      </aside>
    </div>
  `;

  // Repaint immediately from whatever is already cached (avoids a
  // "Caricamento…" flash every time the user revisits this view), then
  // kick off a throttled network refresh.
  renderAlertsPanel();
  renderQuarantineBox();
  maybeFetchHomeExtras();
}

function renderDeptCard(n, zones) {
  const meta = DEPT_META[n];
  const name = (zones[0] && zones[0].department_name) || DEPT_FALLBACK_NAMES[n];
  const slots = [1, 2].map((sn) => {
    const z = zones.find((zz) => zz.sector_number === sn);
    return z ? renderSectorRow(z) : `<div class="sector-slot-empty">Settore ${sn} non ancora registrato</div>`;
  }).join("");
  const quarantineHtml = n === 5 ? `<div class="quarantine-box" id="quarantine-box"></div>` : "";
  return `
    <div class="dept-card" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
      <div class="dept-card-head">
        <span class="dept-code">REPARTO ${n}</span>
        <span class="dept-count">${zones.length} settor${zones.length === 1 ? "e" : "i"}</span>
      </div>
      <div class="dept-title">${escapeHtml(name)}</div>
      ${quarantineHtml}
      <div class="sector-list">${slots}</div>
    </div>
  `;
}

function renderSectorRow(z) {
  const online = z.status === "online";
  const species = z.plant_species || (z.department_number === 5 ? "Zona mista (quarantena)" : "—");
  return `
    <div class="sector-row" data-action="open-zone" data-zone-id="${escapeAttr(z.id)}">
      <span class="conn-dot ${online ? "online" : "offline"}"></span>
      <div class="sector-row-body">
        <div class="sector-row-top">
          <span class="sector-num">SETTORE ${z.sector_number}</span>
          <span class="sector-conn" style="color:${online ? "#2f9e6b" : "#c15a4a"}">${online ? "ONLINE" : "OFFLINE"}</span>
        </div>
        <div class="sector-row-species">${escapeHtml(species)}</div>
        <div class="sector-row-tags">
          <span class="tag-phase">${escapeHtml(z.current_phase || "nessuna fase attiva")}</span>
          <span class="pill op-${z.operational_state}">${z.operational_state}</span>
        </div>
      </div>
      <span class="sector-row-arrow">→</span>
    </div>
  `;
}

async function maybeFetchHomeExtras() {
  const now = Date.now();
  if (now - STATE.homeExtrasLastRun < HOME_EXTRAS_MIN_INTERVAL_MS) return;
  STATE.homeExtrasLastRun = now;
  try {
    const [alerts, quarantine] = await Promise.all([loadAlerts(STATE.zones), loadQuarantineStats()]);
    STATE.alerts = alerts;
    STATE.quarantine = quarantine;
  } catch (e) {
    console.error("failed to refresh home extras", e);
  }
  STATE.homeExtrasLoaded = true;
  renderAlertsPanel();
  renderQuarantineBox();
}

async function loadAlerts(zones) {
  const flagged = zones.filter((z) => z.operational_state !== "Nominal");
  const results = await Promise.all(
    flagged.map(async (z) => {
      try {
        const events = await apiGet(`/zones/${encodeURIComponent(z.id)}/events`, { limit: 1 });
        return { zone: z, event: events && events.length ? events[events.length - 1] : null };
      } catch (e) {
        return { zone: z, event: null };
      }
    })
  );
  results.sort((a, b) => {
    const ta = a.event ? new Date(a.event.recorded_at).getTime() : 0;
    const tb = b.event ? new Date(b.event.recorded_at).getTime() : 0;
    return tb - ta;
  });
  return results.slice(0, 6);
}

async function loadQuarantineStats() {
  try {
    const plants = await apiGet("/plants", { is_quarantined: true, limit: 1000 });
    if (!plants.length) return { count: 0, lastEntry: null };
    let last = null;
    plants.forEach((p) => {
      if (p.quarantined_at && (!last || new Date(p.quarantined_at) > new Date(last))) last = p.quarantined_at;
    });
    return { count: plants.length, lastEntry: last };
  } catch (e) {
    return { count: null, lastEntry: null };
  }
}

function renderAlertsPanel() {
  const el = document.getElementById("alerts-list");
  if (!el) return;
  if (!STATE.homeExtrasLoaded) {
    el.innerHTML = '<div class="empty-note">Caricamento…</div>';
    return;
  }
  const alerts = STATE.alerts || [];
  if (!alerts.length) {
    el.innerHTML = '<div class="empty-note">Nessun allarme attivo: tutti i settori registrati sono Nominal.</div>';
    return;
  }
  el.innerHTML = alerts.map((a) => {
    const op = OP_META[a.zone.operational_state] || OP_META.Nominal;
    const title = `${zoneLabel(a.zone)} in ${a.zone.operational_state}`;
    const detail = a.event
      ? `${fmtDateTime(a.event.recorded_at)} · ${escapeHtml(a.event.event_type)}`
      : "nessun evento registrato per questo settore";
    return `
      <div class="alert-item">
        <span class="alert-bar" style="background:${op.color}"></span>
        <div><div class="alert-title">${escapeHtml(title)}</div><div class="alert-detail">${detail}</div></div>
      </div>
    `;
  }).join("");
}

function renderQuarantineBox() {
  const el = document.getElementById("quarantine-box");
  if (!el) return;
  if (!STATE.homeExtrasLoaded) {
    el.innerHTML = '<div class="quarantine-row"><span>Piante in quarantena</span><b>…</b></div>';
    return;
  }
  const q = STATE.quarantine || {};
  el.innerHTML = `
    <div class="quarantine-row"><span>Piante in quarantena</span><b>${q.count === null || q.count === undefined ? "—" : q.count}</b></div>
    <div class="quarantine-row"><span>Ultimo ingresso</span><b>${q.lastEntry ? fmtDate(q.lastEntry) : "—"}</b></div>
  `;
}

/* ------------------------------------------------------------------ */
/* Recipes view                                                       */
/* ------------------------------------------------------------------ */

async function tickRecipes() {
  try {
    const recipes = await apiGet("/recipes");
    STATE.recipes = recipes;
    STATE.recipesById = Object.fromEntries(recipes.map((r) => [r.id, r]));
    if (isFocusedInside("view-recipes")) updateRecipeGridOnly();
    else renderRecipes();
  } catch (e) {
    console.error("failed to refresh recipes", e);
    document.getElementById("view-recipes").innerHTML = `<div class="empty-note">Impossibile caricare le ricette: ${escapeHtml(e.message)}</div>`;
  }
}

function filteredRecipes() {
  const q = (STATE.recipeQuery || "").trim().toLowerCase();
  if (!q) return STATE.recipes;
  return STATE.recipes.filter((r) => `${r.id} ${r.plant_type} ${r.department_name || ""}`.toLowerCase().includes(q));
}

function recipeCardsHtml(list) {
  if (!list.length) return '<div class="empty-note">Nessuna ricetta trovata.</div>';
  return list.map((r) => `
    <div class="recipe-card" data-action="open-recipe" data-recipe-id="${escapeAttr(r.id)}">
      <div class="recipe-card-head">
        <span class="recipe-code">${escapeHtml(r.id)}</span>
        <span class="recipe-badge">v${r.version}</span>
      </div>
      <div class="recipe-name">${escapeHtml(r.plant_type)}</div>
      <div class="recipe-sub">${escapeHtml(r.department_name || "Reparto non catalogato")} · substrato ${escapeHtml(SUBSTRATE_LABELS[r.substrate] || r.substrate)}</div>
      <div class="recipe-card-foot">
        <span class="meta">${r.phases.length} fasi</span>
        <span class="recipe-arrow">→</span>
      </div>
    </div>
  `).join("");
}

function renderRecipes() {
  const list = filteredRecipes();
  setPageTitle("Ricette", `${STATE.recipes.length} ricette nel catalogo del backend · consultazione in sola lettura`);
  document.getElementById("view-recipes").innerHTML = `
    <div class="toolbar">
      <input type="search" id="recipe-search" placeholder="Cerca una ricetta per nome, id o reparto" value="${escapeAttr(STATE.recipeQuery || "")}">
      <span class="count" id="recipe-count">${list.length} ${list.length === 1 ? "ricetta" : "ricette"}</span>
    </div>
    <div class="recipe-grid" id="recipe-grid">${recipeCardsHtml(list)}</div>
  `;
}

function updateRecipeGridOnly() {
  const list = filteredRecipes();
  const grid = document.getElementById("recipe-grid");
  const count = document.getElementById("recipe-count");
  if (grid) grid.innerHTML = recipeCardsHtml(list);
  if (count) count.textContent = `${list.length} ${list.length === 1 ? "ricetta" : "ricette"}`;
}

function openRecipeDetail(recipeId) {
  switchView("recipe");
  STATE.recipeDetailPhaseIdx = 0;
  const recipe = STATE.recipesById[recipeId];
  if (recipe) {
    STATE.currentRecipe = recipe;
    renderRecipeDetail();
    return;
  }
  document.getElementById("view-recipe").innerHTML = '<div class="empty-note">Caricamento ricetta…</div>';
  apiGet(`/recipes/${encodeURIComponent(recipeId)}`).then((r) => {
    STATE.currentRecipe = r;
    STATE.recipesById[r.id] = r;
    if (STATE.view === "recipe") renderRecipeDetail();
  }).catch((e) => {
    document.getElementById("view-recipe").innerHTML = `<div class="empty-note">Impossibile caricare la ricetta: ${escapeHtml(e.message)}</div>`;
  });
}

function renderRecipeDetail() {
  const r = STATE.currentRecipe;
  const el = document.getElementById("view-recipe");
  if (!r) {
    el.innerHTML = '<div class="empty-note">Ricetta non trovata.</div>';
    return;
  }
  setPageTitle("Variabili ricetta", "Setpoint e banda per le 6 variabili controllate, per fase · sola lettura");

  const idx = Math.min(STATE.recipeDetailPhaseIdx || 0, r.phases.length - 1);
  const phase = r.phases[idx];
  const controllersByVar = Object.fromEntries(r.controllers.map((c) => [c.variable, c]));
  const targetsByVar = Object.fromEntries(phase.targets.map((t) => [t.variable, t]));

  const rows = VARIABLES.map((v) => {
    const t = targetsByVar[v.key];
    const c = controllersByVar[v.key];
    return `
      <div class="data-table-row cols-recipe">
        <div><div class="var-name">${v.label}</div><div class="var-unit">${v.unit}</div></div>
        <div class="readonly-cell">${t ? fmtNum(t.setpoint, v.decimals) : "—"}</div>
        <div class="readonly-cell">${t ? fmtNum(t.allowed_range.minimum, v.decimals) : "—"}</div>
        <div class="readonly-cell">${t ? fmtNum(t.allowed_range.maximum, v.decimals) : "—"}</div>
        <div><span class="tag-phase">${c ? c.selected_strategy : "—"}</span></div>
      </div>
    `;
  }).join("");

  const care = r.care_profile;
  const careHtml = care ? `
    <div class="care-grid">
      <div class="care-item"><div class="k">Luce</div><div class="v">${escapeHtml(care.light)}</div></div>
      <div class="care-item"><div class="k">Irrigazione</div><div class="v">${escapeHtml(care.watering)}</div></div>
      <div class="care-item"><div class="k">Temperatura</div><div class="v">${escapeHtml(care.temperature)}</div></div>
      <div class="care-item"><div class="k">Fertilizzazione</div><div class="v">${escapeHtml(care.fertilization)}</div></div>
    </div>
  ` : '<div class="empty-note">Profilo agronomico non disponibile per questa ricetta.</div>';

  const photoEnd = (phase.photoperiod.start_hour + phase.photoperiod.duration_hours) % 24;

  el.innerHTML = `
    <div class="recipe-detail-head">
      <button type="button" class="btn" data-action="back-to-recipes">← Indietro</button>
      <span class="recipe-badge">${escapeHtml(r.id)}</span>
      <div class="spacer"></div>
      <span class="hint">v${r.version} · sola lettura</span>
    </div>
    <div class="recipe-detail-card">
      <div class="info-grid">
        <div class="info-item"><span class="k">Specie</span><span class="v">${escapeHtml(r.plant_type)}</span></div>
        <div class="info-item"><span class="k">Reparto</span><span class="v">${escapeHtml(r.department_name || "Non catalogata")}</span></div>
        <div class="info-item"><span class="k">Substrato</span><span class="v">${escapeHtml(SUBSTRATE_LABELS[r.substrate] || r.substrate)}</span></div>
        <div class="info-item"><span class="k">Fasi</span><span class="v">${r.phases.length}</span></div>
      </div>
      ${careHtml}
      <div>
        <div class="field-label" style="margin-bottom:8px">Fase</div>
        <div class="phase-tabs">
          ${r.phases.map((p, i) => `<button type="button" class="phase-tab ${i === idx ? "active" : ""}" data-action="select-phase-tab" data-idx="${i}">${escapeHtml(p.name)}</button>`).join("")}
        </div>
      </div>
      <div class="data-table">
        <div class="data-table-head cols-recipe"><span>VARIABILE</span><span>SETPOINT</span><span>MIN</span><span>MAX</span><span>STRATEGIA</span></div>
        ${rows}
      </div>
      <div class="empty-note">Durata fase: ${fmtNum(phase.duration_hours, 1)} h · fotoperiodo dalle ${fmtNum(phase.photoperiod.start_hour, 1)} per ${fmtNum(phase.photoperiod.duration_hours, 1)} h (fino alle ${fmtNum(photoEnd, 1)}) · setpoint, banda e strategia sono definiti a livello di ricetta e non sono modificabili da questa dashboard.</div>
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Control view: all sectors in one filterable table                  */
/* ------------------------------------------------------------------ */

function renderControl() {
  const zones = STATE.zones;
  const deptNames = uniqueSorted(zones.map((z) => z.department_name).filter(Boolean));
  const speciesNames = uniqueSorted(zones.map((z) => z.plant_species).filter(Boolean));
  const f = STATE.controlFilters;

  const rows = zones.filter((z) => {
    if (f.dept !== "all" && z.department_name !== f.dept) return false;
    if (f.species !== "all" && z.plant_species !== f.species) return false;
    if (f.strategy !== "all" && !Object.values(z.current_strategies).includes(f.strategy)) return false;
    return true;
  });

  setPageTitle("Controllo settori", `Accesso diretto al controllo avanzato di ogni settore · ${zones.length} settori registrati (massimo 10)`);

  const rowsHtml = rows.map((z) => {
    const strategies = uniqueSorted(Object.values(z.current_strategies));
    return `
      <div class="data-table-row cols-control" data-action="open-zone-advanced" data-zone-id="${escapeAttr(z.id)}" style="cursor:pointer">
        <div class="settore-cell"><span class="conn-dot ${z.status === "online" ? "online" : "offline"}"></span><span class="label">${zoneLabel(z)}</span></div>
        <div>${escapeHtml(z.department_name)}</div>
        <div style="color:var(--ink-soft)">${escapeHtml(z.plant_species || "—")}</div>
        <div class="mono" style="font-size:11.5px;color:var(--ink-mute)">${escapeHtml(z.current_phase || "—")}</div>
        <div><span class="pill op-${z.operational_state}">${z.operational_state}</span></div>
        <div class="mono" style="font-size:10.5px;color:var(--ink-faint)">${strategies.join(" · ")}</div>
        <div class="link-arrow">→</div>
      </div>
    `;
  }).join("");

  document.getElementById("view-control").innerHTML = `
    <div class="toolbar">
      <span class="mono" style="font:500 10.5px var(--mono);letter-spacing:.07em;color:var(--ink-faint)">FILTRI</span>
      <select data-action="control-filter" data-filter="dept">
        <option value="all" ${f.dept === "all" ? "selected" : ""}>Tutti i reparti</option>
        ${deptNames.map((n) => `<option value="${escapeAttr(n)}" ${f.dept === n ? "selected" : ""}>${escapeHtml(n)}</option>`).join("")}
      </select>
      <select data-action="control-filter" data-filter="species">
        <option value="all" ${f.species === "all" ? "selected" : ""}>Tutte le specie</option>
        ${speciesNames.map((n) => `<option value="${escapeAttr(n)}" ${f.species === n ? "selected" : ""}>${escapeHtml(n)}</option>`).join("")}
      </select>
      <select data-action="control-filter" data-filter="strategy">
        <option value="all" ${f.strategy === "all" ? "selected" : ""}>Tutte le strategie</option>
        ${STRATEGIES.map((s) => `<option value="${s}" ${f.strategy === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
      <span class="count">${rows.length} ${rows.length === 1 ? "settore" : "settori"}</span>
    </div>
    <div class="data-table">
      <div class="data-table-head cols-control"><span>SETTORE</span><span>REPARTO</span><span>SPECIE</span><span>FASE</span><span>SICUREZZA</span><span>STRATEGIE</span><span></span></div>
      ${rowsHtml || '<div class="empty-note">Nessun settore corrisponde ai filtri selezionati.</div>'}
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Zone detail modal                                                  */
/* ------------------------------------------------------------------ */

async function openZoneModal(zoneId, entry) {
  STATE.modalZoneId = zoneId;
  STATE.modalTab = entry === "advanced" ? "advanced" : "summary";
  STATE.modalTelemetry = null;
  STATE.modalActuators = null;
  STATE.modalRecipe = null;
  STATE.modalRecipeId = null;
  STATE.strategyDrafts = {};
  STATE.strategyStatus = {};
  STATE.recipeHintVisible = false;
  STATE.phasePending = false;
  STATE.modalChartVariable = "soil_moisture";
  STATE.modalChartData = [];

  STATE.modalZone = STATE.zones.find((z) => z.id === zoneId) || null;
  document.getElementById("modal-overlay").classList.remove("hidden");
  renderModal();

  if (!STATE.modalZone) {
    try {
      STATE.modalZone = await apiGet(`/zones/${encodeURIComponent(zoneId)}`);
      updateZoneInState(STATE.modalZone);
      renderModal();
    } catch (e) {
      document.getElementById("modal-content").innerHTML = `<div class="empty-note">Impossibile caricare il settore: ${escapeHtml(e.message)}</div>`;
      return;
    }
  }

  if (STATE.modalZone.active_recipe_id) loadModalRecipe(STATE.modalZone.active_recipe_id);

  setPoll("modal", tickModal, MODAL_POLL_MS);
  if (STATE.modalTab === "advanced") {
    setPoll("modal-chart", refreshChartData, CHART_POLL_MS);
  }
}

function closeZoneModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  clearPoll("modal");
  clearPoll("modal-chart");
  STATE.adhocIntervals.forEach(clearInterval);
  STATE.adhocIntervals = [];
  STATE.modalZoneId = null;
  STATE.modalZone = null;
}

async function loadModalRecipe(recipeId) {
  try {
    const recipe = await apiGet(`/recipes/${encodeURIComponent(recipeId)}`);
    if (STATE.modalZoneId === null) return; // modal closed meanwhile
    STATE.modalRecipe = recipe;
    STATE.modalRecipeId = recipeId;
    renderModalIfSafe();
  } catch (e) {
    STATE.modalRecipe = null;
  }
}

async function tickModal() {
  if (!STATE.modalZoneId) return;
  const zoneId = STATE.modalZoneId;
  const [zoneRes, telemetryRes, actuatorsRes] = await Promise.allSettled([
    apiGet(`/zones/${encodeURIComponent(zoneId)}`),
    apiGet(`/zones/${encodeURIComponent(zoneId)}/telemetry/latest`),
    apiGet(`/zones/${encodeURIComponent(zoneId)}/actuators/latest`),
  ]);
  if (STATE.modalZoneId !== zoneId) return; // modal changed while requests were in flight

  if (zoneRes.status === "fulfilled") {
    updateZoneInState(zoneRes.value);
    if (zoneRes.value.active_recipe_id && zoneRes.value.active_recipe_id !== STATE.modalRecipeId) {
      loadModalRecipe(zoneRes.value.active_recipe_id);
    } else if (!zoneRes.value.active_recipe_id) {
      STATE.modalRecipe = null;
      STATE.modalRecipeId = null;
    }
  }
  STATE.modalTelemetry = telemetryRes.status === "fulfilled" ? telemetryRes.value : null;
  STATE.modalActuators = actuatorsRes.status === "fulfilled" ? actuatorsRes.value : null;
  renderModalIfSafe();
}

function renderModalIfSafe() {
  // Avoid yanking focus away from an open <select> mid-interaction.
  const active = document.activeElement;
  if (active && active.tagName === "SELECT" && isFocusedInside("modal-content")) return;
  renderModal();
}

function renderModal() {
  const zone = STATE.modalZone;
  const wrap = document.getElementById("modal-content");
  if (!zone) {
    wrap.innerHTML = '<div class="empty-note">Caricamento…</div>';
    return;
  }
  const meta = DEPT_META[zone.department_number] || DEPT_META[1];
  const online = zone.status === "online";

  wrap.innerHTML = `
    <div class="zone-header" style="--zone-tint:${meta.tint}">
      <div style="min-width:0">
        <div class="zone-header-code">
          <span class="code">${zoneLabel(zone)}</span>
          <span class="pill op-${zone.operational_state}"><span class="pill-dot"></span>${zone.operational_state}</span>
        </div>
        <h2>Reparto ${zone.department_number} — Settore ${zone.sector_number}</h2>
        <div class="zone-header-meta">
          <span>${escapeHtml(zone.plant_species || "Zona mista (quarantena)")}</span>
          <span class="sep"></span>
          <span>fase ${escapeHtml(zone.current_phase || "nessuna")}</span>
          <span class="sep"></span>
          <span class="mono" style="color:${online ? "#1f7a51" : "#c15a4a"}">${online ? "Online" : "Offline"}</span>
        </div>
      </div>
      <button type="button" class="zone-close" data-action="close-modal">✕</button>
    </div>
    ${STATE.modalTab === "advanced" ? renderZoneAdvanced(zone) : renderZoneSummary(zone)}
  `;

  if (STATE.modalTab === "advanced") drawModalChart();
}

function renderZoneSummary(zone) {
  const adminLabel = ADMIN_LABELS[zone.administrative_status] || zone.administrative_status;
  const lifecycleLabel = LIFECYCLE_LABELS[zone.lifecycle_state] || zone.lifecycle_state;
  const op = OP_META[zone.operational_state] || OP_META.Nominal;
  const online = zone.status === "online";
  const contactLabel = zone.last_edge_contact ? fmtDateTime(zone.last_edge_contact) : "mai contattato";

  const canAdvance = zone.lifecycle_state === "Running" && !zone.cultivation_completed && !STATE.phasePending;
  let phaseSub;
  if (STATE.phasePending) phaseSub = "comando inviato · in attesa dell'Edge Controller";
  else if (!zone.active_recipe_id) phaseSub = "nessuna ricetta assegnata a questo settore";
  else if (zone.cultivation_completed) phaseSub = "ricetta completata";
  else if (zone.lifecycle_state !== "Running") phaseSub = `settore non in esecuzione (${lifecycleLabel.toLowerCase()})`;
  else phaseSub = "fase corrente confermata dall'Edge Controller";

  const recipe = STATE.modalRecipe;
  const tiles = VARIABLES.map((v) => {
    const reading = STATE.modalTelemetry ? STATE.modalTelemetry[v.sensorField] : null;
    const strategy = zone.current_strategies[v.key];
    const target = findPhaseTarget(recipe, zone.current_phase, v.key);
    const min = target ? target.allowed_range.minimum : null;
    const max = target ? target.allowed_range.maximum : null;
    const band = bandCalc(reading, min, max);
    return `
      <div class="control-tile">
        <div class="control-tile-head"><span class="label">${v.label}</span><span class="strategy">${strategy}</span></div>
        <div class="control-tile-value"><span class="num" style="color:${band.color}">${reading !== null && reading !== undefined ? fmtNum(reading, v.decimals) : "—"}</span><span class="unit">${v.unit}</span></div>
        <div class="control-band">
          ${band.hasBand ? `<div class="control-band-fill" style="left:${band.bandLeft}%;width:${band.bandWidth}%"></div><div class="control-band-dot" style="left:${band.pct}%;background:${band.color}"></div>` : ""}
        </div>
        <div class="control-tile-foot"><span>target ${target ? fmtNum(min, v.decimals) + " – " + fmtNum(max, v.decimals) : "n/d"}</span><span style="color:${band.color}">${band.flag}</span></div>
      </div>
    `;
  }).join("");

  return `
    <div class="zone-body">
      <section class="zone-section">
        <div class="zone-section-title">Anagrafica settore</div>
        <div class="kv-row"><span class="k">Reparto di appartenenza</span><b class="v">${escapeHtml(zone.department_name)}</b></div>
        <div class="kv-row"><span class="k">Specie coltivata</span><b class="v">${escapeHtml(zone.plant_species || "—")}</b></div>
        <div class="kv-row"><span class="k">Stato amministrativo</span><b class="v">${escapeHtml(adminLabel)}</b></div>
      </section>
      <section class="zone-section" style="background:#fbfdfc">
        <div class="live-head"><span class="live-badge">LIVE</span><span class="title">Stato riportato dal sistema</span><span class="ro">sola lettura</span></div>
        <div class="status-tile"><span class="status-tile-icon" style="background:${op.color}"></span><div><div class="label">Stato di sicurezza</div><div class="value mono" style="color:${op.color}">${zone.operational_state}</div></div></div>
        <div class="status-tile"><span class="status-tile-icon round" style="background:${online ? "#2f9e6b" : "#c15a4a"}"></span><div><div class="label">Connessione</div><div class="value">${online ? "Online" : "Offline"}</div></div></div>
        <div class="status-tile"><span class="status-tile-icon round" style="border:2px solid ${online ? "#2f9e6b" : "#9aada4"}"></span><div><div class="label">Attività corrente</div><div class="value">${escapeHtml(lifecycleLabel)}</div><div class="sub">ultimo contatto Edge · ${escapeHtml(contactLabel)}</div></div></div>
      </section>
      <section class="zone-section span2">
        <div class="zone-section-title">Fase della ricetta</div>
        <div class="phase-row">
          <div><div class="phase-name">${escapeHtml(zone.current_phase || "Nessuna coltivazione attiva")}</div><div class="phase-sub" style="color:${STATE.phasePending ? "var(--warn)" : "var(--ink-faint)"}">${escapeHtml(phaseSub)}</div></div>
          <button type="button" class="btn ${canAdvance ? "btn-primary" : ""}" data-action="advance-phase" ${canAdvance ? "" : "disabled"}>${STATE.phasePending ? "Attesa conferma…" : "Avanza alla fase successiva"}</button>
        </div>
      </section>
      <section class="zone-section span2">
        <div class="live-head"><span class="title">Controlli</span><span class="ro">6 variabili · target dalla ricetta attiva</span></div>
        <div class="controls-grid">${tiles}</div>
        <div style="display:flex;justify-content:flex-end;margin-top:16px">
          <button type="button" class="btn btn-primary" data-action="open-advanced-tab">Modifica parametri →</button>
        </div>
      </section>
    </div>
  `;
}

function renderZoneAdvanced(zone) {
  const recipe = STATE.modalRecipe;
  const rows = VARIABLES.map((v) => {
    const target = findPhaseTarget(recipe, zone.current_phase, v.key);
    const live = zone.current_strategies[v.key];
    const draft = Object.prototype.hasOwnProperty.call(STATE.strategyDrafts, v.key) ? STATE.strategyDrafts[v.key] : live;
    const status = STATE.strategyStatus[v.key];
    const dirty = draft !== live && status !== "sending" && status !== "waiting";
    const locked = status === "sending" || status === "waiting";

    let statusHtml = "";
    if (status === "sending") statusHtml = '<span class="strategy-status pending">invio…</span>';
    else if (status === "waiting") statusHtml = '<span class="strategy-status pending">in attesa…</span>';
    else if (status === "applied") statusHtml = '<span class="strategy-status applied">applicata ✓</span>';
    else if (dirty) statusHtml = `<button type="button" class="btn btn-warn" data-action="confirm-strategy" data-variable="${v.key}">Conferma</button>`;

    return `
      <div class="data-table-row cols-advanced">
        <div><div class="var-name">${v.label}</div><div class="var-unit">${v.unit}</div></div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.setpoint, v.decimals) : "—"}</div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.allowed_range.minimum, v.decimals) : "—"}</div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.allowed_range.maximum, v.decimals) : "—"}</div>
        <div class="strategy-cell">
          <select data-action="strategy-select" data-variable="${v.key}" ${locked ? "disabled" : ""}>
            ${STRATEGIES.map((s) => `<option value="${s}" ${draft === s ? "selected" : ""}>${s}</option>`).join("")}
          </select>
          ${statusHtml}
        </div>
      </div>
    `;
  }).join("");

  const hintBox = STATE.recipeHintVisible ? `
    <div class="recipe-hint-box">
      <span>Setpoint, min e max arrivano dalla fase attiva della ricetta assegnata${recipe ? " (" + escapeHtml(recipe.id) + ")" : ""}: non sono modificabili per singolo settore.</span>
      ${zone.active_recipe_id ? '<button type="button" class="btn" data-action="open-recipe-from-zone">Vedi ricetta assegnata →</button>' : ""}
    </div>
  ` : "";

  return `
    <div style="padding:20px 28px 28px">
      <div class="advanced-hint-row">
        <button type="button" class="btn" data-action="back-to-summary">← Indietro</button>
        <div style="font-size:15.5px;font-weight:600">Controllo avanzato</div>
        <div class="spacer"></div>
        <span class="hint">Setpoint / Min / Max sono di sola lettura · solo la Strategia è modificabile</span>
      </div>
      <div class="data-table">
        <div class="data-table-head cols-advanced"><span>VARIABILE</span><span>SETPOINT</span><span>MIN</span><span>MAX</span><span>STRATEGIA</span></div>
        ${rows}
      </div>
      ${hintBox}
      <div class="chart-actuators-grid">
        <div class="zone-section">
          <div class="chart-head">
            <span class="title">Andamento ultime 24 h</span>
            <select id="chart-variable-select">
              ${VARIABLES.map((v) => `<option value="${v.key}" ${STATE.modalChartVariable === v.key ? "selected" : ""}>${v.label}</option>`).join("")}
            </select>
            <span class="hint">misura vs setpoint</span>
          </div>
          <div class="chart-canvas-wrap"><canvas id="telemetry-chart" style="width:100%;height:100%;display:block"></canvas></div>
        </div>
        <div class="zone-section">
          <div class="zone-section-title">Attuatori del settore</div>
          <div id="actuators-list">${renderActuatorsList()}</div>
        </div>
      </div>
    </div>
  `;
}

function renderActuatorsList() {
  const a = STATE.modalActuators;
  if (!a) return '<div class="empty-note">Nessuno snapshot attuatori disponibile per questo settore.</div>';
  const out = a.output;
  const cmd = a.command;
  // NOTE: the backend serializes FertilizerValveStates with its JSON
  // aliases ("ph-up" / "ph-down"), not the Python field names.
  const items = [
    { name: "Pompa acqua comune", on: out.water_pump_on, v: out.water_pump_on ? `ON · ${fmtNum(out.water_pump_flow_liters_per_hour, 1)} L/h` : "OFF" },
    { name: "Elettrovalvola azoto (N)", on: out.fertilizer_valves_open.nitrogen, v: out.fertilizer_valves_open.nitrogen ? "aperta" : "chiusa" },
    { name: "Elettrovalvola fosforo (P)", on: out.fertilizer_valves_open.phosphorus, v: out.fertilizer_valves_open.phosphorus ? "aperta" : "chiusa" },
    { name: "Elettrovalvola potassio (K)", on: out.fertilizer_valves_open.potassium, v: out.fertilizer_valves_open.potassium ? "aperta" : "chiusa" },
    { name: "Elettrovalvola pH+", on: out.fertilizer_valves_open["ph-up"], v: out.fertilizer_valves_open["ph-up"] ? "aperta" : "chiusa" },
    { name: "Elettrovalvola pH−", on: out.fertilizer_valves_open["ph-down"], v: out.fertilizer_valves_open["ph-down"] ? "aperta" : "chiusa" },
    { name: "Barra LED", on: cmd.lighting_percent > 0, v: `${fmtNum(cmd.lighting_percent, 0)}% illuminazione` },
  ];
  return items.map((it) => `
    <div class="actuator-row">
      <span class="swatch" style="background:${it.on ? "#1f7a51" : "#9aada4"}"></span>
      <span class="name">${it.name}</span>
      <b style="color:${it.on ? "#1f7a51" : "#9aada4"}">${it.v}</b>
    </div>
  `).join("");
}

/* ---- advance phase --------------------------------------------------- */

async function advancePhase() {
  const zone = STATE.modalZone;
  if (!zone) return;
  const canAdvance = zone.lifecycle_state === "Running" && !zone.cultivation_completed && !STATE.phasePending;
  if (!canAdvance) return;
  STATE.phasePending = true;
  renderModal();
  try {
    const cmdId = `advance-${zone.id}-${Date.now()}`;
    await apiPost(`/zones/${encodeURIComponent(zone.id)}/commands`, { command_id: cmdId, command_type: "AdvanceRecipePhase", payload: {} });
    showToast("Comando di avanzamento fase inviato all'Edge Controller.");
    pollForPhaseChange(zone.id, zone.current_phase);
  } catch (err) {
    STATE.phasePending = false;
    renderModal();
    showToast("Invio del comando non riuscito: " + err.message, "error");
  }
}

function pollForPhaseChange(zoneId, previousPhase) {
  let elapsed = 0;
  const iv = setInterval(async () => {
    elapsed += COMMAND_POLL_MS;
    try {
      const zone = await apiGet(`/zones/${encodeURIComponent(zoneId)}`);
      updateZoneInState(zone);
      if (zone.current_phase !== previousPhase || zone.cultivation_completed) {
        clearInterval(iv);
        STATE.phasePending = false;
        if (zone.active_recipe_id) loadModalRecipe(zone.active_recipe_id);
        renderModalIfSafe();
        return;
      }
    } catch (e) { /* transient error, keep polling */ }
    if (elapsed >= COMMAND_TIMEOUT_MS) {
      clearInterval(iv);
      STATE.phasePending = false;
      renderModalIfSafe();
      showToast("Nessuna conferma dall'Edge Controller entro il timeout.", "warn");
    }
  }, COMMAND_POLL_MS);
  STATE.adhocIntervals.push(iv);
}

/* ---- strategy change --------------------------------------------------- */

function onStrategyDraftChange(variableKey, value) {
  STATE.strategyDrafts[variableKey] = value;
  STATE.strategyStatus[variableKey] = null;
  renderModal();
}

async function sendStrategyChange(variableKey) {
  const zone = STATE.modalZone;
  if (!zone) return;
  const draft = STATE.strategyDrafts[variableKey];
  if (!draft || draft === zone.current_strategies[variableKey]) return;
  const target = findPhaseTarget(STATE.modalRecipe, zone.current_phase, variableKey);

  STATE.strategyStatus[variableKey] = "sending";
  renderModal();
  try {
    const params = buildStrategyParameters(draft, target);
    const base = `strategy-${zone.id}-${variableKey}-${Date.now()}`;
    await apiPost(`/zones/${encodeURIComponent(zone.id)}/commands`, {
      command_id: base,
      command_type: "ChangeStrategy",
      payload: { variable: variableKey, strategy: draft, parameters: params },
    });
    await apiPost(`/zones/${encodeURIComponent(zone.id)}/commands`, {
      command_id: base + "-confirm",
      command_type: "ConfirmConfiguration",
      payload: { variable: variableKey },
    });
    STATE.strategyStatus[variableKey] = "waiting";
    renderModal();
    showToast("Comando inviato: in attesa che l'Edge Controller applichi la nuova strategia.");
    pollForStrategyApplied(zone.id, variableKey, draft);
  } catch (err) {
    STATE.strategyStatus[variableKey] = null;
    renderModal();
    showToast("Invio del comando non riuscito: " + err.message, "error");
  }
}

function pollForStrategyApplied(zoneId, variableKey, target) {
  let elapsed = 0;
  const iv = setInterval(async () => {
    elapsed += COMMAND_POLL_MS;
    try {
      const zone = await apiGet(`/zones/${encodeURIComponent(zoneId)}`);
      updateZoneInState(zone);
      if (zone.current_strategies[variableKey] === target) {
        clearInterval(iv);
        STATE.strategyStatus[variableKey] = "applied";
        delete STATE.strategyDrafts[variableKey];
        renderModalIfSafe();
        setTimeout(() => {
          if (STATE.strategyStatus[variableKey] === "applied") {
            STATE.strategyStatus[variableKey] = null;
            renderModalIfSafe();
          }
        }, 4000);
        return;
      }
    } catch (e) { /* transient error, keep polling */ }
    if (elapsed >= COMMAND_TIMEOUT_MS) {
      clearInterval(iv);
      if (STATE.strategyStatus[variableKey] === "waiting") {
        STATE.strategyStatus[variableKey] = null;
        renderModalIfSafe();
        showToast("Nessuna conferma dall'Edge Controller entro il timeout (verifica che sia in esecuzione).", "warn");
      }
    }
  }, COMMAND_POLL_MS);
  STATE.adhocIntervals.push(iv);
}

/* ---- telemetry chart (hand-rolled canvas, no external deps) ----------- */

async function refreshChartData() {
  if (!STATE.modalZoneId) return;
  const zoneId = STATE.modalZoneId;
  const key = STATE.modalChartVariable;
  const varMeta = VARIABLES_BY_KEY[key];
  try {
    const from = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
    const samples = await apiGet(`/zones/${encodeURIComponent(zoneId)}/telemetry`, { from, limit: 500 });
    if (STATE.modalZoneId !== zoneId) return;
    STATE.modalChartData = samples.map((s) => ({ t: new Date(s.recorded_at), v: s[varMeta.sensorField] }));
  } catch (e) {
    STATE.modalChartData = [];
  }
  drawModalChart();
}

function onChartVariableChange(key) {
  STATE.modalChartVariable = key;
  refreshChartData();
}

function drawModalChart() {
  const canvas = document.getElementById("telemetry-chart");
  if (!canvas) return;
  const zone = STATE.modalZone;
  const key = STATE.modalChartVariable;
  const varMeta = VARIABLES_BY_KEY[key];
  const setpoint = zone && zone.current_setpoints ? zone.current_setpoints[key] : null;
  drawTelemetryChart(canvas, STATE.modalChartData || [], setpoint, varMeta.unit);
}

function drawTelemetryChart(canvas, points, setpoint, unit) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 1);
  const height = Math.max(rect.height, 1);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const valid = points.filter((p) => p.v !== null && p.v !== undefined && isFinite(p.v));
  if (valid.length < 2) {
    ctx.fillStyle = "#8aa39a";
    ctx.font = "11px Poppins, sans-serif";
    ctx.fillText(valid.length ? "Dati insufficienti per il grafico" : "Nessun dato di telemetria nelle ultime 24 h", 16, height / 2);
    return;
  }

  const pad = { l: 40, r: 14, t: 14, b: 8 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;

  const values = valid.map((p) => p.v);
  let minV = Math.min(...values);
  let maxV = Math.max(...values);
  if (setpoint !== null && setpoint !== undefined && isFinite(setpoint)) {
    minV = Math.min(minV, setpoint);
    maxV = Math.max(maxV, setpoint);
  }
  if (minV === maxV) { minV -= 1; maxV += 1; }
  const spanPad = (maxV - minV) * 0.12;
  minV -= spanPad;
  maxV += spanPad;

  const times = valid.map((p) => p.t.getTime());
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const spanT = maxT - minT || 1;

  const x = (t) => pad.l + ((t - minT) / spanT) * w;
  const y = (v) => pad.t + h - ((v - minV) / (maxV - minV)) * h;

  ctx.strokeStyle = "#eef3ef";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const gy = pad.t + (h / 3) * i;
    ctx.beginPath();
    ctx.moveTo(pad.l, gy);
    ctx.lineTo(pad.l + w, gy);
    ctx.stroke();
  }

  if (setpoint !== null && setpoint !== undefined && isFinite(setpoint)) {
    ctx.strokeStyle = "#c9803f";
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(pad.l, y(setpoint));
    ctx.lineTo(pad.l + w, y(setpoint));
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.strokeStyle = "#1f7a51";
  ctx.lineWidth = 2;
  ctx.beginPath();
  valid.forEach((p, i) => {
    const px = x(p.t.getTime());
    const py = y(p.v);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();

  ctx.fillStyle = "#8aa39a";
  ctx.font = "10px 'IBM Plex Mono', monospace";
  ctx.fillText(`${maxV.toFixed(1)} ${unit}`, 2, pad.t + 8);
  ctx.fillText(`${minV.toFixed(1)} ${unit}`, 2, pad.t + h);
}

/* ------------------------------------------------------------------ */
/* View switching                                                     */
/* ------------------------------------------------------------------ */

function switchView(view) {
  STATE.view = view;
  document.querySelectorAll("#main-nav .nav-item").forEach((btn) => {
    const active = btn.dataset.view === view || (view === "recipe" && btn.dataset.view === "recipes");
    btn.classList.toggle("active", active);
  });
  ["home", "recipes", "recipe", "control"].forEach((v) => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== view);
  });

  clearPoll("zones");
  clearPoll("recipes-poll");

  if (view === "home") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "control") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "recipes") {
    setPoll("recipes-poll", tickRecipes, RECIPES_POLL_MS);
  }
}

/* ------------------------------------------------------------------ */
/* Event delegation (bound once; view content is re-rendered freely)  */
/* ------------------------------------------------------------------ */

function initEventDelegation() {
  document.getElementById("main-nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-item");
    if (btn) switchView(btn.dataset.view);
  });

  document.addEventListener("click", (e) => {
    if (e.target.id === "modal-overlay") { closeZoneModal(); return; }

    const closeBtn = e.target.closest('[data-action="close-modal"]');
    if (closeBtn) { closeZoneModal(); return; }

    const openZone = e.target.closest('[data-action="open-zone"], [data-action="open-zone-advanced"]');
    if (openZone) {
      const entry = openZone.dataset.action === "open-zone-advanced" ? "advanced" : "summary";
      openZoneModal(openZone.dataset.zoneId, entry);
      return;
    }

    const openRecipe = e.target.closest('[data-action="open-recipe"]');
    if (openRecipe) { openRecipeDetail(openRecipe.dataset.recipeId); return; }

    const backRecipes = e.target.closest('[data-action="back-to-recipes"]');
    if (backRecipes) { switchView("recipes"); return; }

    const phaseTab = e.target.closest('[data-action="select-phase-tab"]');
    if (phaseTab) { STATE.recipeDetailPhaseIdx = Number(phaseTab.dataset.idx); renderRecipeDetail(); return; }

    const openAdv = e.target.closest('[data-action="open-advanced-tab"]');
    if (openAdv) {
      STATE.modalTab = "advanced";
      renderModal();
      setPoll("modal-chart", refreshChartData, CHART_POLL_MS);
      return;
    }

    const backSummary = e.target.closest('[data-action="back-to-summary"]');
    if (backSummary) {
      STATE.modalTab = "summary";
      clearPoll("modal-chart");
      renderModal();
      return;
    }

    const advanceBtn = e.target.closest('[data-action="advance-phase"]');
    if (advanceBtn && !advanceBtn.disabled) { advancePhase(); return; }

    const confirmBtn = e.target.closest('[data-action="confirm-strategy"]');
    if (confirmBtn) { sendStrategyChange(confirmBtn.dataset.variable); return; }

    const hintCell = e.target.closest('[data-action="show-recipe-hint"]');
    if (hintCell) { STATE.recipeHintVisible = true; renderModal(); return; }

    const openRecipeFromZone = e.target.closest('[data-action="open-recipe-from-zone"]');
    if (openRecipeFromZone) {
      const rid = STATE.modalZone ? STATE.modalZone.active_recipe_id : null;
      closeZoneModal();
      if (rid) openRecipeDetail(rid);
      return;
    }
  });

  document.addEventListener("change", (e) => {
    const strategySelect = e.target.closest('[data-action="strategy-select"]');
    if (strategySelect) { onStrategyDraftChange(strategySelect.dataset.variable, strategySelect.value); return; }

    const filterSelect = e.target.closest('[data-action="control-filter"]');
    if (filterSelect) { STATE.controlFilters[filterSelect.dataset.filter] = filterSelect.value; renderControl(); return; }

    if (e.target.id === "chart-variable-select") { onChartVariableChange(e.target.value); return; }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "recipe-search") {
      STATE.recipeQuery = e.target.value;
      updateRecipeGridOnly();
    }
  });
}

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

function init() {
  initEventDelegation();
  switchView("home");
  setPoll("system-status", tickSystemStatus, STATUS_POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);