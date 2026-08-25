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
/* Recipe form (create/edit) constants                                */
/*                                                                    */
/* Mirrors the fixed variable <-> input_source/actuator/default_strategy */
/* associations enforced server-side in                               */
/* backend/app/features/recipes/models.py                             */
/* (_REQUIRED_INPUT_SOURCE / _REQUIRED_ACTUATOR /                     */
/* _required_default_strategy) and validated again, more strictly, by */
/* smarthydro::RecipeControlSystem::confirm_configuration() on the    */
/* Edge — a recipe whose N/P/K selected_strategy isn't Predictive     */
/* fails that confirmation and, since recipe adoption now confirms    */
/* synchronously (see control_system.cpp's confirm_all_from_recipe),  */
/* would reject the whole ActivateCultivation/LoadRecipe command.     */
/* Locking the form's Strategy choice for N/P/K prevents ever          */
/* constructing a payload that could trigger that at adoption time.   */
/* ------------------------------------------------------------------ */

const NUTRIENT_VARIABLES = ["nitrogen", "phosphorus", "potassium"];
const REQUIRED_INPUT_SOURCE = {
  soil_moisture: "soil_moisture_sensor",
  light: "light_sensor",
  ph: "ph_sensor",
  nitrogen: "nitrogen_model",
  phosphorus: "phosphorus_model",
  potassium: "potassium_model",
};
const REQUIRED_ACTUATOR = {
  soil_moisture: "water_pump",
  light: "lighting",
  ph: "ph_corrector_valves",
  nitrogen: "nitrogen_valve",
  phosphorus: "phosphorus_valve",
  potassium: "potassium_valve",
};
const REQUIRED_DEFAULT_STRATEGY = {
  soil_moisture: "Threshold",
  light: "Threshold",
  ph: "PID",
  nitrogen: "Predictive",
  phosphorus: "Predictive",
  potassium: "Predictive",
};
const VARIABLE_UNIT = {
  soil_moisture: "% soil moisture",
  light: "umol/(m2 s)",
  ph: "pH",
  nitrogen: "mg/L",
  phosphorus: "mg/L",
  potassium: "mg/L",
};
// The two OutputSafetyLimits fields the recipe form does not expose
// (the user's spec lists exactly 5 editable safety limits); these get a
// sensible fixed default instead, matching config/example_recipe.json.
const HIDDEN_OUTPUT_LIMIT_DEFAULTS = {
  water_pump_flow_liters_per_hour: 2.0,
  ph_settling_time_seconds: 1800.0,
};
const OUTPUT_LIMIT_FIELDS = [
  { key: "maximum_water_volume_liters", label: "Volume max", unit: "L" },
  { key: "maximum_pump_duration_seconds", label: "Durata max pompa", unit: "s" },
  { key: "maximum_dose_per_command_milliliters", label: "Dose max/comando", unit: "mL" },
  { key: "maximum_daily_dose_milliliters", label: "Dose max giornaliera", unit: "mL" },
  { key: "minimum_seconds_between_doses", label: "Intervallo min fra dosi", unit: "s" },
];
const CARE_FIELDS = [
  { key: "light", label: "Luce" },
  { key: "watering", label: "Irrigazione" },
  { key: "temperature", label: "Temperatura" },
  { key: "fertilization", label: "Fertilizzazione" },
];

function defaultOutputLimits(variableKey) {
  if (variableKey === "ph") {
    return { maximum_water_volume_liters: 1, maximum_pump_duration_seconds: 1800, maximum_dose_per_command_milliliters: 0.5, maximum_daily_dose_milliliters: 5, minimum_seconds_between_doses: 900 };
  }
  if (NUTRIENT_VARIABLES.includes(variableKey)) {
    return { maximum_water_volume_liters: 1, maximum_pump_duration_seconds: 1800, maximum_dose_per_command_milliliters: 4, maximum_daily_dose_milliliters: 12, minimum_seconds_between_doses: 3600 };
  }
  return { maximum_water_volume_liters: 1, maximum_pump_duration_seconds: 1800, maximum_dose_per_command_milliliters: 5, maximum_daily_dose_milliliters: 20, minimum_seconds_between_doses: 900 };
}

const DEFAULT_PHASE_TARGETS = {
  soil_moisture: { setpoint: 60, allowed_range: { minimum: 50, maximum: 70 }, safety_range: { minimum: 25, maximum: 90 }, suggested_phase_dose_milliliters: 0 },
  light: { setpoint: 450, allowed_range: { minimum: 400, maximum: 500 }, safety_range: { minimum: 0, maximum: 1200 }, suggested_phase_dose_milliliters: 0 },
  ph: { setpoint: 6.2, allowed_range: { minimum: 6.0, maximum: 6.4 }, safety_range: { minimum: 4.5, maximum: 8.0 }, suggested_phase_dose_milliliters: 6 },
  nitrogen: { setpoint: 150, allowed_range: { minimum: 130, maximum: 170 }, safety_range: { minimum: 50, maximum: 300 }, suggested_phase_dose_milliliters: 30 },
  phosphorus: { setpoint: 50, allowed_range: { minimum: 40, maximum: 60 }, safety_range: { minimum: 10, maximum: 120 }, suggested_phase_dose_milliliters: 12 },
  potassium: { setpoint: 200, allowed_range: { minimum: 170, maximum: 230 }, safety_range: { minimum: 50, maximum: 400 }, suggested_phase_dose_milliliters: 35 },
};

function defaultPhase(name) {
  return {
    name,
    duration_hours: 336,
    photoperiod: { start_hour: 6, duration_hours: 18 },
    targets: Object.fromEntries(VARIABLES.map((v) => [v.key, { ...DEFAULT_PHASE_TARGETS[v.key], allowed_range: { ...DEFAULT_PHASE_TARGETS[v.key].allowed_range }, safety_range: { ...DEFAULT_PHASE_TARGETS[v.key].safety_range } }])),
  };
}

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

  // Draft object for the create/edit recipe form (see openRecipeForm()),
  // or null when the form is not open. Routed through the same pop-up
  // overlay as the zone/recipe modals via STATE.modalKind = "recipe-form".
  recipeForm: null,

  controlFilters: { dept: "all", species: "all", strategy: "all" },

  alerts: [],
  quarantine: {},
  plantCounts: {}, // zone_id -> live plant count (current_zone_id, not origin)
  homeExtrasLoaded: false,
  homeExtrasLastRun: 0,

  // { dept, sector, name, species, status, error } | null — the single
  // "aggiungi settore" inline form open on Home, if any.
  addSectorForm: null,

  modalZoneId: null,
  modalZone: null,
  modalKind: null, // "zone" | "recipe" | null — which pop-up #modal-content currently shows
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

  // Single shared "where did I come from" marker, captured once at the
  // moment a pop-up (or a sub-view inside one) is opened. Back buttons
  // consult this instead of hard-coding a destination, so the same
  // mechanism serves every pop-up's "Indietro" button.
  returnTo: null,

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

/** Dotted-path get/set into a plain nested object/array draft — used by the
 * recipe form's generic input binding (data-path="phases.0.targets.ph.setpoint"). */
function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o === null || o === undefined ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i++) cur = cur[keys[i]];
  cur[keys[keys.length - 1]] = value;
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
    if (STATE.view === "home") {
      // Don't yank focus/reset an in-progress "Aggiungi settore" form on a
      // background poll tick — same precedent as the Control view below.
      if (!isFocusedInside("view-home")) renderHome();
    } else if (STATE.view === "control") {
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

  if (n === 5) {
    // Quarantine has no per-sector cultivation like the production
    // departments — r5-s1/r5-s2 still exist server-side as the technical
    // destination for PATCH /plants/{id}/quarantine, but showing them as
    // "Settore" slots here doesn't make sense. Show plant-level info instead.
    return `
      <div class="dept-card" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
        <div class="dept-card-head">
          <span class="dept-code">REPARTO ${n}</span>
        </div>
        <div class="dept-title">${escapeHtml(name)}</div>
        <div class="quarantine-box" id="quarantine-box"></div>
      </div>
    `;
  }

  const slots = [1, 2].map((sn) => {
    const z = zones.find((zz) => zz.sector_number === sn);
    if (z) return renderSectorRow(z);
    const f = STATE.addSectorForm;
    if (f && f.dept === n && f.sector === sn) return renderAddSectorForm(f);
    return `<button type="button" class="sector-slot-add" data-action="open-add-sector" data-dept="${n}" data-sector="${sn}">+ Aggiungi settore</button>`;
  }).join("");
  return `
    <div class="dept-card" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
      <div class="dept-card-head">
        <span class="dept-code">REPARTO ${n}</span>
        <span class="dept-count">${zones.length} settor${zones.length === 1 ? "e" : "i"}</span>
      </div>
      <div class="dept-title">${escapeHtml(name)}</div>
      <div class="sector-list">${slots}</div>
    </div>
  `;
}

function renderAddSectorForm(f) {
  const sending = f.status === "sending";
  return `
    <div class="add-sector-form">
      <div class="field-label">Nuovo settore ${f.sector}</div>
      <input type="text" class="add-sector-input" data-action="add-sector-field" data-field="name"
        placeholder="Nome del settore" value="${escapeAttr(f.name)}" ${sending ? "disabled" : ""}>
      <input type="text" class="add-sector-input" data-action="add-sector-field" data-field="species"
        placeholder="Specie coltivata" value="${escapeAttr(f.species)}" ${sending ? "disabled" : ""}>
      ${f.error ? `<div class="add-sector-error">${escapeHtml(f.error)}</div>` : ""}
      <div class="add-sector-actions">
        <button type="button" class="btn" data-action="cancel-add-sector" ${sending ? "disabled" : ""}>Annulla</button>
        <button type="button" class="btn btn-primary" data-action="submit-add-sector" ${sending ? "disabled" : ""}>${sending ? "Creazione…" : "Crea settore"}</button>
      </div>
    </div>
  `;
}

/**
 * id is auto-generated as "r{dept}-s{sector}" (matches the convention of
 * every existing zone id and the backend's id pattern) — never asked from
 * the user. department_number/sector_number come from which empty slot was
 * clicked, not from the form either.
 */
async function submitAddSector() {
  const form = STATE.addSectorForm;
  if (!form || form.status === "sending") return;
  const name = (form.name || "").trim();
  const species = (form.species || "").trim();
  if (!name || !species) {
    STATE.addSectorForm = { ...form, name, species, error: "Nome e specie coltivata sono obbligatori." };
    renderHome();
    return;
  }

  const zoneId = `r${form.dept}-s${form.sector}`;
  STATE.addSectorForm = { ...form, name, species, status: "sending", error: null };
  renderHome();

  try {
    const created = await apiPost("/zones", {
      id: zoneId,
      name,
      department_number: form.dept,
      sector_number: form.sector,
      plant_species: species,
    });
    STATE.zones.push(created);
    STATE.addSectorForm = null;
    renderHome();
    showToast(`Settore ${zoneId} creato.`);
  } catch (err) {
    if (STATE.addSectorForm) {
      STATE.addSectorForm = { ...STATE.addSectorForm, status: null, error: err.message };
      renderHome();
    }
  }
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
          <span class="tag-phase" id="plant-count-${escapeAttr(z.id)}">${escapeHtml(plantCountLabel(z.id))}</span>
        </div>
      </div>
      <span class="sector-row-arrow">→</span>
    </div>
  `;
}

/** Live plant count for one zone (current_zone_id, not origin), from the
 * throttled home-extras fetch — "…" while loading, "—" on fetch error. */
function plantCountLabel(zoneId) {
  if (!STATE.homeExtrasLoaded) return "…";
  const n = STATE.plantCounts[zoneId];
  if (n === null || n === undefined) return "—";
  return `${n} piant${n === 1 ? "a" : "e"}`;
}

async function loadPlantCounts(zones) {
  const results = await Promise.all(
    zones.map(async (z) => {
      try {
        const plants = await apiGet("/plants", { zone_id: z.id, limit: 1000 });
        return [z.id, plants.length];
      } catch (e) {
        return [z.id, null];
      }
    })
  );
  return Object.fromEntries(results);
}

function renderPlantCounts() {
  STATE.zones.forEach((z) => {
    const el = document.getElementById(`plant-count-${z.id}`);
    if (el) el.textContent = plantCountLabel(z.id);
  });
}

async function maybeFetchHomeExtras() {
  const now = Date.now();
  if (now - STATE.homeExtrasLastRun < HOME_EXTRAS_MIN_INTERVAL_MS) return;
  STATE.homeExtrasLastRun = now;
  try {
    const [alerts, quarantine, plantCounts] = await Promise.all([
      loadAlerts(STATE.zones),
      loadQuarantineStats(),
      loadPlantCounts(STATE.zones),
    ]);
    STATE.alerts = alerts;
    STATE.quarantine = quarantine;
    STATE.plantCounts = plantCounts;
  } catch (e) {
    console.error("failed to refresh home extras", e);
  }
  STATE.homeExtrasLoaded = true;
  renderAlertsPanel();
  renderQuarantineBox();
  renderPlantCounts();
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
  const addCard = `
    <button type="button" class="recipe-card-add" data-action="open-recipe-form-create">
      <span class="recipe-card-add-plus">+</span>
      <span>Nuova ricetta</span>
    </button>
  `;
  if (!list.length) return addCard + '<div class="empty-note">Nessuna ricetta trovata.</div>';
  return addCard + list.map((r) => `
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

/**
 * Recipe pop-up — read-only, same overlay/close/visual pattern as the zone
 * modal's "Controllo avanzato" (reuses #modal-overlay/#modal-content and the
 * .zone-header/.zone-close/.advanced-hint-row classes). `returnTo` is the
 * single "where did I come from" marker for this pop-up's Indietro button
 * (see goBack()); defaults to just closing when not given.
 */
function openRecipeModal(recipeId, returnTo) {
  // Stop any zone-modal polling so it doesn't refresh into this pop-up.
  clearPoll("modal");
  clearPoll("modal-chart");
  STATE.adhocIntervals.forEach(clearInterval);
  STATE.adhocIntervals = [];
  STATE.modalZoneId = null;
  STATE.modalZone = null;

  STATE.modalKind = "recipe";
  STATE.recipeDetailPhaseIdx = 0;
  STATE.recipeForm = null;
  STATE.returnTo = returnTo || { action: "closeModal" };
  document.getElementById("modal-overlay").classList.remove("hidden");

  const cached = STATE.recipesById[recipeId];
  if (cached) {
    STATE.currentRecipe = cached;
    renderModal();
    return;
  }
  STATE.currentRecipe = null;
  renderModal();
  apiGet(`/recipes/${encodeURIComponent(recipeId)}`).then((r) => {
    STATE.currentRecipe = r;
    STATE.recipesById[r.id] = r;
    if (STATE.modalKind === "recipe") renderModal();
  }).catch((e) => {
    if (STATE.modalKind === "recipe") {
      document.getElementById("modal-content").innerHTML = `<div class="empty-note">Impossibile caricare la ricetta: ${escapeHtml(e.message)}</div>`;
    }
  });
}

function renderRecipeModal() {
  const r = STATE.currentRecipe;
  const wrap = document.getElementById("modal-content");
  if (!r) {
    wrap.innerHTML = '<div class="empty-note">Caricamento ricetta…</div>';
    return;
  }

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

  wrap.innerHTML = `
    <div class="zone-header" style="--zone-tint:#eef2e4">
      <div style="min-width:0">
        <div class="zone-header-code">
          <span class="code">${escapeHtml(r.id)}</span>
          <span class="recipe-badge">v${r.version}</span>
        </div>
        <h2>${escapeHtml(r.plant_type)}</h2>
        <div class="zone-header-meta">
          <span>${escapeHtml(r.department_name || "Reparto non catalogato")}</span>
          <span class="sep"></span>
          <span>substrato ${escapeHtml(SUBSTRATE_LABELS[r.substrate] || r.substrate)}</span>
          <span class="sep"></span>
          <span>${r.phases.length} fasi</span>
        </div>
      </div>
      <button type="button" class="zone-close" data-action="close-modal">✕</button>
    </div>
    <div style="padding:20px 28px 28px">
      <div class="advanced-hint-row">
        <button type="button" class="btn" data-action="recipe-back">← Indietro</button>
        <div style="font-size:15.5px;font-weight:600">Variabili ricetta</div>
        <div class="spacer"></div>
        <span class="hint">Setpoint / Min / Max / Strategia sono di sola lettura in questa vista</span>
        <button type="button" class="btn btn-primary" data-action="open-recipe-form-edit">Modifica ricetta →</button>
      </div>
      <div class="recipe-detail-card">
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
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Recipe create/edit form                                            */
/*                                                                    */
/* One shared form builds the full POST /recipes payload for both a   */
/* brand-new recipe and a new version of an existing one — same       */
/* layout, pre-filled with the existing recipe's values when editing. */
/* The backend always creates a new version (never overwrites in      */
/* place): version is computed here as existing.version + 1 (or 1 for */
/* a new recipe) and is never asked from the user. Client-side         */
/* validation mirrors backend/app/features/recipes/models.py's        */
/* PhaseVariableTarget._check_range_chain exactly, so a submission     */
/* that passes here never comes back as a 422.                        */
/* ------------------------------------------------------------------ */

function buildBlankDraft() {
  return {
    mode: "create",
    baseVersion: 0,
    id: "",
    plant_type: "",
    substrate: "aerated-universal",
    department_number: "",
    careEnabled: false,
    care_profile: { light: "", watering: "", temperature: "", fertilization: "" },
    phases: [defaultPhase("Fase 1")],
    activePhaseIdx: 0,
    controllers: Object.fromEntries(VARIABLES.map((v) => [v.key, {
      selected_strategy: REQUIRED_DEFAULT_STRATEGY[v.key],
      output_limits: defaultOutputLimits(v.key),
    }])),
    status: null,
    errors: [],
  };
}

function buildDraftFromRecipe(recipe) {
  return {
    mode: "edit",
    baseVersion: recipe.version,
    id: recipe.id,
    plant_type: recipe.plant_type,
    substrate: recipe.substrate,
    department_number: recipe.department_number === null || recipe.department_number === undefined ? "" : String(recipe.department_number),
    careEnabled: !!recipe.care_profile,
    care_profile: recipe.care_profile ? { ...recipe.care_profile } : { light: "", watering: "", temperature: "", fertilization: "" },
    phases: recipe.phases.map((p) => ({
      name: p.name,
      duration_hours: p.duration_hours,
      photoperiod: { start_hour: p.photoperiod.start_hour, duration_hours: p.photoperiod.duration_hours },
      targets: Object.fromEntries(p.targets.map((t) => [t.variable, {
        setpoint: t.setpoint,
        allowed_range: { minimum: t.allowed_range.minimum, maximum: t.allowed_range.maximum },
        safety_range: { minimum: t.safety_range.minimum, maximum: t.safety_range.maximum },
        suggested_phase_dose_milliliters: t.suggested_phase_dose_milliliters,
      }])),
    })),
    activePhaseIdx: 0,
    controllers: Object.fromEntries(recipe.controllers.map((c) => [c.variable, {
      selected_strategy: c.selected_strategy,
      output_limits: { ...c.output_limits },
    }])),
    status: null,
    errors: [],
  };
}

/**
 * Opens the create/edit form in the shared pop-up overlay. `recipe` is
 * required (and used) only for mode "edit". Reuses #modal-overlay so it
 * can replace an already-open recipe pop-up's content in place.
 */
function openRecipeForm(mode, recipe) {
  clearPoll("modal");
  clearPoll("modal-chart");
  STATE.adhocIntervals.forEach(clearInterval);
  STATE.adhocIntervals = [];
  STATE.modalZoneId = null;
  STATE.modalZone = null;
  STATE.modalKind = "recipe-form";
  STATE.recipeForm = mode === "edit" && recipe ? buildDraftFromRecipe(recipe) : buildBlankDraft();
  // Preserve wherever the recipe pop-up's own "Indietro" would have gone
  // (e.g. back into a zone's "Controllo avanzato"), so Annulla/save-success
  // can restore the same chain instead of always falling back to a plain
  // close.
  STATE.recipeForm.formReturnTo = STATE.returnTo || { action: "closeModal" };
  document.getElementById("modal-overlay").classList.remove("hidden");
  renderModal();
}

function closeRecipeForm() {
  STATE.recipeForm = null;
  STATE.modalKind = null;
  document.getElementById("modal-overlay").classList.add("hidden");
}

function addRecipeFormPhase() {
  const draft = STATE.recipeForm;
  if (!draft) return;
  draft.phases.push(defaultPhase(`Fase ${draft.phases.length + 1}`));
  draft.activePhaseIdx = draft.phases.length - 1;
  renderModal();
}

function removeRecipeFormPhase(idx) {
  const draft = STATE.recipeForm;
  if (!draft || draft.phases.length <= 1) return;
  draft.phases.splice(idx, 1);
  draft.activePhaseIdx = Math.min(draft.activePhaseIdx, draft.phases.length - 1);
  renderModal();
}

/**
 * Client-side mirror of backend/app/features/recipes/models.py's
 * PhaseVariableTarget._check_range_chain (and the N/P/K -> Predictive
 * lock enforced at the Edge by RecipeControlSystem::confirm_configuration,
 * see the NUTRIENT_VARIABLES comment above): every rule checked here is
 * checked again server-side, so this exists purely to turn a would-be 422
 * (or, for the N/P/K case, a would-be rejected recipe adoption at the
 * Edge) into a clear Italian message before the request is ever sent.
 */
function validateRecipeForm(draft) {
  const errors = [];
  if (!draft.id.trim()) errors.push("L'id della ricetta è obbligatorio.");
  else if (draft.mode === "create" && !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(draft.id.trim())) {
    errors.push("L'id può contenere solo lettere, numeri, trattini e underscore, e non può iniziare con uno di questi ultimi due.");
  }
  if (!draft.plant_type.trim()) errors.push("La specie/tipo di pianta è obbligatoria.");

  if (draft.careEnabled) {
    CARE_FIELDS.forEach((f) => {
      if (!draft.care_profile[f.key].trim()) errors.push(`Profilo agronomico: il campo "${f.label}" è obbligatorio se il profilo è abilitato.`);
    });
  }

  if (!draft.phases.length) errors.push("Deve essere presente almeno una fase.");
  draft.phases.forEach((phase, pIdx) => {
    const phaseLabel = `Fase ${pIdx + 1} (${phase.name.trim() || "senza nome"})`;
    if (!phase.name.trim()) errors.push(`${phaseLabel}: il nome è obbligatorio.`);
    const duration = Number(phase.duration_hours);
    if (!(duration > 0)) errors.push(`${phaseLabel}: la durata deve essere un numero maggiore di zero.`);
    const startHour = Number(phase.photoperiod.start_hour);
    if (!(startHour >= 0 && startHour < 24)) errors.push(`${phaseLabel}: l'ora di inizio del fotoperiodo deve essere compresa tra 0 e 24 (esclusa).`);
    const photoDuration = Number(phase.photoperiod.duration_hours);
    if (!(photoDuration > 0 && photoDuration <= 24)) errors.push(`${phaseLabel}: la durata del fotoperiodo deve essere maggiore di zero e non superiore a 24 ore.`);

    VARIABLES.forEach((v) => {
      const t = phase.targets[v.key];
      const setpoint = Number(t.setpoint);
      const allowedMin = Number(t.allowed_range.minimum);
      const allowedMax = Number(t.allowed_range.maximum);
      const safetyMin = Number(t.safety_range.minimum);
      const safetyMax = Number(t.safety_range.maximum);
      const dose = Number(t.suggested_phase_dose_milliliters || 0);
      const label = `${phaseLabel} · ${v.label}`;
      if (![setpoint, allowedMin, allowedMax, safetyMin, safetyMax].every(isFinite)) {
        errors.push(`${label}: setpoint, banda consentita e banda di sicurezza sono tutti obbligatori.`);
        return;
      }
      if (!isFinite(dose) || dose < 0) errors.push(`${label}: la dose consigliata per la fase non può essere negativa.`);
      if (!(safetyMin <= allowedMin)) errors.push(`${label}: il minimo della banda di sicurezza deve essere ≤ del minimo della banda consentita.`);
      if (!(allowedMin < allowedMax)) errors.push(`${label}: il minimo della banda consentita deve essere minore del massimo.`);
      if (!(allowedMin <= setpoint && setpoint <= allowedMax)) errors.push(`${label}: il setpoint deve essere compreso nella banda consentita.`);
      if (!(allowedMax <= safetyMax)) errors.push(`${label}: il massimo della banda consentita deve essere ≤ del massimo della banda di sicurezza.`);
      if (!(safetyMin < safetyMax)) errors.push(`${label}: il minimo della banda di sicurezza deve essere minore del massimo.`);
    });
  });

  VARIABLES.forEach((v) => {
    const c = draft.controllers[v.key];
    if (NUTRIENT_VARIABLES.includes(v.key) && c.selected_strategy !== "Predictive") {
      errors.push(`${v.label}: la strategia deve essere Predictive per le variabili nutritive (azoto, fosforo, potassio).`);
    }
    OUTPUT_LIMIT_FIELDS.forEach((f) => {
      const val = Number(c.output_limits[f.key]);
      if (!isFinite(val) || val < 0) errors.push(`${v.label}: il limite di sicurezza "${f.label}" deve essere un numero non negativo.`);
    });
  });

  return errors;
}

/** Builds the exact POST /recipes payload from a validated draft. Version
 * is always computed here (existing.version + 1, or 1 for a new recipe) —
 * never taken from user input, since the backend rejects a non-increasing
 * version outright (see repository.py's RecipeVersionConflict / HTTP 409). */
function buildRecipePayload(draft) {
  const version = draft.mode === "edit" ? draft.baseVersion + 1 : 1;

  const phases = draft.phases.map((phase) => ({
    name: phase.name.trim(),
    duration_hours: Number(phase.duration_hours),
    photoperiod: {
      start_hour: Number(phase.photoperiod.start_hour),
      duration_hours: Number(phase.photoperiod.duration_hours),
    },
    targets: VARIABLES.map((v) => {
      const t = phase.targets[v.key];
      return {
        variable: v.key,
        setpoint: Number(t.setpoint),
        allowed_range: { minimum: Number(t.allowed_range.minimum), maximum: Number(t.allowed_range.maximum) },
        safety_range: { minimum: Number(t.safety_range.minimum), maximum: Number(t.safety_range.maximum) },
        suggested_phase_dose_milliliters: Number(t.suggested_phase_dose_milliliters || 0),
      };
    }),
  }));

  const controllers = VARIABLES.map((v) => {
    const c = draft.controllers[v.key];
    // "selected_strategy vuoto -> default_strategy" fallback from the task
    // spec: structurally the form always has a value here (every draft is
    // initialized with one, and the select for the 3 non-NPK variables
    // always has a selection), but this keeps the one place a blank UI
    // value could theoretically reach this function defensive rather than
    // silently sending an invalid payload.
    const selected = c.selected_strategy || REQUIRED_DEFAULT_STRATEGY[v.key];
    const firstTarget = phases[0].targets.find((t) => t.variable === v.key);
    return {
      variable: v.key,
      input_source: REQUIRED_INPUT_SOURCE[v.key],
      actuator: REQUIRED_ACTUATOR[v.key],
      default_strategy: REQUIRED_DEFAULT_STRATEGY[v.key],
      selected_strategy: selected,
      parameters: buildStrategyParameters(selected, firstTarget),
      unit: VARIABLE_UNIT[v.key],
      output_limits: {
        maximum_water_volume_liters: Number(c.output_limits.maximum_water_volume_liters),
        maximum_pump_duration_seconds: Number(c.output_limits.maximum_pump_duration_seconds),
        water_pump_flow_liters_per_hour: HIDDEN_OUTPUT_LIMIT_DEFAULTS.water_pump_flow_liters_per_hour,
        maximum_dose_per_command_milliliters: Number(c.output_limits.maximum_dose_per_command_milliliters),
        maximum_daily_dose_milliliters: Number(c.output_limits.maximum_daily_dose_milliliters),
        minimum_seconds_between_doses: Number(c.output_limits.minimum_seconds_between_doses),
        ph_settling_time_seconds: HIDDEN_OUTPUT_LIMIT_DEFAULTS.ph_settling_time_seconds,
      },
    };
  });

  return {
    id: draft.id.trim(),
    plant_type: draft.plant_type.trim(),
    substrate: draft.substrate,
    version,
    department_number: draft.department_number === "" ? null : Number(draft.department_number),
    care_profile: draft.careEnabled ? {
      light: draft.care_profile.light.trim(),
      watering: draft.care_profile.watering.trim(),
      temperature: draft.care_profile.temperature.trim(),
      fertilization: draft.care_profile.fertilization.trim(),
    } : null,
    phases,
    controllers,
  };
}

async function submitRecipeForm() {
  const draft = STATE.recipeForm;
  if (!draft || draft.status === "sending") return;

  const errors = validateRecipeForm(draft);
  if (errors.length) {
    draft.errors = errors;
    renderModal();
    return;
  }

  draft.errors = [];
  draft.status = "sending";
  renderModal();

  try {
    const payload = buildRecipePayload(draft);
    const saved = await apiPost("/recipes", payload);
    STATE.recipesById[saved.id] = saved;
    const idx = STATE.recipes.findIndex((r) => r.id === saved.id);
    if (idx >= 0) STATE.recipes[idx] = saved; else STATE.recipes.push(saved);
    if (STATE.view === "recipes") updateRecipeGridOnly();
    showToast(`Ricetta "${saved.id}" salvata come v${saved.version}.`);
    openRecipeModal(saved.id, draft.formReturnTo);
  } catch (err) {
    draft.status = null;
    draft.errors = [err.message || "Salvataggio non riuscito."];
    renderModal();
  }
}

function renderRecipeFormModal() {
  const draft = STATE.recipeForm;
  const wrap = document.getElementById("modal-content");
  if (!draft) { wrap.innerHTML = ""; return; }

  const sending = draft.status === "sending";
  const idx = Math.min(draft.activePhaseIdx, draft.phases.length - 1);
  const phase = draft.phases[idx];

  const saveLabel = sending
    ? "Salvataggio…"
    : draft.mode === "edit"
      ? `Salva come nuova versione (v${draft.baseVersion + 1})`
      : "Crea ricetta (v1)";

  const phaseTabsHtml = draft.phases.map((p, i) => `
    <span class="phase-tab-form ${i === idx ? "active" : ""}">
      <button type="button" class="phase-tab-form-select" data-action="recipe-form-select-phase-tab" data-idx="${i}">${escapeHtml(p.name.trim() || `Fase ${i + 1}`)}</button>
      ${draft.phases.length > 1 ? `<button type="button" class="phase-tab-form-remove" data-action="recipe-form-remove-phase" data-idx="${i}" title="Rimuovi fase">×</button>` : ""}
    </span>
  `).join("");

  const targetRows = VARIABLES.map((v) => {
    const t = phase.targets[v.key];
    const base = `phases.${idx}.targets.${v.key}`;
    return `
      <div class="data-table-row cols-recipe-form">
        <div><div class="var-name">${v.label}</div><div class="var-unit">${v.unit}</div></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.setpoint" value="${escapeAttr(t.setpoint)}"></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.allowed_range.minimum" value="${escapeAttr(t.allowed_range.minimum)}"></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.allowed_range.maximum" value="${escapeAttr(t.allowed_range.maximum)}"></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.safety_range.minimum" value="${escapeAttr(t.safety_range.minimum)}"></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.safety_range.maximum" value="${escapeAttr(t.safety_range.maximum)}"></div>
        <div><input type="number" step="any" class="form-num" data-action="recipe-form-input" data-path="${base}.suggested_phase_dose_milliliters" value="${escapeAttr(t.suggested_phase_dose_milliliters)}"></div>
      </div>
    `;
  }).join("");

  const controllerRows = VARIABLES.map((v) => {
    const c = draft.controllers[v.key];
    const locked = NUTRIENT_VARIABLES.includes(v.key);
    const strategyCell = locked
      ? `<span class="tag-phase" title="Bloccata su Predictive per le variabili nutritive">Predictive (obbligatoria)</span>`
      : `<select class="form-select" data-action="recipe-form-select" data-path="controllers.${v.key}.selected_strategy">
          ${STRATEGIES.map((s) => `<option value="${s}" ${c.selected_strategy === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>`;
    const limitCells = OUTPUT_LIMIT_FIELDS.map((f) => `
      <div>
        <input type="number" step="any" min="0" class="form-num" data-action="recipe-form-input" data-path="controllers.${v.key}.output_limits.${f.key}" value="${escapeAttr(c.output_limits[f.key])}" title="${f.label} (${f.unit})">
      </div>
    `).join("");
    return `
      <div class="data-table-row cols-controller-form">
        <div class="var-name">${v.label}</div>
        <div>${strategyCell}</div>
        ${limitCells}
      </div>
    `;
  }).join("");

  const careBlock = `
    <label class="recipe-form-checkbox">
      <input type="checkbox" data-action="recipe-form-select" data-path="careEnabled" ${draft.careEnabled ? "checked" : ""}>
      <span>Includi profilo agronomico (facoltativo)</span>
    </label>
    ${draft.careEnabled ? `
      <div class="recipe-form-grid">
        ${CARE_FIELDS.map((f) => `
          <div class="form-field">
            <label class="field-label">${f.label}</label>
            <input type="text" class="form-text" data-action="recipe-form-input" data-path="care_profile.${f.key}" value="${escapeAttr(draft.care_profile[f.key])}" placeholder="${f.label}">
          </div>
        `).join("")}
      </div>
    ` : ""}
  `;

  const errorsBlock = draft.errors.length ? `
    <div class="recipe-form-errors">
      <div class="recipe-form-errors-title">Correggi questi punti prima di salvare (${draft.errors.length}):</div>
      <ul>${draft.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>
    </div>
  ` : "";

  wrap.innerHTML = `
    <div class="zone-header" style="--zone-tint:#eef2e4">
      <div style="min-width:0">
        <div class="zone-header-code">
          <span class="code">${draft.mode === "edit" ? "MODIFICA RICETTA" : "NUOVA RICETTA"}</span>
          ${draft.mode === "edit" ? `<span class="recipe-badge">v${draft.baseVersion} → v${draft.baseVersion + 1}</span>` : `<span class="recipe-badge">v1</span>`}
        </div>
        <h2>${escapeHtml(draft.plant_type.trim() || draft.id.trim() || "Nuova ricetta")}</h2>
        <div class="zone-header-meta">
          <span>${draft.mode === "edit" ? "il salvataggio crea sempre una nuova versione: la ricetta non viene sovrascritta in-place" : "compila i campi e crea la prima versione"}</span>
        </div>
      </div>
      <button type="button" class="zone-close" data-action="close-recipe-form">✕</button>
    </div>
    <div style="padding:20px 28px 28px">

      <div class="recipe-form-section">
        <div class="zone-section-title">Dati generali</div>
        <div class="recipe-form-grid">
          <div class="form-field">
            <label class="field-label">Id ricetta${draft.mode === "edit" ? " (non modificabile)" : ""}</label>
            <input type="text" class="form-text" data-action="recipe-form-input" data-path="id" value="${escapeAttr(draft.id)}" placeholder="es. tomato_demo_v1" ${draft.mode === "edit" ? "disabled" : ""}>
          </div>
          <div class="form-field">
            <label class="field-label">Specie / tipo di pianta</label>
            <input type="text" class="form-text" data-action="recipe-form-input" data-path="plant_type" value="${escapeAttr(draft.plant_type)}" placeholder="es. Tomato">
          </div>
          <div class="form-field">
            <label class="field-label">Substrato</label>
            <select class="form-select" data-action="recipe-form-select" data-path="substrate">
              ${Object.entries(SUBSTRATE_LABELS).map(([val, label]) => `<option value="${val}" ${draft.substrate === val ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </div>
          <div class="form-field">
            <label class="field-label">Reparto</label>
            <select class="form-select" data-action="recipe-form-select" data-path="department_number">
              <option value="" ${draft.department_number === "" ? "selected" : ""}>Nessuno (non catalogato)</option>
              ${[1, 2, 3, 4].map((n) => `<option value="${n}" ${draft.department_number === String(n) ? "selected" : ""}>Reparto ${n}</option>`).join("")}
            </select>
          </div>
        </div>
        ${careBlock}
      </div>

      <div class="recipe-form-section">
        <div class="zone-section-title">Fasi di coltivazione</div>
        <div class="phase-tabs-form">
          ${phaseTabsHtml}
          <button type="button" class="btn" data-action="recipe-form-add-phase">+ Nuova fase</button>
        </div>
        <div class="recipe-form-grid" style="margin-top:14px">
          <div class="form-field">
            <label class="field-label">Nome fase</label>
            <input type="text" class="form-text" data-action="recipe-form-input" data-path="phases.${idx}.name" value="${escapeAttr(phase.name)}" placeholder="es. VegetativeGrowth">
          </div>
          <div class="form-field">
            <label class="field-label">Durata fase (ore)</label>
            <input type="number" step="any" min="0" class="form-num" data-action="recipe-form-input" data-path="phases.${idx}.duration_hours" value="${escapeAttr(phase.duration_hours)}">
          </div>
          <div class="form-field">
            <label class="field-label">Fotoperiodo — ora inizio</label>
            <input type="number" step="any" min="0" max="24" class="form-num" data-action="recipe-form-input" data-path="phases.${idx}.photoperiod.start_hour" value="${escapeAttr(phase.photoperiod.start_hour)}">
          </div>
          <div class="form-field">
            <label class="field-label">Fotoperiodo — durata (ore)</label>
            <input type="number" step="any" min="0" max="24" class="form-num" data-action="recipe-form-input" data-path="phases.${idx}.photoperiod.duration_hours" value="${escapeAttr(phase.photoperiod.duration_hours)}">
          </div>
        </div>
        <div class="data-table" style="margin-top:14px;overflow-x:auto">
          <div class="data-table-head cols-recipe-form"><span>VARIABILE</span><span>SETPOINT</span><span>MIN CONSENT.</span><span>MAX CONSENT.</span><span>MIN SICUR.</span><span>MAX SICUR.</span><span>DOSE (mL)</span></div>
          ${targetRows}
        </div>
      </div>

      <div class="recipe-form-section">
        <div class="zone-section-title">Controllori — Strategy e limiti di sicurezza</div>
        <div class="data-table" style="overflow-x:auto">
          <div class="data-table-head cols-controller-form"><span>VARIABILE</span><span>STRATEGIA</span>${OUTPUT_LIMIT_FIELDS.map((f) => `<span>${f.label.toUpperCase()} (${f.unit})</span>`).join("")}</div>
          ${controllerRows}
        </div>
      </div>

      ${errorsBlock}

      <div class="recipe-form-footer">
        <button type="button" class="btn" data-action="recipe-form-cancel" ${sending ? "disabled" : ""}>Annulla</button>
        <button type="button" class="btn btn-primary" data-action="recipe-form-submit" ${sending ? "disabled" : ""}>${saveLabel}</button>
      </div>
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
  STATE.modalKind = "zone";
  STATE.modalTab = entry === "advanced" ? "advanced" : "summary";
  // Opened straight into "Controllo avanzato" (e.g. from the Controllo
  // sidebar list): there is no summary tab to fall back to, so "Indietro"
  // should return to whatever page opened this modal, i.e. close it.
  // Opened on the summary tab: no return target yet — one gets recorded
  // if/when the user later drills into the advanced tab from here.
  STATE.returnTo = entry === "advanced" ? { action: "closeModal" } : null;
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

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  clearPoll("modal");
  clearPoll("modal-chart");
  STATE.adhocIntervals.forEach(clearInterval);
  STATE.adhocIntervals = [];
  STATE.modalZoneId = null;
  STATE.modalZone = null;
  STATE.modalKind = null;
  STATE.currentRecipe = null;
  STATE.recipeForm = null;
}

/**
 * Generic "Indietro" handler for pop-ups: consults the single STATE.returnTo
 * marker recorded when the current pop-up (or sub-view within it) was
 * opened, instead of any button hard-coding its own destination.
 */
function goBack() {
  const target = STATE.returnTo;
  STATE.returnTo = null;
  if (!target || target.action === "closeModal") {
    closeModal();
    return;
  }
  if (target.action === "modalTab") {
    STATE.modalTab = target.tab;
    clearPoll("modal-chart");
    renderModal();
    return;
  }
  if (target.action === "reopenZoneAdvanced") {
    // Recipe pop-up was opened from inside "Controllo avanzato" — go back
    // to that same zone's advanced tab.
    openZoneModal(target.zoneId, "advanced");
    return;
  }
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
  if (STATE.modalKind === "recipe") { renderRecipeModal(); return; }
  if (STATE.modalKind === "recipe-form") { renderRecipeFormModal(); return; }
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
        <button type="button" class="btn" data-action="advanced-back">← Indietro</button>
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
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  ["home", "recipes", "control"].forEach((v) => {
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
    if (e.target.id === "modal-overlay") { closeModal(); return; }

    const closeBtn = e.target.closest('[data-action="close-modal"]');
    if (closeBtn) { closeModal(); return; }

    const openZone = e.target.closest('[data-action="open-zone"], [data-action="open-zone-advanced"]');
    if (openZone) {
      const entry = openZone.dataset.action === "open-zone-advanced" ? "advanced" : "summary";
      openZoneModal(openZone.dataset.zoneId, entry);
      return;
    }

    const openRecipe = e.target.closest('[data-action="open-recipe"]');
    if (openRecipe) {
      // Opened from the Ricette grid: the grid stays visible behind the
      // pop-up, so Indietro just closes it — no view switch involved.
      openRecipeModal(openRecipe.dataset.recipeId, { action: "closeModal" });
      return;
    }

    const openRecipeFormCreate = e.target.closest('[data-action="open-recipe-form-create"]');
    if (openRecipeFormCreate) { openRecipeForm("create"); return; }

    const openRecipeFormEdit = e.target.closest('[data-action="open-recipe-form-edit"]');
    if (openRecipeFormEdit && STATE.currentRecipe) { openRecipeForm("edit", STATE.currentRecipe); return; }

    const closeRecipeFormBtn = e.target.closest('[data-action="close-recipe-form"]');
    if (closeRecipeFormBtn) { closeRecipeForm(); return; }

    const cancelRecipeForm = e.target.closest('[data-action="recipe-form-cancel"]');
    if (cancelRecipeForm) {
      const draft = STATE.recipeForm;
      if (draft && draft.mode === "edit") openRecipeModal(draft.id, draft.formReturnTo);
      else closeRecipeForm();
      return;
    }

    const addPhaseBtn = e.target.closest('[data-action="recipe-form-add-phase"]');
    if (addPhaseBtn) { addRecipeFormPhase(); return; }

    const removePhaseBtn = e.target.closest('[data-action="recipe-form-remove-phase"]');
    if (removePhaseBtn) { removeRecipeFormPhase(Number(removePhaseBtn.dataset.idx)); return; }

    const selectPhaseTabForm = e.target.closest('[data-action="recipe-form-select-phase-tab"]');
    if (selectPhaseTabForm) {
      if (STATE.recipeForm) STATE.recipeForm.activePhaseIdx = Number(selectPhaseTabForm.dataset.idx);
      renderModal();
      return;
    }

    const submitRecipeFormBtn = e.target.closest('[data-action="recipe-form-submit"]');
    if (submitRecipeFormBtn && !submitRecipeFormBtn.disabled) { submitRecipeForm(); return; }

    const openAddSector = e.target.closest('[data-action="open-add-sector"]');
    if (openAddSector) {
      STATE.addSectorForm = {
        dept: Number(openAddSector.dataset.dept),
        sector: Number(openAddSector.dataset.sector),
        name: "",
        species: "",
        status: null,
        error: null,
      };
      renderHome();
      return;
    }

    const cancelAddSector = e.target.closest('[data-action="cancel-add-sector"]');
    if (cancelAddSector) { STATE.addSectorForm = null; renderHome(); return; }

    const submitAddSectorBtn = e.target.closest('[data-action="submit-add-sector"]');
    if (submitAddSectorBtn && !submitAddSectorBtn.disabled) { submitAddSector(); return; }

    const recipeBack = e.target.closest('[data-action="recipe-back"]');
    if (recipeBack) { goBack(); return; }

    const phaseTab = e.target.closest('[data-action="select-phase-tab"]');
    if (phaseTab) { STATE.recipeDetailPhaseIdx = Number(phaseTab.dataset.idx); renderModal(); return; }

    const openAdv = e.target.closest('[data-action="open-advanced-tab"]');
    if (openAdv) {
      // Drilling into "Controllo avanzato" from the summary tab of this
      // same modal: record that as the return target for its back button.
      STATE.returnTo = { action: "modalTab", tab: "summary" };
      STATE.modalTab = "advanced";
      renderModal();
      setPoll("modal-chart", refreshChartData, CHART_POLL_MS);
      return;
    }

    const advancedBack = e.target.closest('[data-action="advanced-back"]');
    if (advancedBack) { goBack(); return; }

    const advanceBtn = e.target.closest('[data-action="advance-phase"]');
    if (advanceBtn && !advanceBtn.disabled) { advancePhase(); return; }

    const confirmBtn = e.target.closest('[data-action="confirm-strategy"]');
    if (confirmBtn) { sendStrategyChange(confirmBtn.dataset.variable); return; }

    const hintCell = e.target.closest('[data-action="show-recipe-hint"]');
    if (hintCell) { STATE.recipeHintVisible = true; renderModal(); return; }

    const openRecipeFromZone = e.target.closest('[data-action="open-recipe-from-zone"]');
    if (openRecipeFromZone) {
      const zone = STATE.modalZone;
      const rid = zone ? zone.active_recipe_id : null;
      // Opened from inside "Controllo avanzato": Indietro on the recipe
      // pop-up should return to this same zone's advanced tab.
      if (rid && zone) openRecipeModal(rid, { action: "reopenZoneAdvanced", zoneId: zone.id });
      return;
    }
  });

  document.addEventListener("change", (e) => {
    const strategySelect = e.target.closest('[data-action="strategy-select"]');
    if (strategySelect) { onStrategyDraftChange(strategySelect.dataset.variable, strategySelect.value); return; }

    const filterSelect = e.target.closest('[data-action="control-filter"]');
    if (filterSelect) { STATE.controlFilters[filterSelect.dataset.filter] = filterSelect.value; renderControl(); return; }

    if (e.target.id === "chart-variable-select") { onChartVariableChange(e.target.value); return; }

    // Recipe form selects/checkboxes: bound by dotted data-path into
    // STATE.recipeForm (see getPath/setPath). Re-rendered on change since
    // selects/checkboxes don't fight cursor focus the way text inputs do.
    const recipeFormSelect = e.target.closest('[data-action="recipe-form-select"]');
    if (recipeFormSelect && STATE.recipeForm) {
      const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      setPath(STATE.recipeForm, recipeFormSelect.dataset.path, value);
      renderModal();
      return;
    }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "recipe-search") {
      STATE.recipeQuery = e.target.value;
      updateRecipeGridOnly();
    }

    const addSectorField = e.target.closest('[data-action="add-sector-field"]');
    if (addSectorField && STATE.addSectorForm) {
      // Write straight into STATE only — no re-render, so typing doesn't
      // fight the innerHTML refresh for cursor position/focus.
      STATE.addSectorForm[addSectorField.dataset.field] = addSectorField.value;
    }

    // Recipe form text/number inputs: same "write straight into STATE,
    // don't re-render" pattern, keyed by a dotted data-path so one handler
    // covers every field in the form (top-level, phase targets, controller
    // limits) instead of one bespoke handler per field.
    const recipeFormInput = e.target.closest('[data-action="recipe-form-input"]');
    if (recipeFormInput && STATE.recipeForm) {
      setPath(STATE.recipeForm, recipeFormInput.dataset.path, recipeFormInput.value);
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
