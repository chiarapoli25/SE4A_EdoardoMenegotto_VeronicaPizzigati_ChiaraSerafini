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
// The Allarmi page pulls the recent event log for every registered zone on
// each tick (see loadZoneEventsBundle) — deliberately slower than the 6s
// zones poll, since it is a diagnostics page opened occasionally, not the
// always-on Home view.
const ALERTS_POLL_MS = 20000;
const CHART_POLL_MS = 15000;
const COMMAND_POLL_MS = 1500;
const COMMAND_TIMEOUT_MS = 20000;

// Frontend-only rule (not enforced by the backend): "Fai uscire" stays
// disabled until at least this much time has passed since quarantined_at.
// Placeholder value — change this constant to tune it, nothing else to touch.
const QUARANTINE_MIN_RELEASE_MS = 24 * 60 * 60 * 1000;

// The quarantine zone id is fixed: department 5 has exactly one sector
// (backend now enforces sector_number=1 there, see zones/routes.py), so
// there is only ever one valid destination for PATCH /plants/{id}/quarantine.
const QUARANTINE_ZONE_ID = "r5-s1";

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

/**
 * Real phase-name sequence used by each of the 4 production departments'
 * recipes today, per config/recipe_catalog/profiles.json's phase_sequences
 * (department -> sequence mapping is catalog.py's
 * _DEPARTMENT_PHASE_SEQUENCE: 1->foliage, 2->flowering, 3->succulent,
 * 4->fruiting). Confirmed against the live catalog via GET /recipes across
 * all 4 departments — no recipe in the catalog deviates from its
 * department's sequence. RecipePhase.name has no enum on the backend; this
 * is a frontend-only restriction to keep new/edited recipes consistent
 * with the existing catalog's phase vocabulary, so it is enforced only by
 * what the phase-name <select> below offers — no server-side validation
 * was added for it.
 */
const PHASE_SEQUENCE_BY_DEPARTMENT = {
  "1": ["Avvio e attecchimento", "Crescita vegetativa", "Mantenimento fogliare", "Riposo vegetativo"],
  "2": ["Avvio e attecchimento", "Crescita vegetativa", "Fioritura", "Riposo post-fioritura"],
  "3": ["Avvio e attecchimento", "Crescita vegetativa", "Maturazione", "Riposo asciutto"],
  "4": ["Avvio e attecchimento", "Crescita vegetativa", "Fioritura e allegagione", "Produzione e maturazione"],
};

function phaseNameOptionsFor(departmentNumber) {
  return PHASE_SEQUENCE_BY_DEPARTMENT[departmentNumber] || PHASE_SEQUENCE_BY_DEPARTMENT["1"];
}

/** Re-syncs every phase's name to the new department's sequence, matched
 * by position (phase 1 -> that department's phase 1 name, etc.) — keeps
 * the phase-name <select> always showing a valid, real catalog value
 * after the department changes, with no silent/invalid selection. */
function remapPhaseNamesForDepartment(draft, departmentNumber) {
  const names = phaseNameOptionsFor(departmentNumber);
  draft.phases.forEach((phase, i) => {
    phase.name = names[Math.min(i, names.length - 1)];
  });
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

  // "Rimuovi settore" (zone summary tab, bottom of the pop-up): null = plain
  // button; true = inline confirmation panel open; the explicit second step
  // before DELETE actually fires, same two-phase principle as addSectorForm.
  deleteZoneConfirm: false,
  deleteZoneStatus: null, // "sending" | null
  deleteZoneError: null,

  // Plants of the zone currently open in the modal (production-sector
  // detail): GET /plants?zone_id=<zone>, refreshed on every "modal" poll
  // tick alongside telemetry/actuators.
  modalPlants: [],
  modalPlantsLoaded: false,
  addPlantStatus: null, // "sending" | null
  addPlantError: null,

  // Quarantine detail pop-up (STATE.modalKind = "quarantine"): plants
  // currently in r5-s1, is_quarantined=true.
  quarantinePlants: [],
  quarantinePlantsLoaded: false,

  // Per-plant transient UI state, keyed by plant id — shared between the
  // zone-detail plant list and the quarantine detail tiles, since plant ids
  // are unique across both. Each entry may carry: quarantineFormOpen,
  // quarantineReason, quarantineStatus, quarantineError (Metti in
  // quarantena); releaseStatus, releaseError (Fai uscire); deleteConfirm,
  // deleteStatus, deleteError (Rimuovi definitivamente).
  plantUi: {},

  // Single shared "where did I come from" marker, captured once at the
  // moment a pop-up (or a sub-view inside one) is opened. Back buttons
  // consult this instead of hard-coding a destination, so the same
  // mechanism serves every pop-up's "Indietro" button.
  returnTo: null,

  adhocIntervals: [],

  // Dedicated "Allarmi" page (STATE.view = "alerts"): situazioni attive +
  // registro eventi + movimenti in quarantena. `zoneBundle` holds the raw
  // GET /zones/{id}/events response (ascending, oldest first — same shape
  // the backend already returns) for every zone, refetched on each poll
  // tick; every section below is a pure function derived from it plus
  // STATE.zones / STATE.quarantine, so nothing else needs to be cached.
  alertsPage: {
    loaded: false,
    zoneBundle: [], // [{ zoneId, events }]
  },
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
const apiDelete = (path) => apiRequest("DELETE", path, {});

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

/** Human-readable elapsed time since `iso` (e.g. "2g 4h", "5h 12m", "8m"),
 * recomputed from Date.now() on every call so re-rendering on a poll tick
 * is enough to keep it live — no page reload needed. */
function fmtElapsed(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "—";
  const totalMinutes = Math.floor(Math.max(0, Date.now() - then) / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}g ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

/** True once at least QUARANTINE_MIN_RELEASE_MS has passed since `iso`. */
function quarantineReleaseAllowed(iso) {
  if (!iso) return true;
  const then = new Date(iso).getTime();
  if (isNaN(then)) return true;
  return Date.now() - then >= QUARANTINE_MIN_RELEASE_MS;
}

/** Remaining wait before "Fai uscire" becomes enabled, human-readable. */
function quarantineReleaseWait(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const remainingMs = QUARANTINE_MIN_RELEASE_MS - (Date.now() - then);
  if (remainingMs <= 0) return "";
  const totalMinutes = Math.ceil(remainingMs / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
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
    updateNavAlertsBadge();
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

  setPageTitle("Reparti della serra", `${total} settori registrati (massimo 9: 4 reparti produttivi × 2 settori + quarantena × 1) · ${online} online`);

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
    // departments — r5-s1 is its only physical sector, the fixed technical
    // destination for PATCH /plants/{id}/quarantine. Shown as a real
    // "settore" box all the same (same white card, border, padding, hover
    // and arrow as every other department's sector row via .sector-row),
    // so the two-level structure — tinted dept card containing white
    // sector box(es) — stays visually consistent across every department;
    // only the content inside this one differs (quarantine stats instead
    // of species/phase/operational state), and it's this inner box that's
    // clickable, exactly like a production sector row.
    return `
      <div class="dept-card" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
        <div class="dept-card-head">
          <span class="dept-code">REPARTO ${n}</span>
        </div>
        <div class="dept-title">${escapeHtml(name)}</div>
        <div class="sector-list sector-list-top">
          <div class="sector-row" data-action="open-quarantine">
            <div class="sector-row-body">
              <div class="sector-row-top">
                <span class="sector-num">SETTORE 1</span>
              </div>
              <div class="quarantine-box" id="quarantine-box"></div>
            </div>
            <span class="sector-row-arrow">→</span>
          </div>
        </div>
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
  updateNavAlertsBadge();
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
    // Keeps the full plant list (not just count/lastEntry) so the Home
    // alerts panel can compute the "pianta senza settore di origine" alarm
    // client-side without a second network call.
    if (!plants.length) return { count: 0, lastEntry: null, plants: [] };
    let last = null;
    plants.forEach((p) => {
      if (p.quarantined_at && (!last || new Date(p.quarantined_at) > new Date(last))) last = p.quarantined_at;
    });
    return { count: plants.length, lastEntry: last, plants };
  } catch (e) {
    return { count: null, lastEntry: null, plants: [] };
  }
}

/**
 * Quarantined plants whose home_zone_id no longer resolves to any zone in
 * STATE.zones — e.g. after their origin sector was deleted (see DELETE
 * /zones/{id}, which deliberately leaves plants pointing at a since-removed
 * zone rather than blocking the deletion). Computed entirely client-side
 * from data already fetched for this page (STATE.zones, STATE.quarantine
 * .plants) — no extra backend call.
 */
function computeOrphanQuarantinePlants() {
  const plants = (STATE.quarantine && STATE.quarantine.plants) || [];
  const zoneIds = new Set(STATE.zones.map((z) => z.id));
  return plants.filter((p) => !zoneIds.has(p.home_zone_id));
}

function renderAlertsPanel() {
  const el = document.getElementById("alerts-list");
  if (!el) return;
  if (!STATE.homeExtrasLoaded) {
    el.innerHTML = '<div class="empty-note">Caricamento…</div>';
    return;
  }
  const alerts = STATE.alerts || [];
  const orphanPlants = computeOrphanQuarantinePlants();

  if (!alerts.length && !orphanPlants.length) {
    el.innerHTML = '<div class="empty-note">Nessun allarme attivo: tutti i settori registrati sono Nominal.</div>';
    return;
  }

  const zoneAlertsHtml = alerts.map((a) => {
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

  // Third, client-computed category: plants stuck in quarantine because
  // their origin sector no longer exists. Distinct title from the zone
  // alerts above but the same visual shape, so it reads as one more entry
  // in the same list rather than a separate widget.
  const orphanHtml = orphanPlants.map((p) => {
    return `
      <div class="alert-item">
        <span class="alert-bar" style="background:${OP_META.EmergencyLockdown.color}"></span>
        <div><div class="alert-title">Pianta senza settore di origine</div><div class="alert-detail">${escapeHtml(p.species)} (${escapeHtml(p.id)}) · in quarantena da ${fmtElapsed(p.quarantined_at)}</div></div>
      </div>
    `;
  }).join("");

  el.innerHTML = zoneAlertsHtml + orphanHtml;
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
/* Allarmi view — dedicated page (STATE.view = "alerts")              */
/*                                                                    */
/* Three sections, all built from real backend data, nothing mocked: */
/*  - Situazioni attive: zones currently offline or not Nominal, plus */
/*    quarantined plants whose origin sector was deleted (grouped by  */
/*    origin) — same underlying data as the Home sidebar widgets      */
/*    above, just not capped to a top-6 preview.                      */
/*  - Registro eventi: real EdgeEvent rows (GET /zones/{id}/events)   */
/*    across every zone, filtered to the types that actually signal a */
/*    problem (FaultDetected, CommandFailed, StateChanged into        */
/*    Degraded/EmergencyLockdown) — recoveries back to Nominal and    */
/*    routine bookkeeping events (recipe/strategy/simulation changes) */
/*    are deliberately left out.                                      */
/*  - Piante in osservazione: the full quarantined-plant roster right */
/*    now, purely informational (no severity), from the same /plants  */
/*    call as the Home quarantine box — a snapshot, not a history of  */
/*    entries/exits (there is no backend endpoint for that).          */
/* ------------------------------------------------------------------ */

/** Decodes a zone id back into "R{dept}·S{sector}" even after the zone
 * itself has been deleted — every zone id in this app follows the fixed
 * "r{dept}-s{sector}" convention (see addSectorForm/QUARANTINE_ZONE_ID), so
 * this is reading a real naming convention, not guessing. */
function zoneCodeFromId(zoneId) {
  const m = /^r(\d+)-s(\d+)$/i.exec(zoneId || "");
  return m ? `R${m[1]}·S${m[2]}` : (zoneId || "—");
}

/** Zones worth surfacing on the Allarmi page: not Nominal, or not
 * currently reachable from the Edge (independent conditions — an offline
 * zone keeps whatever operational_state it last reported). */
function computeFlaggedZones(zones) {
  return zones.filter((z) => z.status !== "online" || z.operational_state !== "Nominal");
}

/** Same orphan plants as the Home alarm (computeOrphanQuarantinePlants),
 * grouped by their now-nonexistent origin zone so plants that lost the
 * same sector share one card, matching how the situation actually reads:
 * "this sector is gone" is one fact, not one fact per plant. */
function computeOrphanQuarantineGroups() {
  const byOrigin = {};
  computeOrphanQuarantinePlants().forEach((p) => {
    (byOrigin[p.home_zone_id] = byOrigin[p.home_zone_id] || []).push(p);
  });
  return Object.keys(byOrigin).sort().map((originId) => ({ originId, plants: byOrigin[originId] }));
}

/** Cards in "Situazioni attive" = flagged zones + orphan groups — cheap
 * enough to recompute on demand (used by the nav sidebar badge too, which
 * needs a count without paying for a full page render). */
function computeActiveSituationsCount() {
  return computeFlaggedZones(STATE.zones).length + computeOrphanQuarantineGroups().length;
}

function updateNavAlertsBadge() {
  const el = document.getElementById("nav-alerts-badge");
  if (!el) return;
  const n = computeActiveSituationsCount();
  el.textContent = n > 0 ? String(n) : "";
  el.classList.toggle("hidden", n === 0);
}

/** Pairs each flagged zone with its most recent event (if any) — the same
 * "last event explains the current state" idea as Home's loadAlerts, just
 * reading from the already-fetched zoneBundle instead of a second request
 * per zone. */
function computeActiveZoneAlerts() {
  const eventsByZone = Object.fromEntries(
    STATE.alertsPage.zoneBundle.map((b) => [b.zoneId, b.events])
  );
  return computeFlaggedZones(STATE.zones).map((zone) => {
    const events = eventsByZone[zone.id] || [];
    return { zone, lastEvent: events.length ? events[events.length - 1] : null };
  });
}

/** The real Edge event types that indicate a problem — see
 * edge/src/backend/http_backend_client.cpp (uploads_from_event) for the
 * full event_type catalogue. StateChanged is only alarm-worthy when it
 * lands on a bad state: a transition back to Nominal is a recovery, not a
 * situation to report here. */
function isAlarmEvent(ev) {
  if (ev.event_type === "FaultDetected" || ev.event_type === "CommandFailed") return true;
  if (ev.event_type === "StateChanged") {
    const cur = ev.payload && ev.payload.current_state;
    return cur === "Degraded" || cur === "EmergencyLockdown";
  }
  return false;
}

function eventBadgeMeta(ev) {
  if (ev.event_type === "FaultDetected") {
    return { badge: "GUASTO", color: OP_META.Degraded.color, bg: "rgba(201,128,63,.14)" };
  }
  if (ev.event_type === "CommandFailed") {
    return { badge: "COMANDO KO", color: "#a58a5e", bg: "rgba(201,128,63,.08)" };
  }
  if (ev.payload && ev.payload.current_state === "EmergencyLockdown") {
    return { badge: "EMERGENCYLOCKDOWN", color: OP_META.EmergencyLockdown.color, bg: "rgba(193,90,74,.14)" };
  }
  return { badge: "DEGRADED", color: OP_META.Degraded.color, bg: "rgba(201,128,63,.14)" };
}

/** Title/detail text built entirely from the event's own real payload
 * fields (component/rule/diagnostic for faults, actuator/diagnostic for
 * failed commands, previous_state/current_state/reason for state
 * transitions) — nothing here is invented per event, only the wording
 * around the real values is fixed. */
function eventTitleDetail(ev) {
  const p = ev.payload || {};
  if (ev.event_type === "FaultDetected") {
    const comp = p.component ? String(p.component).replace(/_/g, " ") : "componente sconosciuto";
    return {
      title: `Guasto rilevato: ${comp}`,
      detail: p.diagnostic || "Nessun dettaglio disponibile per questo guasto.",
    };
  }
  if (ev.event_type === "CommandFailed") {
    const act = p.actuator ? String(p.actuator).replace(/_/g, " ") : "attuatore sconosciuto";
    return {
      title: `Comando non eseguito: ${act}`,
      detail: p.diagnostic || "Nessun dettaglio disponibile per questo comando.",
    };
  }
  const cur = p.current_state || "?";
  return {
    title: `Settore passato in ${cur}`,
    detail: p.reason || "Nessun motivo registrato per questa transizione.",
  };
}

/** Merges every zone's event list, keeps only the alarm-worthy ones, most
 * recent first. Zones deleted since the fetch are skipped (their id has no
 * match left in STATE.zones) — a rare edge case, and there is no
 * department/species left to show for them anyway. */
function computeEventLogEntries(limitTotal) {
  const entries = [];
  STATE.alertsPage.zoneBundle.forEach(({ zoneId, events }) => {
    const zone = STATE.zones.find((z) => z.id === zoneId);
    if (!zone) return;
    events.forEach((ev) => {
      if (isAlarmEvent(ev)) entries.push({ zone, event: ev });
    });
  });
  entries.sort((a, b) => new Date(b.event.recorded_at) - new Date(a.event.recorded_at));
  return limitTotal ? entries.slice(0, limitTotal) : entries;
}

function activeZoneCardMeta(zone) {
  if (zone.operational_state === "EmergencyLockdown") {
    return {
      badge: "EMERGENCYLOCKDOWN", color: OP_META.EmergencyLockdown.color, bg: "rgba(193,90,74,.14)",
      tone: "danger",
      title: "Settore bloccato in emergenza",
      staticDetail: "Il ciclo di controllo è sospeso: nessun comando viene applicato finché lo stato non torna Nominal.",
    };
  }
  if (zone.status !== "online") {
    return {
      badge: "SETTORE OFFLINE", color: OP_META.EmergencyLockdown.color, bg: "rgba(193,90,74,.14)",
      tone: "danger",
      title: "Connessione con l'Edge persa",
      staticDetail: "Nessun dato in arrivo: i valori mostrati altrove per questo settore sono l'ultima lettura nota, non lo stato reale.",
    };
  }
  return {
    badge: "DEGRADED", color: OP_META.Degraded.color, bg: "rgba(201,128,63,.14)",
    tone: "warn",
    title: "Settore in stato Degraded",
    staticDetail: "Funzionamento ridotto: se la condizione persiste per più cicli di controllo scatta l'escalation a EmergencyLockdown.",
  };
}

function renderActiveZoneCard({ zone, lastEvent }) {
  const meta = activeZoneCardMeta(zone);
  const deptName = zone.department_name || DEPT_FALLBACK_NAMES[zone.department_number] || "";
  // The static per-state explanation above is always true; swap in the
  // real reason from this zone's last event when that event is actually
  // what produced the CURRENT badge — only for EmergencyLockdown/Degraded,
  // never for "SETTORE OFFLINE": once a zone stops reporting, its last
  // event could be anything (even an old recovery to Nominal, as here),
  // and showing it as if it explains "why offline" would be misleading —
  // the honest answer for offline is exactly the static text above.
  let detail = meta.staticDetail;
  if ((meta.badge === "EMERGENCYLOCKDOWN" || meta.badge === "DEGRADED")
      && lastEvent && lastEvent.event_type === "StateChanged" && lastEvent.payload
      && lastEvent.payload.current_state === zone.operational_state) {
    detail = lastEvent.payload.reason || detail;
  }
  const connHint = zone.status === "online" ? "edge online · telemetria in arrivo" : "controllo locale in autonomia";
  return `
    <article class="alert-card clickable tone-${meta.tone}" data-action="open-zone" data-zone-id="${escapeAttr(zone.id)}">
      <div class="alert-card-head">
        <span class="pill" style="color:${meta.color};background:${meta.bg}"><span class="pill-dot"></span>${meta.badge}</span>
        <span class="zone-code">${zoneLabel(zone)}</span>
        <span class="sector-row-arrow">→</span>
      </div>
      <div>
        <div class="alert-card-title">${escapeHtml(meta.title)}</div>
        <div class="alert-card-detail">${escapeHtml(detail)}</div>
      </div>
      <div class="alert-card-tags">
        <span class="tag-phase">REPARTO ${zone.department_number} · ${escapeHtml(deptName)}</span>
        ${zone.plant_species ? `<span class="tag-phase">${escapeHtml(zone.plant_species)}</span>` : ""}
        <span class="tag-phase">${escapeHtml(plantCountLabel(zone.id))}</span>
      </div>
      <div class="alert-card-foot">
        <span class="alert-card-foot-hint">${escapeHtml(connHint)}</span>
        <button type="button" class="btn" data-action="open-zone" data-zone-id="${escapeAttr(zone.id)}">Apri settore</button>
      </div>
    </article>
  `;
}

function renderOrphanPlantRow(p) {
  const ui = STATE.plantUi[p.id] || {};
  const dSending = ui.deleteStatus === "sending";
  return `
    <div class="alert-plant-unit">
      <div class="alert-plant-row">
        <span class="plant-row-id mono">${escapeHtml(p.id)}</span>
        <span>${escapeHtml(p.species)}</span>
        <span class="alert-plant-age mono">in quarantena da ${fmtElapsed(p.quarantined_at)}</span>
      </div>
      ${renderPlantRemoveBlock(p, ui, dSending)}
    </div>
  `;
}

/**
 * One card per deleted origin sector, listing every plant that lost it.
 * "Fai uscire" is deliberately not offered here: PATCH .../quarantine
 * already returns 409 for exactly this case (home zone gone), so the only
 * real way out — already implemented, same component as the quarantine
 * pop-up — is "Rimuovi definitivamente" per plant.
 */
function renderOrphanGroupCard(group) {
  const origin = zoneCodeFromId(group.originId);
  const n = group.plants.length;
  const species = uniqueSorted(group.plants.map((p) => p.species));
  return `
    <article class="alert-card tone-quarantine">
      <div class="alert-card-head">
        <span class="pill" style="color:#a06a37;background:rgba(201,128,63,.14)"><span class="pill-dot"></span>QUARANTENA ORFANA</span>
      </div>
      <div>
        <div class="alert-card-title">${n} piant${n === 1 ? "a" : "e"} senza settore di origine</div>
        <div class="alert-card-detail">Il settore di origine <b class="mono">${escapeHtml(origin)}</b> è stato cancellato: ${n === 1 ? "questa pianta non può" : "queste piante non possono"} più rientrare da nessuna parte. L'unica uscita è la rimozione definitiva.</div>
      </div>
      <div class="alert-card-tags">
        <span class="tag-phase">REPARTO 5 · Quarantena</span>
        ${species.map((s) => `<span class="tag-phase">${escapeHtml(s)}</span>`).join("")}
      </div>
      <div class="alert-plant-list">
        ${group.plants.map((p) => renderOrphanPlantRow(p)).join("")}
      </div>
    </article>
  `;
}

function renderActiveSituationsSection() {
  const zoneAlerts = computeActiveZoneAlerts();
  const orphanGroups = computeOrphanQuarantineGroups();
  const total = zoneAlerts.length + orphanGroups.length;
  const body = total === 0
    ? '<div class="empty-note">Nessuna situazione attiva: tutti i settori registrati sono Nominal e nessuna pianta in quarantena ha perso il proprio settore di origine.</div>'
    : `<div class="alerts-grid">${zoneAlerts.map(renderActiveZoneCard).join("")}${orphanGroups.map(renderOrphanGroupCard).join("")}</div>`;
  return `
    <section class="alerts-section">
      <div class="alerts-section-head">
        <h2>Situazioni attive</h2>
        ${total > 0 ? `<span class="alerts-section-badge">${total} DA RISOLVERE</span>` : ""}
      </div>
      <p class="alerts-section-hint">Problemi in corso adesso, letti dallo stato corrente dei settori e delle piante. Non hanno un orario perché non sono ancora finiti.</p>
      ${body}
    </section>
  `;
}

function renderEventLogRow({ zone, event }) {
  const meta = eventBadgeMeta(event);
  const { title, detail } = eventTitleDetail(event);
  const d = new Date(event.recorded_at);
  const valid = !isNaN(d.getTime());
  const time = valid ? d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "—";
  const date = valid ? d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" }) : "—";
  const deptName = zone.department_name || DEPT_FALLBACK_NAMES[zone.department_number] || "";
  return `
    <div class="event-row" data-action="open-zone" data-zone-id="${escapeAttr(zone.id)}">
      <div class="event-time"><div class="t mono">${time}</div><div class="d mono">${date}</div></div>
      <span class="pill event-badge" style="color:${meta.color};background:${meta.bg}"><span class="pill-dot"></span>${meta.badge}</span>
      <div class="event-body">
        <div class="event-title">${escapeHtml(title)}</div>
        <div class="event-detail">${escapeHtml(detail)}</div>
      </div>
      <div class="event-meta">
        <div class="zone mono">${zoneLabel(zone)}</div>
        <div class="dept mono">REPARTO ${zone.department_number} · ${escapeHtml(deptName)}</div>
        ${zone.plant_species ? `<div class="species">${escapeHtml(zone.plant_species)}</div>` : ""}
      </div>
      <span class="sector-row-arrow">→</span>
    </div>
  `;
}

function renderEventLogSection() {
  const entries = computeEventLogEntries(40);
  const body = entries.length
    ? `<div class="event-log">${entries.map(renderEventLogRow).join("")}</div>`
    : '<div class="empty-note">Nessun evento registrato.</div>';
  return `
    <section class="alerts-section">
      <div class="alerts-section-head">
        <h2>Registro eventi</h2>
        <span class="alerts-section-badge muted">DAL PIÙ RECENTE</span>
      </div>
      <p class="alerts-section-hint">Cose realmente accadute, con l'orario in cui sono state registrate. Le transizioni verso stati buoni non compaiono qui.</p>
      ${body}
    </section>
  `;
}

function renderQuarantineLogRow(p) {
  const originZone = STATE.zones.find((z) => z.id === p.home_zone_id);
  const originLabel = originZone ? zoneLabel(originZone) : zoneCodeFromId(p.home_zone_id);
  return `
    <div class="quarantine-log-row">
      <div class="quarantine-log-code mono">${escapeHtml(p.id)}</div>
      <div class="quarantine-log-main">
        <div class="quarantine-log-species">${escapeHtml(p.species)}</div>
        <div class="quarantine-log-reason">${escapeHtml(p.quarantine_reason || "—")}</div>
      </div>
      <div class="quarantine-log-origin mono">da ${escapeHtml(originLabel)}</div>
      <div class="quarantine-log-age">
        <div class="v mono">${fmtElapsed(p.quarantined_at)}</div>
        <div class="l">in osservazione</div>
      </div>
    </div>
  `;
}

function renderQuarantineLogSection() {
  const plants = ((STATE.quarantine && STATE.quarantine.plants) || [])
    .slice()
    .sort((a, b) => new Date(a.quarantined_at || 0) - new Date(b.quarantined_at || 0));
  const body = plants.length
    ? `<div class="quarantine-log">${plants.map(renderQuarantineLogRow).join("")}</div>`
    : '<div class="empty-note">Nessuna pianta attualmente in quarantena.</div>';
  return `
    <section class="alerts-section">
      <div class="alerts-section-head">
        <h2 style="color:var(--ink-soft)">Piante in osservazione</h2>
        <span class="alerts-section-badge muted quarantine">INFORMATIVO</span>
      </div>
      <p class="alerts-section-hint">Chi è attualmente in quarantena e perché, con il tempo trascorso da quando è entrato. Non è uno storico di ingressi e uscite — non esiste un endpoint che lo fornisca — solo la situazione presente. È un fatto amministrativo, non un problema: nessuna severità associata.</p>
      ${body}
    </section>
  `;
}

function renderAlertsView() {
  const container = document.getElementById("view-alerts");
  if (!STATE.alertsPage.loaded) {
    setPageTitle("Allarmi", "Caricamento…");
    container.innerHTML = '<div class="empty-note">Caricamento…</div>';
    return;
  }

  const activeCount = computeActiveSituationsCount();
  const eventCount = computeEventLogEntries().length;
  const quarantineCount = ((STATE.quarantine && STATE.quarantine.plants) || []).length;
  setPageTitle(
    "Allarmi",
    `${activeCount} situazion${activeCount === 1 ? "e attiva" : "i attive"} · ` +
    `${eventCount} event${eventCount === 1 ? "o" : "i"} in registro · ` +
    `${quarantineCount} piant${quarantineCount === 1 ? "a" : "e"} in quarantena`
  );

  container.innerHTML =
    renderActiveSituationsSection() +
    renderEventLogSection() +
    renderQuarantineLogSection();

  updateNavAlertsBadge();
}

async function loadZoneEventsBundle(zones, limit) {
  return Promise.all(zones.map(async (z) => {
    try {
      const events = await apiGet(`/zones/${encodeURIComponent(z.id)}/events`, { limit });
      return { zoneId: z.id, events };
    } catch (e) {
      return { zoneId: z.id, events: [] };
    }
  }));
}

async function tickAlertsView() {
  if (STATE.view !== "alerts") return;
  try {
    const zones = await apiGet("/zones");
    STATE.zones = zones;
    const [quarantine, bundle, plantCounts] = await Promise.all([
      loadQuarantineStats(),
      loadZoneEventsBundle(zones, 50),
      loadPlantCounts(zones),
    ]);
    if (STATE.view !== "alerts") return; // navigated away while requests were in flight
    STATE.quarantine = quarantine;
    STATE.alertsPage.zoneBundle = bundle;
    STATE.alertsPage.loaded = true;
    STATE.plantCounts = plantCounts;
    STATE.homeExtrasLoaded = true;
    renderAlertsView();
  } catch (e) {
    console.error("failed to refresh alerts view", e);
    if (STATE.view !== "alerts") return;
    STATE.alertsPage.loaded = true;
    renderAlertsView();
  }
  updateNavAlertsBadge();
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
        <span class="recipe-badge">Versione ${r.version}</span>
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
          <span class="recipe-badge">Versione ${r.version}</span>
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
    // Always a real department 1-4 (no blank option in the form — see
    // renderRecipeFormModal's department <select>).
    department_number: "1",
    careEnabled: false,
    care_profile: { light: "", watering: "", temperature: "", fertilization: "" },
    phases: [defaultPhase(phaseNameOptionsFor("1")[0])],
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
    // The backend schema allows a null department_number (an uncatalogued
    // recipe), but the form's department <select> no longer offers a blank
    // option (see requirement above) — a legacy recipe with no department
    // just opens pre-set to department 1 rather than showing nothing.
    department_number: recipe.department_number === null || recipe.department_number === undefined ? "1" : String(recipe.department_number),
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
  const names = phaseNameOptionsFor(draft.department_number);
  const name = names[Math.min(draft.phases.length, names.length - 1)];
  draft.phases.push(defaultPhase(name));
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
  // Defensive, not the primary mechanism: the department <select> never
  // offers a blank option (see renderRecipeFormModal), so this only fires
  // if some other code path left department_number unset.
  if (!["1", "2", "3", "4"].includes(draft.department_number)) {
    errors.push("Il reparto è obbligatorio: seleziona uno dei 4 reparti produttivi.");
  }

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
    // Always one of 1-4: validateRecipeForm() guarantees this before
    // buildRecipePayload() is ever called (see the department <select>'s
    // "no blank option" comment above).
    department_number: Number(draft.department_number),
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
    showToast(`Ricetta "${saved.id}" salvata come Versione ${saved.version}.`);
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
      ? `Salva come Versione ${draft.baseVersion + 1}`
      : "Crea ricetta (Versione 1)";

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
          ${draft.mode === "edit" ? `<span class="recipe-badge">Versione ${draft.baseVersion} → Versione ${draft.baseVersion + 1}</span>` : `<span class="recipe-badge">Versione 1</span>`}
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
              ${[1, 2, 3, 4].map((n) => `<option value="${n}" ${draft.department_number === String(n) ? "selected" : ""}>${DEPT_FALLBACK_NAMES[n]}</option>`).join("")}
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
            <select class="form-select" data-action="recipe-form-select" data-path="phases.${idx}.name">
              ${(phaseNameOptionsFor(draft.department_number).includes(phase.name) ? phaseNameOptionsFor(draft.department_number) : [phase.name, ...phaseNameOptionsFor(draft.department_number)])
                .map((n) => `<option value="${escapeAttr(n)}" ${phase.name === n ? "selected" : ""}>${escapeHtml(n)}</option>`).join("")}
            </select>
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

  setPageTitle("Controllo settori", `Accesso diretto al controllo avanzato di ogni settore · ${zones.length} settori registrati (massimo 9)`);

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
  STATE.deleteZoneConfirm = false;
  STATE.deleteZoneStatus = null;
  STATE.deleteZoneError = null;
  STATE.modalPlants = [];
  STATE.modalPlantsLoaded = false;
  STATE.addPlantStatus = null;
  STATE.addPlantError = null;
  STATE.plantUi = {};

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
  STATE.deleteZoneConfirm = false;
  STATE.deleteZoneStatus = null;
  STATE.deleteZoneError = null;
  STATE.modalPlants = [];
  STATE.modalPlantsLoaded = false;
  STATE.addPlantStatus = null;
  STATE.addPlantError = null;
  STATE.quarantinePlants = [];
  STATE.quarantinePlantsLoaded = false;
  STATE.plantUi = {};
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
  const [zoneRes, telemetryRes, actuatorsRes, plantsRes] = await Promise.allSettled([
    apiGet(`/zones/${encodeURIComponent(zoneId)}`),
    apiGet(`/zones/${encodeURIComponent(zoneId)}/telemetry/latest`),
    apiGet(`/zones/${encodeURIComponent(zoneId)}/actuators/latest`),
    apiGet("/plants", { zone_id: zoneId, limit: 1000 }),
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
  if (plantsRes.status === "fulfilled") {
    // Don't clobber a plant the user is mid-action on (quarantine form open,
    // delete confirm pending) with a stale poll response race — but a plain
    // list refresh (new plant added elsewhere, one removed) is safe to apply.
    STATE.modalPlants = plantsRes.value;
    STATE.modalPlantsLoaded = true;
  }
  renderModalIfSafe();
}

/**
 * Quarantine detail pop-up: opened from the clickable REPARTO 5 card on
 * Home, not tied to any single zone (STATE.modalZoneId stays null — there
 * is no "zone" this pop-up is about, only the fixed quarantine sector).
 */
async function openQuarantineModal() {
  STATE.modalKind = "quarantine";
  STATE.modalZoneId = null;
  STATE.modalZone = null;
  STATE.quarantinePlants = [];
  STATE.quarantinePlantsLoaded = false;
  STATE.plantUi = {};
  STATE.returnTo = null;

  document.getElementById("modal-overlay").classList.remove("hidden");
  renderModal();
  setPoll("modal", tickQuarantineModal, MODAL_POLL_MS);
}

async function tickQuarantineModal() {
  if (STATE.modalKind !== "quarantine") return;
  try {
    const plants = await apiGet("/plants", {
      zone_id: QUARANTINE_ZONE_ID,
      is_quarantined: true,
      limit: 1000,
    });
    if (STATE.modalKind !== "quarantine") return; // modal changed while in flight
    STATE.quarantinePlants = plants;
    STATE.quarantinePlantsLoaded = true;
    renderModalIfSafe();
  } catch (e) {
    if (STATE.modalKind !== "quarantine") return;
    STATE.quarantinePlantsLoaded = true;
    renderModalIfSafe();
  }
}

function renderModalIfSafe() {
  // Avoid yanking focus away from an open <select> — or, since the plant
  // quarantine reason field lives here too, a text <input> — mid-interaction.
  const active = document.activeElement;
  if (active && (active.tagName === "SELECT" || active.tagName === "INPUT") && isFocusedInside("modal-content")) return;
  renderModal();
}

function renderModal() {
  if (STATE.modalKind === "recipe") { renderRecipeModal(); return; }
  if (STATE.modalKind === "recipe-form") { renderRecipeFormModal(); return; }
  if (STATE.modalKind === "quarantine") { renderQuarantineModal(); return; }
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
      <section class="zone-section span2">
        ${renderZonePlants(zone)}
      </section>
      <section class="zone-section span2 zone-danger-zone">
        ${renderDeleteZoneSection(zone)}
      </section>
    </div>
  `;
}

function renderZonePlants(zone) {
  const plants = STATE.modalPlants || [];
  const addSending = STATE.addPlantStatus === "sending";
  let body;
  if (!STATE.modalPlantsLoaded) {
    body = '<div class="empty-note">Caricamento…</div>';
  } else if (plants.length === 0) {
    body = '<div class="empty-note">Nessuna pianta registrata in questo settore.</div>';
  } else {
    body = `<div class="plant-list">${plants.map((p) => renderZonePlantRow(p)).join("")}</div>`;
  }
  return `
    <div class="live-head"><span class="title">Piante di questo settore</span><span class="ro">${plants.length} registrat${plants.length === 1 ? "a" : "e"}</span></div>
    ${body}
    ${STATE.addPlantError ? `<div class="zone-danger-error">${escapeHtml(STATE.addPlantError)}</div>` : ""}
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button type="button" class="btn" data-action="add-plant" data-zone-id="${escapeAttr(zone.id)}" ${addSending ? "disabled" : ""}>${addSending ? "Aggiunta…" : "+ Aggiungi pianta"}</button>
    </div>
  `;
}

/**
 * One row for a plant currently hosted in this production zone. Two
 * actions, deliberately unbalanced: "Metti in quarantena" is the frequent,
 * inviting one (btn-warn, same weight as elsewhere in the app); "Rimuovi
 * definitivamente" is the rare, irreversible one — same principle as
 * "Rimuovi settore": muted styling, physically separated (own row below),
 * explicit two-step confirmation before the DELETE actually fires.
 */
function renderZonePlantRow(p) {
  const ui = STATE.plantUi[p.id] || {};
  const qSending = ui.quarantineStatus === "sending";
  const dSending = ui.deleteStatus === "sending";

  let quarantineBlock;
  if (ui.quarantineFormOpen) {
    quarantineBlock = `
      <div class="plant-inline-form">
        <input type="text" class="add-sector-input" data-action="plant-quarantine-reason" data-plant-id="${escapeAttr(p.id)}"
          placeholder="Motivo (obbligatorio)" value="${escapeAttr(ui.quarantineReason || "")}" ${qSending ? "disabled" : ""}>
        ${ui.quarantineError ? `<div class="add-sector-error">${escapeHtml(ui.quarantineError)}</div>` : ""}
        <div class="add-sector-actions">
          <button type="button" class="btn" data-action="cancel-plant-quarantine" data-plant-id="${escapeAttr(p.id)}" ${qSending ? "disabled" : ""}>Annulla</button>
          <button type="button" class="btn btn-warn" data-action="submit-plant-quarantine" data-plant-id="${escapeAttr(p.id)}" ${qSending ? "disabled" : ""}>${qSending ? "Invio…" : "Conferma quarantena"}</button>
        </div>
      </div>
    `;
  } else {
    quarantineBlock = `<button type="button" class="btn btn-warn" data-action="open-plant-quarantine" data-plant-id="${escapeAttr(p.id)}">Metti in quarantena</button>`;
  }

  return `
    <div class="plant-row">
      <div class="plant-row-head">
        <span class="plant-row-id mono">${escapeHtml(p.id)}</span>
        <span class="plant-row-species">${escapeHtml(p.species)}</span>
      </div>
      <div class="plant-row-actions">${quarantineBlock}</div>
      ${renderPlantRemoveBlock(p, ui, dSending)}
    </div>
  `;
}

/**
 * "Rimuovi definitivamente" — shared between the zone-detail plant rows and
 * the quarantine tiles, same muted/irreversible treatment in both places.
 */
function renderPlantRemoveBlock(p, ui, dSending) {
  if (ui.deleteConfirm) {
    return `
      <div class="plant-remove-confirm">
        <p>Rimuovere definitivamente <b>${escapeHtml(p.id)}</b> (${escapeHtml(p.species)})? Non è reversibile: cancella anche lo storico movimenti.</p>
        ${ui.deleteError ? `<div class="zone-danger-error">${escapeHtml(ui.deleteError)}</div>` : ""}
        <div class="zone-danger-actions">
          <button type="button" class="btn" data-action="cancel-plant-remove" data-plant-id="${escapeAttr(p.id)}" ${dSending ? "disabled" : ""}>Annulla</button>
          <button type="button" class="btn btn-danger" data-action="confirm-plant-remove" data-plant-id="${escapeAttr(p.id)}" ${dSending ? "disabled" : ""}>${dSending ? "Rimozione…" : "Conferma rimozione"}</button>
        </div>
      </div>
    `;
  }
  return `
    <div class="plant-row-danger">
      <button type="button" class="plant-remove-btn" data-action="open-plant-remove" data-plant-id="${escapeAttr(p.id)}">Rimuovi definitivamente</button>
    </div>
  `;
}

function renderDeleteZoneSection(zone) {
  const sending = STATE.deleteZoneStatus === "sending";
  if (!STATE.deleteZoneConfirm) {
    return `
      <div class="zone-section-title">Rimuovi settore</div>
      <div class="zone-danger-note">Elimina definitivamente il settore ${escapeHtml(zoneLabel(zone))} e il suo storico (telemetria, eventi, comandi, coltivazioni archiviate). Un settore con una coltivazione attiva non può essere rimosso.</div>
      <button type="button" class="btn btn-danger-ghost" data-action="open-delete-zone-confirm">Rimuovi settore</button>
    `;
  }
  return `
    <div class="zone-section-title">Rimuovi settore</div>
    <div class="zone-danger-confirm">
      <p>Stai per eliminare definitivamente il settore <b>${escapeHtml(zoneLabel(zone))}</b> (${escapeHtml(zone.name)}). L'operazione non è reversibile e rimuove anche:</p>
      <ul>
        <li>lo storico di telemetria e degli attuatori</li>
        <li>gli eventi e i comandi registrati per questo settore</li>
        <li>le coltivazioni archiviate legate a questo settore</li>
      </ul>
      <p style="margin-bottom:0">Le eventuali piante ancora associate a questo settore non vengono cancellate.</p>
      ${STATE.deleteZoneError ? `<div class="zone-danger-error">${escapeHtml(STATE.deleteZoneError)}</div>` : ""}
      <div class="zone-danger-actions" style="margin-top:12px">
        <button type="button" class="btn" data-action="cancel-delete-zone" ${sending ? "disabled" : ""}>Annulla</button>
        <button type="button" class="btn btn-danger" data-action="confirm-delete-zone" ${sending ? "disabled" : ""}>${sending ? "Rimozione…" : "Conferma rimozione"}</button>
      </div>
    </div>
  `;
}

async function deleteZoneNow() {
  const zone = STATE.modalZone;
  if (!zone || STATE.deleteZoneStatus === "sending") return;
  STATE.deleteZoneStatus = "sending";
  STATE.deleteZoneError = null;
  renderModal();

  try {
    await apiDelete(`/zones/${encodeURIComponent(zone.id)}`);
    STATE.zones = STATE.zones.filter((z) => z.id !== zone.id);
    closeModal();
    if (STATE.view === "home") renderHome();
    else if (STATE.view === "control") renderControl();
    showToast(`Settore ${zoneLabel(zone)} rimosso.`);
  } catch (err) {
    STATE.deleteZoneStatus = null;
    STATE.deleteZoneError = err.message;
    if (STATE.modalZoneId === zone.id) renderModal();
  }
}

/* ------------------------------------------------------------------ */
/* Plant actions — shared between the zone-detail plant list and the  */
/* quarantine detail pop-up (per-plant STATE.plantUi, keyed by id)    */
/* ------------------------------------------------------------------ */

/**
 * id is auto-generated as "{zone_id}-p{n}" (n = 1 past the highest existing
 * suffix already used by a plant with home_zone_id === zone_id), species and
 * home_zone_id come from the zone itself — nothing is asked from the user,
 * per spec.
 */
async function addPlantToZone(zoneId) {
  if (STATE.addPlantStatus === "sending") return;
  const zone = STATE.zones.find((z) => z.id === zoneId) || STATE.modalZone;
  if (!zone) return;

  STATE.addPlantStatus = "sending";
  STATE.addPlantError = null;
  renderModal();

  const prefix = `${zoneId}-p`;
  try {
    // The next free number can NOT be derived from STATE.modalPlants (or
    // any current_zone_id-filtered list, i.e. GET /plants?zone_id=...): a
    // plant that started in this zone and was later quarantined keeps
    // home_zone_id === zoneId forever, but its current_zone_id moves to
    // r5-s1 — so a current_zone_id query is blind to it and its number
    // could be reused. GET /plants has no home_zone_id filter, so ask the
    // backend for every plant and filter client-side by home_zone_id here
    // — always the backend's live state, never a locally-held counter (one
    // would reset on every page reload and reintroduce this exact bug).
    const allPlants = await apiGet("/plants", { limit: 1000 });
    const usedNumbers = allPlants
      .filter((p) => p.home_zone_id === zoneId)
      .map((p) => p.id)
      .filter((id) => id.startsWith(prefix))
      .map((id) => Number(id.slice(prefix.length)))
      .filter((n) => Number.isInteger(n) && n > 0);
    let n = usedNumbers.length ? Math.max(...usedNumbers) + 1 : 1;

    // Belt-and-braces on top of the home_zone_id lookup above: POST
    // /plants is idempotent by (id, species, home_zone_id), so if a
    // concurrent add from elsewhere races this one and the id picked above
    // gets taken first, the backend would silently hand back that OTHER
    // record instead of erroring — its current_zone_id would then not be
    // this zone. Treat that as "id taken" and move to the next number
    // instead of showing someone else's plant as freshly added here.
    const MAX_ATTEMPTS = 20;
    let created = null;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      const candidate = await apiPost("/plants", {
        id: `${prefix}${n}`,
        species: zone.plant_species,
        home_zone_id: zoneId,
      });
      if (candidate.current_zone_id === zoneId) { created = candidate; break; }
      n += 1;
    }
    if (STATE.modalZoneId !== zoneId) return; // modal changed meanwhile
    if (!created) {
      STATE.addPlantStatus = null;
      STATE.addPlantError = "Impossibile generare un id pianta libero, riprova.";
      renderModal();
      return;
    }
    STATE.modalPlants = [...(STATE.modalPlants || []), created];
    STATE.addPlantStatus = null;
    renderModal();
    showToast(`Pianta ${created.id} aggiunta.`);
  } catch (err) {
    if (STATE.modalZoneId !== zoneId) return;
    STATE.addPlantStatus = null;
    STATE.addPlantError = err.message;
    renderModal();
  }
}

function openPlantQuarantineForm(plantId) {
  STATE.plantUi[plantId] = { ...(STATE.plantUi[plantId] || {}), quarantineFormOpen: true, quarantineReason: "", quarantineError: null };
  renderModal();
}

function cancelPlantQuarantineForm(plantId) {
  STATE.plantUi[plantId] = { ...(STATE.plantUi[plantId] || {}), quarantineFormOpen: false, quarantineError: null };
  renderModal();
}

async function submitPlantQuarantine(plantId) {
  const ui = STATE.plantUi[plantId] || {};
  if (ui.quarantineStatus === "sending") return;
  const reason = (ui.quarantineReason || "").trim();
  if (!reason) {
    // Backend rejects a quarantine request with no reason (see
    // PlantQuarantineUpdate._validate_transition) — validated client-side
    // too, so the round trip isn't needed just to learn that.
    STATE.plantUi[plantId] = { ...ui, quarantineReason: reason, quarantineError: "Il motivo è obbligatorio." };
    renderModal();
    return;
  }

  STATE.plantUi[plantId] = { ...ui, quarantineReason: reason, quarantineStatus: "sending", quarantineError: null };
  renderModal();

  try {
    await apiPatch(`/plants/${encodeURIComponent(plantId)}/quarantine`, {
      is_quarantined: true,
      quarantine_zone_id: QUARANTINE_ZONE_ID,
      reason,
    });
    // The plant's current_zone_id just moved to quarantine — it no longer
    // belongs in this zone's plant list.
    if (STATE.modalPlants) STATE.modalPlants = STATE.modalPlants.filter((p) => p.id !== plantId);
    delete STATE.plantUi[plantId];
    renderModal();
    showToast(`Pianta ${plantId} messa in quarantena.`);
  } catch (err) {
    STATE.plantUi[plantId] = { ...STATE.plantUi[plantId], quarantineStatus: null, quarantineError: err.message };
    renderModal();
  }
}

async function releasePlantFromQuarantine(plantId) {
  const ui = STATE.plantUi[plantId] || {};
  if (ui.releaseStatus === "sending") return;
  STATE.plantUi[plantId] = { ...ui, releaseStatus: "sending", releaseError: null };
  renderModal();

  try {
    await apiPatch(`/plants/${encodeURIComponent(plantId)}/quarantine`, { is_quarantined: false });
    if (STATE.quarantinePlants) STATE.quarantinePlants = STATE.quarantinePlants.filter((p) => p.id !== plantId);
    delete STATE.plantUi[plantId];
    renderModal();
    showToast(`Pianta ${plantId} uscita dalla quarantena.`);
  } catch (err) {
    // The one documented failure mode here is 409 (home zone no longer
    // exists, see PATCH /plants/{id}/quarantine) — surfaced on this same
    // tile instead of a generic toast, since "Rimuovi definitivamente"
    // right below is the actual way out of that specific situation.
    const message = err.status === 409
      ? `Impossibile far uscire la pianta: ${err.message}`
      : err.message;
    STATE.plantUi[plantId] = { ...STATE.plantUi[plantId], releaseStatus: null, releaseError: message };
    renderModal();
  }
}

/**
 * "Rimuovi definitivamente" is now reachable from three places — the zone
 * detail pop-up, the quarantine detail pop-up, and (for a quarantined
 * plant whose origin sector is gone) the Allarmi page's orphan cards. The
 * first two live inside #modal-content, the third doesn't open any modal
 * at all, so a single renderModal() call isn't enough to always repaint
 * wherever the plant is actually shown — this refreshes every host that
 * might currently have it on screen.
 */
function refreshPlantHosts() {
  renderModal();
  if (STATE.view === "alerts") renderAlertsView();
  updateNavAlertsBadge();
}

function openPlantRemoveConfirm(plantId) {
  STATE.plantUi[plantId] = { ...(STATE.plantUi[plantId] || {}), deleteConfirm: true, deleteError: null };
  refreshPlantHosts();
}

function cancelPlantRemove(plantId) {
  STATE.plantUi[plantId] = { ...(STATE.plantUi[plantId] || {}), deleteConfirm: false, deleteError: null };
  refreshPlantHosts();
}

async function confirmPlantRemove(plantId) {
  const ui = STATE.plantUi[plantId] || {};
  if (ui.deleteStatus === "sending") return;
  STATE.plantUi[plantId] = { ...ui, deleteStatus: "sending", deleteError: null };
  refreshPlantHosts();

  try {
    await apiDelete(`/plants/${encodeURIComponent(plantId)}`);
    if (STATE.modalPlants) STATE.modalPlants = STATE.modalPlants.filter((p) => p.id !== plantId);
    if (STATE.quarantinePlants) STATE.quarantinePlants = STATE.quarantinePlants.filter((p) => p.id !== plantId);
    // Also drop it from the Home/Allarmi quarantine cache (the source for
    // the orphan-plant alarm and the "Piante in osservazione" list) so a
    // removal made right here doesn't keep showing a now-deleted plant
    // until the next background poll happens to refetch it.
    if (STATE.quarantine && STATE.quarantine.plants) {
      STATE.quarantine.plants = STATE.quarantine.plants.filter((p) => p.id !== plantId);
    }
    delete STATE.plantUi[plantId];
    refreshPlantHosts();
    showToast(`Pianta ${plantId} rimossa definitivamente.`);
  } catch (err) {
    STATE.plantUi[plantId] = { ...STATE.plantUi[plantId], deleteStatus: null, deleteError: err.message };
    refreshPlantHosts();
  }
}

/* ------------------------------------------------------------------ */
/* Quarantine detail pop-up                                           */
/* ------------------------------------------------------------------ */

function renderQuarantineModal() {
  const wrap = document.getElementById("modal-content");
  const meta = DEPT_META[5];
  const plants = STATE.quarantinePlants || [];

  let body;
  if (!STATE.quarantinePlantsLoaded) {
    body = '<div class="empty-note">Caricamento…</div>';
  } else if (plants.length === 0) {
    body = '<div class="empty-note">Nessuna pianta attualmente in quarantena.</div>';
  } else {
    body = `<div class="quarantine-grid">${plants.map((p) => renderQuarantineTile(p)).join("")}</div>`;
  }

  wrap.innerHTML = `
    <div class="zone-header" style="--zone-tint:${meta.tint}">
      <div style="min-width:0">
        <div class="zone-header-code">
          <span class="code">REPARTO 5</span>
        </div>
        <h2>Quarantena</h2>
        <div class="zone-header-meta">
          <span>${plants.length} piant${plants.length === 1 ? "a" : "e"} attualmente in quarantena</span>
        </div>
      </div>
      <button type="button" class="zone-close" data-action="close-modal">✕</button>
    </div>
    <div class="zone-body">
      <section class="zone-section span2">
        ${body}
      </section>
    </div>
  `;
}

/**
 * One tile per quarantined plant: species, entry reason, live elapsed time,
 * "Fai uscire" (disabled until QUARANTINE_MIN_RELEASE_MS has passed, 409
 * surfaced inline instead of a generic error) and, distinctly styled,
 * "Rimuovi definitivamente" — the real fix for a plant stuck here because
 * its origin sector no longer exists (see the Home alarm above).
 */
function renderQuarantineTile(p) {
  const ui = STATE.plantUi[p.id] || {};
  const rSending = ui.releaseStatus === "sending";
  const dSending = ui.deleteStatus === "sending";
  const canRelease = quarantineReleaseAllowed(p.quarantined_at) && !rSending;
  const wait = quarantineReleaseWait(p.quarantined_at);
  const releaseLabel = rSending ? "Uscita…" : "Fai uscire";
  const releaseTitle = !quarantineReleaseAllowed(p.quarantined_at) ? `Disponibile tra ${wait}` : "";

  return `
    <div class="quarantine-tile">
      <div class="quarantine-tile-head">
        <span class="plant-row-species">${escapeHtml(p.species)}</span>
        <span class="plant-row-id mono">${escapeHtml(p.id)}</span>
      </div>
      <div class="quarantine-tile-row"><span class="k">Motivo</span><span class="v">${escapeHtml(p.quarantine_reason || "—")}</span></div>
      <div class="quarantine-tile-row"><span class="k">In quarantena da</span><span class="v mono">${fmtElapsed(p.quarantined_at)}</span></div>
      ${ui.releaseError ? `<div class="zone-danger-error">${escapeHtml(ui.releaseError)}</div>` : ""}
      <div class="quarantine-tile-actions">
        <button type="button" class="btn btn-primary" data-action="release-plant" data-plant-id="${escapeAttr(p.id)}" ${canRelease ? "" : "disabled"} title="${escapeAttr(releaseTitle)}">${releaseLabel}${!canRelease && !rSending && wait ? ` (tra ${wait})` : ""}</button>
      </div>
      ${renderPlantRemoveBlock(p, ui, dSending)}
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
  ["home", "recipes", "control", "alerts"].forEach((v) => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== view);
  });

  clearPoll("zones");
  clearPoll("recipes-poll");
  clearPoll("alerts-view");

  if (view === "home") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "control") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "recipes") {
    setPoll("recipes-poll", tickRecipes, RECIPES_POLL_MS);
  } else if (view === "alerts") {
    setPoll("alerts-view", tickAlertsView, ALERTS_POLL_MS);
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

    const openDeleteZoneConfirm = e.target.closest('[data-action="open-delete-zone-confirm"]');
    if (openDeleteZoneConfirm) {
      STATE.deleteZoneConfirm = true;
      STATE.deleteZoneError = null;
      renderModal();
      return;
    }

    const cancelDeleteZone = e.target.closest('[data-action="cancel-delete-zone"]');
    if (cancelDeleteZone) {
      STATE.deleteZoneConfirm = false;
      STATE.deleteZoneError = null;
      renderModal();
      return;
    }

    const confirmDeleteZoneBtn = e.target.closest('[data-action="confirm-delete-zone"]');
    if (confirmDeleteZoneBtn && !confirmDeleteZoneBtn.disabled) { deleteZoneNow(); return; }

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

    const openQuarantine = e.target.closest('[data-action="open-quarantine"]');
    if (openQuarantine) { openQuarantineModal(); return; }

    const addPlantBtn = e.target.closest('[data-action="add-plant"]');
    if (addPlantBtn && !addPlantBtn.disabled) { addPlantToZone(addPlantBtn.dataset.zoneId); return; }

    const openPlantQuarantine = e.target.closest('[data-action="open-plant-quarantine"]');
    if (openPlantQuarantine) { openPlantQuarantineForm(openPlantQuarantine.dataset.plantId); return; }

    const cancelPlantQuarantine = e.target.closest('[data-action="cancel-plant-quarantine"]');
    if (cancelPlantQuarantine && !cancelPlantQuarantine.disabled) { cancelPlantQuarantineForm(cancelPlantQuarantine.dataset.plantId); return; }

    const submitPlantQuarantineBtn = e.target.closest('[data-action="submit-plant-quarantine"]');
    if (submitPlantQuarantineBtn && !submitPlantQuarantineBtn.disabled) { submitPlantQuarantine(submitPlantQuarantineBtn.dataset.plantId); return; }

    const releasePlantBtn = e.target.closest('[data-action="release-plant"]');
    if (releasePlantBtn && !releasePlantBtn.disabled) { releasePlantFromQuarantine(releasePlantBtn.dataset.plantId); return; }

    const openPlantRemove = e.target.closest('[data-action="open-plant-remove"]');
    if (openPlantRemove) { openPlantRemoveConfirm(openPlantRemove.dataset.plantId); return; }

    const cancelPlantRemoveBtn = e.target.closest('[data-action="cancel-plant-remove"]');
    if (cancelPlantRemoveBtn && !cancelPlantRemoveBtn.disabled) { cancelPlantRemove(cancelPlantRemoveBtn.dataset.plantId); return; }

    const confirmPlantRemoveBtn = e.target.closest('[data-action="confirm-plant-remove"]');
    if (confirmPlantRemoveBtn && !confirmPlantRemoveBtn.disabled) { confirmPlantRemove(confirmPlantRemoveBtn.dataset.plantId); return; }
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
      // Changing department re-syncs every phase's name to the new
      // department's real catalog sequence (by position) — the phase-name
      // <select> only ever offers that department's actual phase names, so
      // this keeps every phase pointing at a valid one instead of an
      // orphaned selection from the previous department.
      if (recipeFormSelect.dataset.path === "department_number") {
        remapPhaseNamesForDepartment(STATE.recipeForm, value);
      }
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

    const plantQuarantineReason = e.target.closest('[data-action="plant-quarantine-reason"]');
    if (plantQuarantineReason) {
      const plantId = plantQuarantineReason.dataset.plantId;
      const ui = STATE.plantUi[plantId] || {};
      // Same "write straight into STATE, no re-render" pattern as
      // add-sector-field, so typing doesn't fight the modal's periodic poll
      // re-render for cursor position/focus.
      STATE.plantUi[plantId] = { ...ui, quarantineReason: plantQuarantineReason.value };
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
