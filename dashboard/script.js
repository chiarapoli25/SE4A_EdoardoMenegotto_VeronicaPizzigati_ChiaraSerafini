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
// Simulatore page: batch preview job polling — isolated from every
// operational poll above, see the dedicated section near
// openGreenhouseSimulatorModal().
const SIMULATION_POLL_MS = 1500;

// Frontend-only rule (not enforced by the backend): "Fai uscire" stays
// disabled until at least this much time has passed since quarantined_at.
// Placeholder value — change this constant to tune it, nothing else to touch.
const QUARANTINE_MIN_RELEASE_MS = 24 * 60 * 60 * 1000;

// The quarantine zone id is fixed: department 5 has exactly one sector
// (backend now enforces sector_number=1 there, see zones/routes.py), so
// there is only ever one valid destination for PATCH /plants/{id}/quarantine.
const QUARANTINE_ZONE_ID = "r5-s1";

// simKey is a SEPARATE field from sensorField: sensorField names the live
// telemetry sample field (GET /zones/{id}/telemetry, "..._estimate_..."
// for the three nutrients), simKey names the raw Edge batch-simulation
// step field (POST /simulations result series' sensors+models, no
// "_estimate_" — see edge/src/simulation_main.cpp). The two pipelines are
// genuinely different data sources with different field names; simKey
// exists only for the "Simula questa ricetta" chart (drawSimulationChart).
// L'unità della luce ("mol/m²/giorno", DLI) descrive il TARGET di fase
// (setpoint/allowed_range — vedi il form ricette), non il dato grezzo dietro
// sensorField/simKey: quello resta un PPFD istantaneo (µmol/m²s), invariato
// — solo il Simulatore lo converte in DLI maturato oggi prima di
// disegnarlo (drawSimulationSeriesChart), proprio per poterlo confrontare
// col target. Il grafico telemetria live del singolo settore mostra invece
// ancora il PPFD istantaneo sotto questa stessa etichetta: un'imprecisione
// nota, non affrontata in questo giro.
const VARIABLES = [
  { key: "soil_moisture", label: "Umidità del terriccio", unit: "%", sensorField: "soil_moisture_percent", simKey: "soil_moisture_percent", decimals: 1 },
  { key: "light", label: "Luce (DLI)", unit: "mol/m²/giorno", sensorField: "light_ppfd_umol_m2_s", simKey: "light_ppfd_umol_m2_s", decimals: 1 },
  { key: "ph", label: "pH", unit: "pH", sensorField: "ph", simKey: "ph", decimals: 2 },
  { key: "nitrogen", label: "Azoto (N)", unit: "mg/L", sensorField: "nitrogen_estimate_mg_per_liter", simKey: "nitrogen_mg_per_liter", decimals: 1 },
  { key: "phosphorus", label: "Fosforo (P)", unit: "mg/L", sensorField: "phosphorus_estimate_mg_per_liter", simKey: "phosphorus_mg_per_liter", decimals: 1 },
  { key: "potassium", label: "Potassio (K)", unit: "mg/L", sensorField: "potassium_estimate_mg_per_liter", simKey: "potassium_mg_per_liter", decimals: 1 },
];
const VARIABLES_BY_KEY = Object.fromEntries(VARIABLES.map((v) => [v.key, v]));

// "Simula questa ricetta" duration presets (giorni, mai secondi digitati
// dall'utente): 1/7/30/90/180/360 giorni × 86400 sono tutti multipli
// esatti di STEP_SECONDS=900, quindi sempre validi per il backend senza
// bisogno di ulteriore validazione client-side sul valore convertito.
const SIMULATION_DURATION_PRESETS = [
  { days: 1, label: "1 giorno" },
  { days: 7, label: "1 settimana" },
  { days: 30, label: "1 mese" },
  { days: 90, label: "3 mesi" },
  { days: 180, label: "6 mesi" },
  { days: 360, label: "1 anno" },
];

const STRATEGIES = ["Threshold", "PID", "Predictive"];
// Predictive used to be forced for the three nutrients (the only source
// available for N/P/K is a model estimate, not a direct sensor) and was
// therefore excluded here as "never a real choice". That lock was a policy
// choice, not a technical one — process_value()/source_value() on the Edge
// resolve a model estimate identically for any Strategy (see
// control_system.cpp's required_default_strategy()) — and has been lifted:
// an Amministratore can now pick all three, from this same bulk panel, for
// every variable including N/P/K. There is still no separate "photoperiod"
// Strategy anywhere in the backend (photoperiod is a fixed recipe timing
// property, not a controller strategy), so it's never offered as one here.
const CHOOSABLE_STRATEGIES = ["Threshold", "PID", "Predictive"];
// The variables the plant-wide Strategy panel (Controllo page) lets an
// Amministratore actually choose a Strategy for. Kept in this fixed order
// wherever the panel lists them. Nitrogen/phosphorus/potassium joined this
// list once Threshold/PID became valid choices for them too (see the
// CHOOSABLE_STRATEGIES comment above).
const GLOBAL_STRATEGY_VARIABLES = [
  "soil_moisture", "light", "ph", "nitrogen", "phosphorus", "potassium",
];

// Account roles (see the "Login screen" section far below), verified by the
// backend on every login — "admin" is the one role value with an actual
// effect on what's shown (gates the Controllo page/nav item and the "Gestione
// utenti" panel inside it, see isAdmin()) and on what the backend itself
// accepts (POST/GET /users, see backend/app/features/users/dependencies.py).
const VALID_ROLES = ["agronomo", "admin"];

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
// Display order wherever departments are listed or filtered: plain
// numeric order. (A prior version of this spec put Quarantena (5) right
// after department 3 — [1,2,3,5,4] — that was deliberately reverted back
// to simple numeric order; don't reintroduce it without being asked.)
const DEPT_ORDER = [1, 2, 3, 4, 5];

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
/* Mirrors the fixed variable <-> input_source/actuator associations  */
/* enforced server-side in backend/app/features/recipes/models.py     */
/* (_REQUIRED_INPUT_SOURCE / _REQUIRED_ACTUATOR). default_strategy is  */
/* also enforced there (_required_default_strategy) and, redundantly, */
/* by smarthydro::RecipeControlSystem::validate_recipe() on the Edge — */
/* REQUIRED_DEFAULT_STRATEGY below must keep matching both, or a       */
/* submitted recipe comes back as a 422 (it's sent unconditionally,   */
/* see buildRecipePayload). selected_strategy is NOT a per-recipe      */
/* choice this form exposes: it's a plant-wide setting (see            */
/* Controllo/renderGlobalStrategyPanel and                             */
/* backend/app/features/control_strategy/) — the form shows it read-   */
/* only per variable (currentControlStrategy()) and the payload just   */
/* carries that same value, since the backend re-derives it from the   */
/* global setting on every read regardless of what's submitted.        */
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
  // DLI (Daily Light Integral) minimo giornaliero, non più un livello PPFD
  // istantaneo — vedi ControlledVariable.LIGHT lato backend.
  light: "mol/(m2 day)",
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
  light: { setpoint: 17.3, allowed_range: { minimum: 13.0, maximum: 21.6 }, safety_range: { minimum: 0, maximum: 80 }, suggested_phase_dose_milliliters: 0 },
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
  // False until the first successful GET /zones. switchView() uses this to
  // decide whether a view can be repainted immediately from cache or needs
  // a "Caricamento…" placeholder — without it, every nav switch (even a
  // revisit) leaves the content area blank until its poll's first fetch
  // resolves, which on a slow connection reads as a broken two-column
  // layout (sidebar full height, nothing beside it for a long stretch).
  zonesLoaded: false,

  recipes: [],
  recipesLoaded: false,
  recipesById: {},
  recipeQuery: "",
  currentRecipe: null,
  recipeDetailPhaseIdx: 0,

  // Draft object for the create/edit recipe form (see openRecipeForm()),
  // or null when the form is not open. Routed through the same pop-up
  // overlay as the zone/recipe modals via STATE.modalKind = "recipe-form".
  recipeForm: null,

  // Simulatore (STATE.view = "simulator"): one "Simula l'intera serra"
  // batch preview covering every REAL, currently-registered production
  // sector that has a recipe REALLY assigned — never a free pick from the
  // full catalog, and never a single isolated sector (see POST
  // /simulations — never touches any zone itself, only reads
  // active_recipe_id for each once when the run starts). Shape:
  // { greenhouse: true, durationDays, starting, job, result, activeZoneId,
  //   error, chartVariable, chartView } — job is the polled
  // SimulationJob (queued/running/succeeded/failed/cancelled); result is
  // an ARRAY of SimulationPreview, one per zone, fetched once job
  // succeeds (see activeSimulationPreview); activeZoneId picks which one
  // the charts currently show — set on open (clicking a sector row, see
  // renderSimulatorSectorRow) or via the "Settore mostrato" <select>
  // inside the pop-up (renderSimulationResult).
  //
  // null whenever no simulation has been started yet this visit to the
  // Simulatore page. Unlike every other pop-up, closing this one
  // (closeModal) does NOT reset it to null — the job keeps polling and any
  // result stays around, so reopening (another sector row, or the page
  // button) shows the very same run. Only starting a genuinely new run
  // (restartSimulationSetup, "Nuova simulazione") or leaving the
  // Simulatore page entirely (switchView, which also frees the system's
  // single global batch slot via discardActiveSimulationIfAny if a job is
  // still queued/running) resets it.
  simulation: null,

  controlFilters: { dept: "all", species: "all", strategy: "all" },

  // The account returned by POST /auth/login (or re-verified by GET
  // /auth/me on boot — see the "Login screen" section below): { token,
  // username, display_name, role }. Mirrored into STATE so isAdmin()/nav
  // visibility/switchView's Controllo gate and apiRequest()'s Authorization
  // header can read it synchronously. null while the login screen is
  // showing.
  currentUser: null,

  // Pagina "Utenti" (nav item dedicato, admin-only): the account list and
  // the create-account form — see ensureUsersLoaded()/
  // renderUserManagementPanel()/renderUsersView()/submitCreateUser().
  users: {
    list: [],
    loaded: false,
    loading: false,
    error: null,
    form: { username: "", password: "", displayName: "", role: "agronomo" },
    // null | { kind: "sending" } | { kind: "success"|"error", message }
    status: null,
  },

  // Strategy di controllo a livello di impianto (pagina Controllo,
  // Amministratore-only per la scrittura — GET /control-strategy e' invece
  // leggibile da qualunque account, cosi' la pagina Ricette puo' mostrare
  // in sola lettura la Strategy in vigore). Non e' piu' una caratteristica
  // di ogni singola ricetta (vedi backend/app/features/control_strategy/):
  // un'unica scelta per variabile, persistita dal server e valida per
  // tutto l'impianto, incluse ricette non ancora assegnate a un settore e
  // il Simulatore batch. Keyed by variable key throughout:
  //  - settings: l'ultimo valore confermato dal server (GET/PUT
  //    /control-strategy) — vedi ensureControlStrategyLoaded().
  //  - drafts: la scelta nel <select> non ancora inviata (persistita qui
  //    cosi' un renderControl() in background, il poll zone ogni 6s, non
  //    perde una selezione in corso).
  //  - status: "sending" | "waiting" | "done" | null — "sending" durante
  //    la PUT, "waiting" mentre si attende che ogni settore gia' in
  //    coltivazione confermi il cambio live che il backend ha appena
  //    fatto per suo conto, "done" quando ogni settore coinvolto si e'
  //    risolto (successo o timeout). Gate sul pulsante "Applica" cosi' un
  //    secondo click non sovrappone un invio ancora in corso per la stessa
  //    variabile.
  //  - results: array di { zoneId, label, status: "waiting"|"success"|
  //    "timeout"|"error", message } — l'esito reale per settore, mostrato
  //    al posto di un singolo "fatto" generico (un cambio del genere puo',
  //    e su un settore non Running lo fara' davvero, riuscire per alcuni
  //    settori e non per altri). Lasciato in piedi (non azzerato) dopo che
  //    lo status passa a "done", cosi' il riepilogo resta visibile fino al
  //    prossimo invio.
  controlStrategy: { settings: {}, loaded: false, loading: false, drafts: {}, status: {}, results: {} },
  // setInterval ids started by applyGlobalStrategy's per-variable polling —
  // tracked separately from STATE.adhocIntervals (that one is cleared by
  // closeModal(), but this panel lives in the page itself, not a pop-up) so
  // switchView() can stop them when the Amministratore navigates away from
  // Controllo mid-poll instead of leaving them running against a page no
  // longer shown.
  controlAdhocIntervals: [],

  alerts: [],
  quarantine: {},
  plantCounts: {}, // zone_id -> live plant count (current_zone_id, not origin)
  homeExtrasLoaded: false,
  homeExtrasLastRun: 0,

  // { dept, sector, recipeId, confirmStep, status, error } | null — the
  // single "aggiungi settore" inline form open on Home, if any. Species is
  // never free text: recipeId points at one of that department's catalog
  // recipes (recipesForDepartment), and confirmStep gates an explicit
  // "are you sure" step before the POST actually fires.
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

  recipeHintVisible: false,
  phasePending: false,

  // "Rimuovi settore" (zone summary tab, bottom of the pop-up): null = plain
  // button; true = inline confirmation panel open; the explicit second step
  // before DELETE actually fires, same two-phase principle as addSectorForm.
  deleteZoneConfirm: false,
  deleteZoneStatus: null, // "sending" | null
  deleteZoneError: null,

  // "Ferma coltivazione" (zone summary tab, "Fase della ricetta" section):
  // the resolution path for the "settore con coltivazione attiva" delete
  // block — same inline-confirm shape as deleteZoneConfirm above, plus a
  // "waiting" status while polling for the Edge Controller to apply it.
  stopCultivationConfirm: false,
  stopCultivationStatus: null, // "sending" | "waiting" | null
  stopCultivationError: null,

  // "Avvia coltivazione" (same section, mirror of the stop block above) —
  // shown instead of it when the zone has no active_cultivation_id but
  // already has a recipe assigned (active_recipe_id): a single-click
  // action, no confirm step (starting isn't destructive) and no recipe
  // picker (always the zone's own active_recipe_id).
  startCultivationStatus: null, // "sending" | "waiting" | null
  startCultivationError: null,

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
  // Attaches the session token once one exists. Harmless on every endpoint
  // that doesn't require an account (the vast majority — see main.py: only
  // /users and /auth/me are gated) and required for the admin-only /users
  // endpoints the "Gestione utenti" panel calls.
  if (STATE.currentUser && STATE.currentUser.token) {
    opts.headers["Authorization"] = `Bearer ${STATE.currentUser.token}`;
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
const apiPut = (path, body) => apiRequest("PUT", path, { body });
const apiDelete = (path) => apiRequest("DELETE", path, {});

/**
 * The backend's HTTP error `detail` strings are internal/English by design
 * (see e.g. the docstrings in zones/repository.py) — never meant to reach
 * an end user verbatim. Known ones get a proper Italian translation in the
 * app's existing message style; anything not in the map falls back to the
 * raw text rather than hiding it.
 */
const API_ERROR_TRANSLATIONS = {
  "the zone has an active cultivation; stop it before deleting the zone":
    "Questo settore ha una coltivazione attiva: fermala prima di poterlo rimuovere.",
};
function translateApiError(message) {
  if (!message) return message;
  return API_ERROR_TRANSLATIONS[message] || message;
}

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

/** Whether the logged-in account has the "admin" role — the one role value
 * that actually gates something client-side (the Controllo page/nav item and
 * the "Gestione utenti" panel). The backend enforces the same rule
 * independently on POST/GET /users (require_admin), so this is a UI
 * convenience, not the only line of defense. */
function isAdmin() {
  return !!STATE.currentUser && STATE.currentUser.role === "admin";
}

/** Production zones (department 1-4 — never Quarantena) that currently have
 * a running cultivation (active_cultivation_id set — the same flag the
 * "Ferma coltivazione" block keys off elsewhere in this file). This is the
 * exact target list the plant-wide Strategy panel sends ChangeStrategy
 * commands to: a zone with a recipe assigned but no active cultivation, or
 * any Quarantena zone, is never included. */
function activeProductionZones() {
  return STATE.zones.filter((z) => z.department_number >= 1 && z.department_number <= 4 && !!z.active_cultivation_id);
}

/** Loads the current plant-wide Strategy per variable (GET /control-strategy)
 * into STATE.controlStrategy.settings, once — subsequent calls are no-ops
 * until the settings are explicitly refreshed by a successful
 * applyGlobalStrategy(). Called both from renderGlobalStrategyPanel() (the
 * Controllo page) and from the recipe form (which shows the same values
 * read-only), same lazy-load pattern as ensureUsersLoaded()/
 * ensureRecipeLoaded(). Readable by any logged-in account — only the PUT is
 * Amministratore-only — so an agronomo opening Ricette also sees the real
 * current Strategy, not just REQUIRED_DEFAULT_STRATEGY's fallback. */
async function ensureControlStrategyLoaded() {
  const cs = STATE.controlStrategy;
  if (cs.loaded || cs.loading) return;
  cs.loading = true;
  try {
    const rows = await apiGet("/control-strategy");
    rows.forEach((row) => { cs.settings[row.variable] = row.selected_strategy; });
    cs.loaded = true;
  } catch (err) {
    // Left un-loaded on failure: renderers fall back to
    // REQUIRED_DEFAULT_STRATEGY, and the next render retries.
  } finally {
    cs.loading = false;
    // Two call sites read this: renderGlobalStrategyPanel() (Controllo) and
    // the recipe form's read-only Strategy cells (opened as a modal, not a
    // view) — refresh whichever is actually showing.
    if (STATE.modalKind === "recipe-form") renderModalIfSafe();
    else renderControlIfSafe();
  }
}

/** Current Strategy for a variable — the server's answer once loaded,
 * REQUIRED_DEFAULT_STRATEGY as a placeholder before the first successful
 * ensureControlStrategyLoaded(). */
function currentControlStrategy(variableKey) {
  return STATE.controlStrategy.settings[variableKey] || REQUIRED_DEFAULT_STRATEGY[variableKey];
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
/* Strategy parameters — recipe payload placeholder only               */
/*                                                                    */
/* Strategy is a plant-wide setting now (see control_strategy/ on the  */
/* backend and renderGlobalStrategyPanel() below), not a per-recipe    */
/* choice: the live ChangeStrategy+ConfirmConfiguration fan-out this   */
/* function used to feed has moved server-side into                    */
/* PUT /control-strategy/{variable}, which derives its own parameters  */
/* (backend/app/features/recipes/parameters.py — a Python port of the  */
/* same formulas below). This copy survives only to fill               */
/* controllers[*].parameters in buildRecipePayload(): the backend      */
/* always re-derives Strategy and parameters from the current global   */
/* setting on every read (recipes/repository.py), so whatever is sent  */
/* here is a placeholder that satisfies the Recipe schema, not what    */
/* ends up in effect.                                                  */
/* ------------------------------------------------------------------ */

/**
 * `variableKey` matters for N/P/K: unlike soil_moisture/light/ph (which can
 * push the controlled variable in either direction, or at least sit at a
 * true zero), a fertilizer valve can only add — it can never "un-dose" — so
 * a Threshold/PID/Predictive command for a nutrient must never be allowed
 * to go negative, and its command scale is a dose in mL (a handful of mL,
 * see output_limits.maximum_dose_per_command_milliliters), not the
 * L/W-scale magic numbers used for the other three.
 */
// Limiti di comando (PID e Predictive) per le tre variabili non nutritive,
// nella reale unita' fisica del rispettivo attuatore — vedi lo stesso
// _NON_DOSE_COMMAND_LIMITS in backend/app/features/recipes/parameters.py, di
// cui questa e' la controparte JS: umidita' in litri per erogazione pompa
// (mai negativa), luce in percento di potenza (0-100, l'unica davvero
// bidirezionale in senso pieno), pH in mL di correttore (bidirezionale
// pH+/pH-).
const NON_DOSE_COMMAND_LIMITS = {
  soil_moisture: [0.0, 0.5],
  light: [0.0, 100.0],
  ph: [-0.5, 0.5],
};
// Guadagni Predictive specifici di ciascun nutriente — identici a quelli
// gia' tarati in catalog.py::_build_recipe per il seed iniziale, vedi la
// stessa costante _NUTRIENT_PREDICTIVE_GAINS in parameters.py.
const NUTRIENT_PREDICTIVE_GAINS = {
  nitrogen: [2.0, 4.0],
  phosphorus: [0.8, 2.0],
  potassium: [2.5, 5.0],
};
// Tempo di integrazione del PID in secondi — vedi la stessa costante e la
// stessa spiegazione in parameters.py: troppo corto insegue il rumore di
// ogni ciclo di controllo, troppo lungo lascia per ore un bias stazionario
// che il solo termine proporzionale non elimina contro un disturbo costante
// (es. l'evapotraspirazione, che un attuatore mono-direzionale come la
// pompa non puo' mai contrastare "tirando giu'" il valore).
const PID_INTEGRAL_TIME_SECONDS = 4 * 3600;
// Tempo di integrazione del Predictive per N/P/K — molto piu' lungo di
// quello del PID: vedi _PREDICTIVE_INTEGRAL_TIME_SECONDS in parameters.py.
// Il dosaggio dei fertilizzanti e' troppo lento/raro (gated dall'irrigazione,
// minimo un'ora fra dosi) perche' un errore di poche ore — normale durante
// la salita verso un nuovo setpoint di fase — vada scambiato per un bias
// stazionario da correggere subito.
const PREDICTIVE_INTEGRAL_TIME_SECONDS = 3 * 24 * 3600;

function buildStrategyParameters(strategy, target, variableKey) {
  const setpoint = target ? target.setpoint : 0;
  const min = target ? target.allowed_range.minimum : 0;
  const max = target ? target.allowed_range.maximum : Math.max(setpoint + 1, 1);
  const doseOnly = NUTRIENT_VARIABLES.includes(variableKey);
  if (strategy === "Threshold") {
    return {
      lower_threshold: min, upper_threshold: max, direction: "increases",
      active_command: doseOnly ? 2.0 : 1.0,
      inactive_command: 0.0, bidirectional: false,
    };
  }
  if (strategy === "PID") {
    // Guadagno proporzionale scalato sulla meta' banda attorno al setpoint
    // (stessa idea del response_gain del Predictive sotto): un errore
    // grande quanto la banda spinge il comando (quasi) al suo massimo, ma
    // vicino al setpoint il comando si affievolisce di conseguenza — un
    // guadagno fisso, applicato a variabili con bande/comandi di scala
    // enormemente diversa (umidita': decine di punti percentuali contro un
    // massimo di 0.5 L), restava saturato per qualunque errore non
    // trascurabile, comportandosi come un bang-bang travestito da PID.
    const [commandMin, commandMax] = doseOnly ? [0.0, 3.0] : NON_DOSE_COMMAND_LIMITS[variableKey];
    const margin = target ? Math.max(max - setpoint, setpoint - min, 1e-6) : 1.0;
    const proportionalGain = commandMax / margin;
    const integralGain = proportionalGain / PID_INTEGRAL_TIME_SECONDS;
    return {
      setpoint, proportional_gain: proportionalGain, integral_gain: integralGain,
      derivative_gain: 0.0, command_minimum: commandMin, command_maximum: commandMax,
      direction: "increases",
    };
  }
  // command_maximum e' una scala di comando (litri/watt/mL a seconda
  // dell'attuatore), non va confusa con `max` sopra — quella e' la banda
  // della VARIABILE controllata (es. 170 mg/L), un'unita' completamente
  // diversa: usarla qui produrrebbe comandi enormi e privi di senso fisico.
  {
    const [commandMin, commandMax] = doseOnly ? [0.0, 3.0] : NON_DOSE_COMMAND_LIMITS[variableKey];
    // response_gain scalato sulla banda della fase, stessa formula e stessa
    // ragione del proportional_gain del PID sopra.
    const margin = target ? Math.max(max - setpoint, setpoint - min, 1e-6) : 1.0;
    const responseGain = commandMax / margin;
    // water_dilution_gain/substrate_gain hanno senso solo per un dosaggio
    // di fertilizzante (correggono la stima N/P/K per la diluizione data
    // dall'acqua appena irrigata e per il fattore del substrato — vedi
    // PredictiveController::compute in controllers.cpp): per una variabile
    // non nutritiva restano a zero invece di riusare per errore un valore
    // tarato per i nutrienti.
    const [waterDilutionGain, substrateGain] = doseOnly
      ? NUTRIENT_PREDICTIVE_GAINS[variableKey]
      : [0.0, 0.0];
    // Stessa idea del PID sopra, ma con una costante di tempo molto più
    // lunga (PREDICTIVE_INTEGRAL_TIME_SECONDS): il dosaggio dei fertilizzanti
    // è troppo lento/raro perché un errore di poche ore significhi un bias
    // reale da correggere, invece del normale transitorio di una salita
    // verso un nuovo setpoint di fase.
    const integralGain = responseGain / PREDICTIVE_INTEGRAL_TIME_SECONDS;
    return {
      setpoint, prediction_horizon_steps: 1.0, response_gain: responseGain, neutral_command: 0.0,
      command_minimum: commandMin, command_maximum: commandMax,
      direction: "increases",
      water_dilution_gain: waterDilutionGain,
      // Azzerato deliberatamente: un guadagno positivo incondizionato qui
      // continua a spingere il comando verso l'alto finche' resta dose di
      // fase da erogare, anche quando l'errore e' gia' nullo o negativo —
      // il bug v4 di catalog.py (vedi la sua nota su _predictive_parameters),
      // per cui il valore medio di N/P/K restava incollato sopra il
      // setpoint invece di convergerci.
      cumulative_dose_gain: 0.0,
      substrate_gain: substrateGain,
      integral_gain: integralGain,
    };
  }
}

/* ------------------------------------------------------------------ */
/* System status pill                                                 */
/* ------------------------------------------------------------------ */

// Updates every copy of the backend-status indicator by class — there are
// two in the DOM (the sidebar footer, always present, and the login
// screen's own copy, shown before the sidebar exists), kept in sync from
// this single poll rather than duplicating it per screen.
async function tickSystemStatus() {
  const dots = document.querySelectorAll(".status-dot");
  const nameEls = document.querySelectorAll(".status-name");
  const detailEls = document.querySelectorAll(".status-detail");
  try {
    const [root, health] = await Promise.all([apiGet("/"), apiGet("/health")]);
    dots.forEach((el) => { el.className = "status-dot ok"; });
    nameEls.forEach((el) => { el.textContent = `${root.name} v${root.version}`; });
    detailEls.forEach((el) => { el.textContent = health.status === "healthy" ? "operativo" : health.status; });
  } catch (e) {
    dots.forEach((el) => { el.className = "status-dot down"; });
    nameEls.forEach((el) => { el.textContent = "SmartHydro Backend"; });
    detailEls.forEach((el) => { el.textContent = "non raggiungibile"; });
  }
}

/* ------------------------------------------------------------------ */
/* Home view: /zones grouped into 5 departments x <=2 sectors          */
/* ------------------------------------------------------------------ */

async function tickZones() {
  try {
    const zones = await apiGet("/zones");
    STATE.zones = zones;
    STATE.zonesLoaded = true;
    if (STATE.modalZoneId) {
      const z = zones.find((zz) => zz.id === STATE.modalZoneId);
      if (z) STATE.modalZone = z;
    }
    if (STATE.view === "home") {
      // Don't yank focus/reset an in-progress "Aggiungi settore" form on a
      // background poll tick — same precedent as the Control view below.
      if (!isFocusedInside("view-home")) renderHome();
    } else if (STATE.view === "control") {
      // See renderControlIfSafe()/isFocusedInControlFilter() near
      // renderControl() — narrower than a blanket isFocusedInside check so
      // a focused "Applica a tutto l'impianto" button (every browser keeps
      // DOM focus on a button after it's clicked) doesn't block this poll
      // from ever showing that click's results.
      renderControlIfSafe();
    } else if (STATE.view === "simulator") {
      // The grid has no inputs to protect focus on; the pop-up (if open)
      // is a separate DOM subtree (#modal-content) untouched by this.
      renderSimulatorView();
    }
    updateNavAlertsBadge();
  } catch (e) {
    console.error("failed to refresh zones", e);
  }
}

/** Wraps already-rendered department cards (one per DEPT_ORDER entry, each
 * carrying data-dept="N" — see renderDeptCard/renderSimulatorDeptCard) in
 * the "piantina della serra" chrome: an outer wall, two decorative
 * entrance tabs, and a "Corridoio centrale" bar between the two rows the
 * grid naturally falls into (1-2-3 on top, 4-5 below — see the named
 * grid-template-areas on .dept-grid in styles.css, which is what actually
 * places d1..d5/corridor/dgap; this function only ever supplies the HTML,
 * never the layout math). Shared by Home (renderHome/renderDeptGridOnly)
 * and Simulatore (renderSimulatorView) — same building, two different
 * pages looking at it. */
function renderGreenhousePlan(deptCardsHtml) {
  return `
    <div class="greenhouse-plan">
      <span class="greenhouse-entrance greenhouse-entrance-top">Ingresso principale</span>
      <div class="dept-grid">
        ${deptCardsHtml}
        <div class="greenhouse-corridor">Corridoio centrale</div>
        <div class="greenhouse-gap"><span>Attrezzi</span></div>
      </div>
      <span class="greenhouse-entrance greenhouse-entrance-bottom">Ingresso di servizio</span>
    </div>
  `;
}

function renderHome() {
  const zones = STATE.zones;
  const byDept = groupZonesByDepartment(zones);
  const total = zones.length;
  const online = zones.filter((z) => z.status === "online").length;
  const degraded = zones.filter((z) => z.operational_state === "Degraded").length;
  const emergency = zones.filter((z) => z.operational_state === "EmergencyLockdown").length;

  setPageTitle("Serra", `${total} settori registrati (massimo 9: 4 reparti produttivi × 2 settori + quarantena × 1) · ${online} online`);

  const deptCards = DEPT_ORDER.map((n) => renderDeptCard(n, byDept[n] || [])).join("");

  document.getElementById("view-home").innerHTML = `
    <div class="home-layout">
      ${renderGreenhousePlan(deptCards)}
      <aside class="home-summary">
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
      <div class="dept-card" data-dept="${n}" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
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
    // Nothing to grow, nothing to select: a department with zero catalogued
    // recipes can't populate the species dropdown, so the action is
    // disabled outright rather than opening a form with an empty select.
    // Before recipes have loaded at all we don't yet know either way, so
    // the button stays enabled (it self-corrects once STATE.recipesLoaded
    // flips true — see maybeFetchHomeExtras/renderDeptGridOnly).
    const noRecipes = STATE.recipesLoaded && recipesForDepartment(n).length === 0;
    return `<button type="button" class="sector-slot-add" data-action="open-add-sector" data-dept="${n}" data-sector="${sn}" ${noRecipes ? `disabled title="Nessuna ricetta disponibile per questo reparto: aggiungine una dalla pagina Ricette prima di creare un nuovo settore."` : ""}>+ Aggiungi settore</button>`;
  }).join("");
  return `
    <div class="dept-card" data-dept="${n}" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
      <div class="dept-card-head">
        <span class="dept-code">REPARTO ${n}</span>
        <span class="dept-count">${zones.length} settor${zones.length === 1 ? "e" : "i"}</span>
      </div>
      <div class="dept-title">${escapeHtml(name)}</div>
      <div class="sector-list">${slots}</div>
    </div>
  `;
}

/** All catalogued recipes for department `n` (1-4) — the source for the
 * "Aggiungi settore" species dropdown and for deciding whether that action
 * should even be offered (zero recipes → nothing to grow, nothing to
 * select). Reads from the shared STATE.recipes cache, fed either by the
 * Recipes page (tickRecipes) or, if Home is visited first, by
 * maybeFetchHomeExtras — never fetched per-department on its own. */
function recipesForDepartment(n) {
  return STATE.recipes.filter((r) => r.department_number === n);
}

function renderAddSectorForm(f) {
  const sending = f.status === "sending";
  const options = recipesForDepartment(f.dept);
  const selectedRecipe = options.find((r) => r.id === f.recipeId) || null;

  if (f.confirmStep) {
    return `
      <div class="add-sector-form">
        <div class="field-label">Nuovo settore ${f.sector}</div>
        <p style="font-size:12.5px;color:var(--ink-mute);line-height:1.5;margin:0">
          Stai per creare questo settore con specie <b>${escapeHtml(selectedRecipe ? selectedRecipe.plant_type : "—")}</b>.
          La specie coltivata di un settore non è pensata per essere cambiata spesso: verificala prima di confermare.
        </p>
        ${f.error ? `<div class="add-sector-error">${escapeHtml(f.error)}</div>` : ""}
        <div class="add-sector-actions">
          <button type="button" class="btn" data-action="cancel-add-sector-confirm" ${sending ? "disabled" : ""}>Indietro</button>
          <button type="button" class="btn btn-primary" data-action="submit-add-sector" ${sending ? "disabled" : ""}>${sending ? "Creazione…" : "Conferma creazione"}</button>
        </div>
      </div>
    `;
  }

  return `
    <div class="add-sector-form">
      <div class="field-label">Nuovo settore ${f.sector}</div>
      <select class="add-sector-input" data-action="add-sector-recipe-select" ${sending ? "disabled" : ""}>
        <option value="" ${f.recipeId ? "" : "selected"} disabled>Specie coltivata…</option>
        ${options.map((r) => `<option value="${escapeAttr(r.id)}" ${f.recipeId === r.id ? "selected" : ""}>${escapeHtml(r.plant_type)}</option>`).join("")}
      </select>
      ${f.error ? `<div class="add-sector-error">${escapeHtml(f.error)}</div>` : ""}
      <div class="add-sector-actions">
        <button type="button" class="btn" data-action="cancel-add-sector" ${sending ? "disabled" : ""}>Annulla</button>
        <button type="button" class="btn btn-primary" data-action="proceed-add-sector" ${sending || !f.recipeId ? "disabled" : ""}>Avanti</button>
      </div>
    </div>
  `;
}

/**
 * id is auto-generated as "r{dept}-s{sector}" (matches the convention of
 * every existing zone id and the backend's id pattern) and so is name
 * ("Reparto N — Settore M", the same wording as the zone-detail header) —
 * neither is ever asked from the user, since a settore's name is barely
 * surfaced anywhere in the UI. Species is not free text either: it comes
 * from the recipe picked in the dropdown, and that same recipe's id is
 * sent as active_recipe_id in the same request, so the two can never
 * silently disagree the way a free-text species string could.
 */
async function submitAddSector() {
  const form = STATE.addSectorForm;
  if (!form || form.status === "sending" || !form.confirmStep) return;
  const recipe = recipesForDepartment(form.dept).find((r) => r.id === form.recipeId);
  if (!recipe) {
    STATE.addSectorForm = { ...form, error: "La ricetta selezionata non è più disponibile: torna indietro e riprova." };
    renderHome();
    return;
  }

  const zoneId = `r${form.dept}-s${form.sector}`;
  STATE.addSectorForm = { ...form, status: "sending", error: null };
  renderHome();

  try {
    const created = await apiPost("/zones", {
      id: zoneId,
      name: `Reparto ${form.dept} — Settore ${form.sector}`,
      department_number: form.dept,
      sector_number: form.sector,
      plant_species: recipe.plant_type,
      active_recipe_id: recipe.id,
    });
    STATE.zones.push(created);
    STATE.addSectorForm = null;
    renderHome();
    showToast(`Settore ${zoneLabel(created)} creato con specie "${recipe.plant_type}".`);
  } catch (err) {
    if (STATE.addSectorForm) {
      STATE.addSectorForm = { ...STATE.addSectorForm, status: null, error: err.message };
      renderHome();
    }
  }
}

/**
 * Shared innards of a sector tile — connection dot, species, phase/state/
 * plant-count tags — used both by Home's real sector row (renderSectorRow)
 * and the Simulatore page's own grid (renderSimulatorSectorRow, see the
 * "Simulatore" section below). Only the OUTER wrapper differs between the
 * two (data-action target, clickability, trailing arrow) — keeping the
 * inner markup in one place means the two pages can never silently drift
 * apart in what a sector tile actually shows.
 *
 * showConnection (default true) toggles ONLY the online/offline dot + label:
 * real connectivity state is meaningless inside the Simulatore, which is by
 * definition an isolated, non-live scenario — showing it there risks
 * suggesting the simulation reflects the zone's real connection, which it
 * never does. Home's own sector row never passes this (stays true); the
 * Simulatore grid (renderSimulatorSectorRow) passes false. Every other tag
 * (species, phase, safety badge, plant count) is unaffected either way.
 *
 * The plant-count tag is identified by data-plant-count (not an id): the
 * same zone can now legitimately appear in two different grids at once
 * (Home's and Simulatore's DOM subtrees both stay mounted, just toggled
 * with .hidden — see switchView), so an id would collide; renderPlantCounts
 * below updates every matching element via querySelectorAll instead of
 * getElementById.
 */
function renderSectorRowInner(z, { showConnection = true } = {}) {
  const online = z.status === "online";
  const species = z.plant_species || (z.department_number === 5 ? "Zona mista (quarantena)" : "—");
  return `
    ${showConnection ? `<span class="conn-dot ${online ? "online" : "offline"}"></span>` : ""}
    <div class="sector-row-body">
      <div class="sector-row-top">
        <span class="sector-num">SETTORE ${z.sector_number}</span>
        ${showConnection ? `<span class="sector-conn" style="color:${online ? "#2f9e6b" : "#c15a4a"}">${online ? "ONLINE" : "OFFLINE"}</span>` : ""}
      </div>
      <div class="sector-row-species">${escapeHtml(species)}</div>
      <div class="sector-row-tags">
        <span class="tag-phase">${escapeHtml(z.current_phase || "nessuna fase attiva")}</span>
        <span class="pill op-${z.operational_state}">${z.operational_state}</span>
        <span class="tag-phase" data-plant-count="${escapeAttr(z.id)}">${escapeHtml(plantCountLabel(z.id))}</span>
      </div>
    </div>
  `;
}

function renderSectorRow(z) {
  return `
    <div class="sector-row" data-action="open-zone" data-zone-id="${escapeAttr(z.id)}">
      ${renderSectorRowInner(z)}
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
    document.querySelectorAll(`[data-plant-count="${z.id}"]`).forEach((el) => {
      el.textContent = plantCountLabel(z.id);
    });
  });
}

// Despite the name, also called from the Simulatore page (renderSimulatorView):
// its own sector tiles show the same live plant count as Home's, via the
// shared renderSectorRowInner — reusing this single throttled fetch instead
// of a second one. renderAlertsPanel/renderQuarantineBox/renderDeptGridOnly
// below all no-op harmlessly when their target element isn't in the
// currently-visible view's DOM (see each function's own `if (!el) return`).
async function maybeFetchHomeExtras() {
  const now = Date.now();
  if (now - STATE.homeExtrasLastRun < HOME_EXTRAS_MIN_INTERVAL_MS) return;
  STATE.homeExtrasLastRun = now;
  // The recipe catalog is needed on Home too — it decides whether each
  // department's "+ Aggiungi settore" button should be enabled — but it
  // rarely changes, so it's fetched once and cached (STATE.recipesLoaded),
  // not on every throttled tick the way alerts/quarantine/plantCounts are.
  // If the Recipes page already loaded it this session, this is a no-op.
  const needsRecipes = !STATE.recipesLoaded;
  try {
    const tasks = [loadAlerts(STATE.zones), loadQuarantineStats(), loadPlantCounts(STATE.zones)];
    if (needsRecipes) tasks.push(apiGet("/recipes"));
    const [alerts, quarantine, plantCounts, recipes] = await Promise.all(tasks);
    STATE.alerts = alerts;
    STATE.quarantine = quarantine;
    STATE.plantCounts = plantCounts;
    if (needsRecipes) {
      STATE.recipes = recipes;
      STATE.recipesLoaded = true;
    }
  } catch (e) {
    console.error("failed to refresh home extras", e);
  }
  STATE.homeExtrasLoaded = true;
  renderAlertsPanel();
  renderQuarantineBox();
  renderPlantCounts();
  updateNavAlertsBadge();
  // The department grid's "+ Aggiungi settore" buttons read
  // STATE.recipesLoaded/recipesForDepartment() — if the catalog just
  // finished loading for the first time, repaint them immediately instead
  // of waiting for the next zones poll to happen to redraw the grid.
  if (needsRecipes && STATE.view === "home" && !isFocusedInside("view-home")) renderDeptGridOnly();
}

/** Repaints only the department cards (not the whole Home layout) — used
 * when something that affects a card's rendering (e.g. the recipe catalog
 * finishing its first load) changes without the zone list itself changing,
 * so there's no need to rebuild the aside panels too. */
function renderDeptGridOnly() {
  const grid = document.querySelector("#view-home .dept-grid");
  if (!grid) return;
  const byDept = groupZonesByDepartment(STATE.zones);
  const deptCards = DEPT_ORDER.map((n) => renderDeptCard(n, byDept[n] || [])).join("");
  // Rebuilding just the cards would also wipe the corridor/gap filler
  // (renderGreenhousePlan) since they live inside this same .dept-grid —
  // put them back rather than only ever rendering them via a full
  // renderHome().
  grid.innerHTML = `
    ${deptCards}
    <div class="greenhouse-corridor">Corridoio centrale</div>
    <div class="greenhouse-gap"></div>
  `;
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

// Backs the Ricette page; also re-renders the Simulatore page and its
// pop-up on every tick (the sector grid itself reads only STATE.zones, no
// recipe fields — kept in sync anyway since both pages share this one
// poll/cache instead of fetching GET /recipes twice).
async function tickRecipes() {
  try {
    const recipes = await apiGet("/recipes");
    STATE.recipes = recipes;
    STATE.recipesLoaded = true;
    STATE.recipesById = Object.fromEntries(recipes.map((r) => [r.id, r]));
    if (STATE.view === "recipes") {
      if (isFocusedInside("view-recipes")) updateRecipeGridOnly();
      else renderRecipes();
    } else if (STATE.view === "simulator") {
      // The grid itself has no inputs to protect focus on; the pop-up
      // (if open) might have a chart-variable <select> mid-interaction,
      // same guard tickModal()/tickSimulationJob() already use elsewhere.
      renderSimulatorView();
      if (STATE.modalKind === "simulator") renderModalIfSafe();
    }
  } catch (e) {
    console.error("failed to refresh recipes", e);
    if (STATE.view === "recipes") {
      document.getElementById("view-recipes").innerHTML = `<div class="empty-note">Impossibile caricare le ricette: ${escapeHtml(e.message)}</div>`;
    } else if (STATE.view === "simulator") {
      document.getElementById("view-simulator").innerHTML = `<div class="empty-note">Impossibile caricare le ricette: ${escapeHtml(e.message)}</div>`;
    }
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
        <div class="empty-note">Durata fase: ${fmtNum(phase.duration_hours, 1)} h · fotoperiodo dalle ${fmtNum(phase.photoperiod.start_hour, 1)} per ${fmtNum(phase.photoperiod.duration_hours, 1)} h (fino alle ${fmtNum(photoEnd, 1)}) · setpoint e banda sono definiti a livello di ricetta, la Strategia a livello di impianto dalla pagina Controllo — nessuno dei due è modificabile da questa vista.</div>
      </div>
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Simulatore — isolated batch preview page (POST /simulations)         */
/*                                                                       */
/* Deliberately kept separate from every operational code path above:   */
/* it never touches a zone, a command, or telemetry — POST /simulations */
/* only ever reads the picked sector's active_recipe_id ONCE, at the    */
/* moment its pop-up is opened. The backend allows exactly ONE batch    */
/* simulation system-wide at a time (409 if another is already queued/  */
/* running), so this page is careful to free that slot (DELETE) the     */
/* moment the user stops watching — see discardActiveSimulationIfAny(), */
/* called both from closeModal() (STATE.modalKind === "simulator") and  */
/* from switchView() (navigating away from the Simulatore page          */
/* entirely, pop-up open or not).                                       */
/*                                                                       */
/* Page layout: the SAME department-card grid as "Serra"                */
/* (renderDeptCard/renderSectorRow in the Home section above), reused    */
/* via renderSimulatorSectorRow/renderSimulatorDeptCard rather than a   */
/* catalog-wide recipe picker — restricted to departments 1-4 (no       */
/* Quarantena: department 5 never has an active_recipe_id) and, within  */
/* those, only real registered sectors. A sector tile opens/focuses the */
/* one shared "Simula l'intera serra" pop-up (openGreenhouseSimulator   */
/* Modal, STATE.modalKind = "simulator") on that sector — a separate    */
/* pop-up from the real zone-detail one (openZoneModal/"zone"): the two */
/* must never converge into the same component/handler, since one is   */
/* read-only live data and the other starts/reads a batch job.          */
/* ------------------------------------------------------------------ */

/** Frees the global batch-simulation slot if the current job is still
 * queued/running when the user leaves the Simulatore page entirely
 * (switchView) or starts a genuinely new run (restartSimulationSetup) —
 * fire-and-forget, since there's nothing more to show regardless of
 * whether the DELETE itself succeeds. Closing the pop-up alone does NOT
 * call this (see closeModal): the job is meant to keep running/polling in
 * the background while the user is still just browsing sectors on this
 * same page. A no-op if there is no job, or the job already reached a
 * final state (nothing to free). */
function discardActiveSimulationIfAny() {
  const sim = STATE.simulation;
  clearPoll("simulation-job");
  if (!sim || !sim.job) return;
  if (sim.job.status === "queued" || sim.job.status === "running") {
    apiDelete(`/simulations/${encodeURIComponent(sim.job.id)}`).catch(() => {});
  }
}

function restartSimulationSetup() {
  const sim = STATE.simulation;
  if (!sim) return;
  clearPoll("simulation-job");
  STATE.simulation = {
    greenhouse: true,
    durationDays: sim.durationDays || 7,
    starting: false,
    job: null,
    result: null,
    activeZoneId: null,
    error: null,
    chartVariable: sim.chartVariable || "soil_moisture",
    chartView: sim.chartView || "single",
    chartLayers: sim.chartLayers || {},
    showActuatorStrips: sim.showActuatorStrips,
  };
  renderModal();
}

/** True when `a` and `b` are still "the same" simulation session as far as
 * a late async response is concerned — there is only the one-of-a-kind
 * "Simula l'intera serra" session now, so this only ever needs to check
 * that both actually exist. Used by startSimulation to ignore a
 * POST/error that resolves after the pop-up moved on (closed and a
 * genuinely new run started via "Nuova simulazione" in the meantime). */
function isSameSimulationSession(a, b) {
  return !!a && !!b;
}

async function startSimulation() {
  const sim = STATE.simulation;
  if (!sim || sim.starting) return;
  const durationSeconds = sim.durationDays * 86400;
  sim.starting = true;
  sim.error = null;
  renderModal();
  try {
    const job = await apiPost("/simulations", { duration_seconds: durationSeconds });
    if (!isSameSimulationSession(STATE.simulation, sim) || STATE.modalKind !== "simulator") return; // pop-up closed/changed meanwhile
    STATE.simulation.starting = false;
    STATE.simulation.job = job;
    STATE.simulation.result = null;
    renderModal();
    pollSimulationJob();
  } catch (err) {
    if (!isSameSimulationSession(STATE.simulation, sim) || STATE.modalKind !== "simulator") return;
    STATE.simulation.starting = false;
    // 409 here means one of two normal, expected conditions — never the
    // raw backend detail: either the system's one global batch slot is
    // already taken (SimulationBusy), or no zone has a recipe assigned at
    // all (SimulationInvalid, routes.py) — distinguished by the backend's
    // own detail text, since both map to the same HTTP status.
    STATE.simulation.error = err.status === 409
      ? ((err.message || "").includes("nothing to simulate")
          ? "Nessun settore ha una ricetta assegnata: non c'è nulla da simulare."
          : "È già in corso un'altra simulazione nel sistema, riprova tra poco.")
      : err.message;
    renderModal();
  }
}

async function cancelSimulation() {
  const sim = STATE.simulation;
  if (!sim || !sim.job) return;
  const runId = sim.job.id;
  clearPoll("simulation-job");
  try {
    await apiDelete(`/simulations/${encodeURIComponent(runId)}`);
  } catch (e) { /* best effort — the job expires on its own regardless */ }
  if (STATE.simulation && STATE.simulation.job && STATE.simulation.job.id === runId) {
    STATE.simulation.job = null;
    STATE.simulation.result = null;
    STATE.simulation.error = null;
    renderModal();
  }
}

function pollSimulationJob() {
  setPoll("simulation-job", tickSimulationJob, SIMULATION_POLL_MS);
}

async function tickSimulationJob() {
  const sim = STATE.simulation;
  if (!sim || !sim.job) { clearPoll("simulation-job"); return; }
  if (sim.job.status !== "queued" && sim.job.status !== "running") { clearPoll("simulation-job"); return; }
  const runId = sim.job.id;
  try {
    const job = await apiGet(`/simulations/${encodeURIComponent(runId)}`);
    if (!STATE.simulation || !STATE.simulation.job || STATE.simulation.job.id !== runId) return; // stale
    STATE.simulation.job = job;
    if (job.status === "succeeded") {
      clearPoll("simulation-job");
      try {
        const result = await apiGet(`/simulations/${encodeURIComponent(runId)}/result`);
        if (STATE.simulation && STATE.simulation.job && STATE.simulation.job.id === runId) {
          STATE.simulation.result = result;
        }
      } catch (err) {
        if (STATE.simulation && STATE.simulation.job && STATE.simulation.job.id === runId) {
          STATE.simulation.error = err.message;
        }
      }
    } else if (job.status === "failed" || job.status === "cancelled") {
      clearPoll("simulation-job");
    }
    if (STATE.modalKind === "simulator") renderModalIfSafe();
  } catch (e) { /* transient network error, keep polling */ }
}

/**
 * Simulatore page: the department-card grid, restricted to production
 * departments (1-4) and — within those — sectors that actually have a
 * recipe assigned (active_recipe_id). --sim chrome on the intro banner/
 * page title matches the app's normal green (--accent) rather than a
 * separate color, so the page reads as one visual system with "Reparti
 * della serra"; see renderSimulatorSectorRow for the equally-deliberate
 * "not clickable" treatment of a production sector that has no recipe
 * assigned yet.
 */
function renderSimulatorView() {
  const byDept = groupZonesByDepartment(STATE.zones);
  const simulable = STATE.zones.filter((z) => z.department_number !== 5 && !!z.active_recipe_id);

  setPageTitle(
    "Simulatore",
    `${simulable.length} settor${simulable.length === 1 ? "e" : "i"} disponibil${simulable.length === 1 ? "e" : "i"} per la simulazione · nessun dato reale coinvolto`
  );

  // Reparto 5 (quarantena) never has an active_recipe_id (see
  // ZoneCreate._validate_department_role backend-side) and so is never
  // simulable — shown as a muted, non-interactive room all the same
  // rather than leaving a hole in the floor plan (renderGreenhousePlan
  // always expects all 5).
  const deptCards = [1, 2, 3, 4].map((n) => renderSimulatorDeptCard(n, byDept[n] || [])).join("")
    + renderSimulatorQuarantineCard();

  document.getElementById("view-simulator").innerHTML = `
    <div class="simulator-intro simulator-intro-prominent">
      <div class="simulator-intro-title">Ambiente di simulazione</div>
      <div class="simulator-intro-text">
        Questa griglia rispecchia i settori reali della serra — stessa specie, stessa fase, stesso stato — ma "Simula
        l'intera serra" avvia un unico scenario batch isolato che copre ogni settore con una ricetta assegnata, tutti
        sullo stesso arco temporale: <strong>non tocca mai la telemetria, i comandi o le coltivazioni reali</strong>.
        Cliccando un settore vedi il suo grafico dentro quella simulazione. Ogni risultato è marcato esplicitamente
        <strong>«Scenario simulato — non operativo»</strong>.
      </div>
      ${simulable.length > 0 ? `
      <div class="simulator-intro-action">
        <button type="button" class="btn btn-primary" data-action="open-greenhouse-simulator">Simula l'intera serra</button>
        <span class="hint">un'unica simulazione per tutti i ${simulable.length} settor${simulable.length === 1 ? "e" : "i"} con ricetta assegnata — stesso arco temporale per tutti</span>
      </div>` : ""}
    </div>
    ${renderGreenhousePlan(deptCards)}
    ${simulable.length === 0 && STATE.zonesLoaded
      ? '<div class="simulator-empty" style="margin-top:20px">Nessun settore produttivo ha ancora una ricetta assegnata: non c\'è ancora nulla da simulare.</div>'
      : ""}
  `;
  maybeFetchHomeExtras(); // plant counts shown on each tile — same throttled fetch Home uses
}

/** Reparto 5's room in the Simulatore floor plan — never clickable (see
 * the comment above its call site): quarantena has no recipe and nothing
 * to simulate, so it's rendered muted instead of omitted, keeping all 5
 * reparti visible as rooms on both pages. */
function renderSimulatorQuarantineCard() {
  const meta = DEPT_META[5];
  return `
    <div class="dept-card dept-card-muted" data-dept="5" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
      <div class="dept-card-head"><span class="dept-code">REPARTO 5</span></div>
      <div class="dept-title">${escapeHtml(DEPT_FALLBACK_NAMES[5])}</div>
      <div class="simulator-empty" style="margin:auto 0">Zona di quarantena: non coltivabile, quindi non simulabile.</div>
    </div>
  `;
}

/** One department card for the Simulatore grid — same visual shell as
 * renderDeptCard (Home), but only ever showing REAL registered sectors
 * (no "+ Aggiungi settore" slots: this page never creates anything) and
 * routing clicks through renderSimulatorSectorRow instead of
 * renderSectorRow. */
function renderSimulatorDeptCard(n, zones) {
  const meta = DEPT_META[n];
  const name = (zones[0] && zones[0].department_name) || DEPT_FALLBACK_NAMES[n];
  const rows = zones.length
    ? zones.map((z) => renderSimulatorSectorRow(z)).join("")
    : '<div class="sector-slot-empty">Nessun settore registrato in questo reparto.</div>';
  return `
    <div class="dept-card" data-dept="${n}" style="--dept-tint:${meta.tint};--dept-border:${meta.border};--dept-accent:${meta.accent}">
      <div class="dept-card-head">
        <span class="dept-code">REPARTO ${n}</span>
        <span class="dept-count">${zones.length} settor${zones.length === 1 ? "e" : "i"}</span>
      </div>
      <div class="dept-title">${escapeHtml(name)}</div>
      <div class="sector-list">${rows}</div>
    </div>
  `;
}

/** A single sector tile on the Simulatore grid — same inner content as
 * Home's own sector row (species/phase/state/plant-count, via the shared
 * renderSectorRowInner), but clicking it opens/focuses that settore inside
 * the shared "Simula l'intera serra" pop-up (data-action=
 * "open-greenhouse-simulator", same action as the page-level button, just
 * with a data-zone-id — see openGreenhouseSimulatorModal). There is no
 * per-sector isolated simulation anymore: a settore's own chart only ever
 * comes from the one greenhouse-wide run. A production sector without an
 * active_recipe_id renders as a plain, non-clickable tile instead of one
 * that would show nothing useful on click: no data-action, muted styling,
 * no arrow. */
function renderSimulatorSectorRow(z) {
  // showConnection:false — vedi il commento su renderSectorRowInner: online/
  // offline è connettività reale, fuori luogo in un contesto che simula
  // sempre uno scenario isolato. Le altre informazioni della card (specie,
  // fase, badge di sicurezza, conteggio piante) restano identiche a Home.
  const inner = renderSectorRowInner(z, { showConnection: false });
  if (!z.active_recipe_id) {
    return `
      <div class="sector-row sector-row-disabled" title="Nessuna ricetta assegnata a questo settore: non simulabile.">
        ${inner}
        <span class="sector-row-arrow" style="visibility:hidden">→</span>
      </div>
    `;
  }
  return `
    <div class="sector-row" data-action="open-greenhouse-simulator" data-zone-id="${escapeAttr(z.id)}">
      ${inner}
      <span class="sector-row-arrow">→</span>
    </div>
  `;
}

/**
 * Opens (or brings back to front) the single, shared "Simula l'intera
 * serra" pop-up — the only way to run or view a simulation now, no
 * per-sector isolated run left (see renderSimulatorSectorRow). Called both
 * by the page-level "Simula l'intera serra" button (no preferredZoneId)
 * and by clicking a sector row (preferredZoneId set), so a sector's own
 * chart is only ever reached through this one greenhouse-wide run.
 *
 * Reopening an already queued/running/completed simulation does NOT reset
 * it — closing the pop-up alone never discards STATE.simulation either
 * (see closeModal); only leaving the Simulatore page entirely (switchView)
 * or starting a genuinely new run does. preferredZoneId just refocuses
 * which settore's chart is shown once a result exists.
 */
function openGreenhouseSimulatorModal(preferredZoneId) {
  if (!STATE.simulation) {
    STATE.simulation = {
      greenhouse: true,
      durationDays: 7,
      starting: false,
      job: null,
      result: null,
      activeZoneId: preferredZoneId || null,
      error: null,
      chartVariable: "soil_moisture",
      chartView: "single",
      chartLayers: {},
      showActuatorStrips: true,
    };
  } else if (preferredZoneId) {
    STATE.simulation.activeZoneId = preferredZoneId;
  }
  STATE.modalKind = "simulator";
  document.getElementById("modal-overlay").classList.remove("hidden");
  renderModal();
}

/** Pop-up body: setup / progress / result, exactly the same three-state
 * body previously used inline on the Simulatore page — only the
 * surrounding header changed (now the sector + recipe it was opened for,
 * plus a compact repeat of the "Ambiente di simulazione" banner — see the
 * NEW LAYOUT note 3 this was written against: the grid looks enough like
 * the real one that the warning belongs inside the pop-up too, not only
 * above the grid). */
/** "Simula l'intera serra" pop-up body: setup / progress / result — the
 * only simulation pop-up now (see openGreenhouseSimulatorModal), so no
 * recipe/zone to fetch first: the header names the greenhouse-wide run,
 * not a single settore/ricetta, and setup/progress/result render
 * immediately without an async load step. */
function renderSimulatorModal() {
  const wrap = document.getElementById("modal-content");
  const sim = STATE.simulation;
  if (!sim) { wrap.innerHTML = '<div class="empty-note">Nessuna simulazione selezionata.</div>'; return; }

  const zoneCount = (sim.job && sim.job.zone_ids && sim.job.zone_ids.length)
    || (Array.isArray(sim.result) ? sim.result.length : STATE.zones.filter((z) => z.department_number !== 5 && !!z.active_recipe_id).length);

  let body;
  if (sim.result) {
    body = renderSimulationResult(sim);
  } else if (sim.job && (sim.job.status === "queued" || sim.job.status === "running")) {
    body = renderSimulationProgress(sim);
  } else {
    body = renderSimulationSetup(sim);
  }

  wrap.innerHTML = `
    <div class="zone-header" style="--zone-tint:var(--sim-soft)">
      <div style="min-width:0">
        <div class="zone-header-code"><span class="code">Intera serra</span></div>
        <h2>Simulazione dell'intera serra</h2>
        <div class="zone-header-meta">
          <span>${zoneCount} settor${zoneCount === 1 ? "e" : "i"} con ricetta assegnata, ognuno con la propria ricetta</span>
        </div>
      </div>
      <button type="button" class="zone-close" data-action="close-modal">✕</button>
    </div>
    <div style="padding:20px 28px 28px">
      <div class="simulator-intro simulator-intro-compact">
        <div class="simulator-intro-title">Ambiente di simulazione</div>
        <div class="simulator-intro-text">
          Ogni settore produttivo con una ricetta assegnata avanza sullo stesso arco temporale, ciascuno con la propria
          ricetta — resta comunque un'anteprima batch isolata: <strong>non tocca mai la telemetria, i comandi o le
          coltivazioni reali</strong>.
        </div>
      </div>
      ${body}
    </div>
  `;
  if (sim.result) { drawSimulationChart(); }
}

function renderSimulationSetup(sim) {
  const disabled = sim.starting;
  const failedNote = sim.job && sim.job.status === "failed"
    ? `<div class="zone-danger-error" style="margin-top:12px">Simulazione non riuscita: ${escapeHtml(sim.job.error || "errore sconosciuto")}</div>`
    : "";
  const cancelledNote = sim.job && sim.job.status === "cancelled"
    ? `<div class="empty-note" style="margin-top:12px">Simulazione annullata.</div>`
    : "";
  return `
    <div class="zone-section" style="margin-top:16px">
      <div class="zone-section-title">Durata della simulazione</div>
      <div class="sim-preset-row">
        ${SIMULATION_DURATION_PRESETS.map((p) => `
          <button type="button" class="btn ${sim.durationDays === p.days ? "btn-primary" : ""}"
            data-action="sim-select-duration" data-days="${p.days}" ${disabled ? "disabled" : ""}>${p.label}</button>
        `).join("")}
      </div>
      ${sim.error ? `<div class="zone-danger-error" style="margin-top:12px">${escapeHtml(sim.error)}</div>` : ""}
      ${failedNote}
      ${cancelledNote}
      <div style="display:flex;justify-content:flex-end;margin-top:16px">
        <button type="button" class="btn btn-primary" data-action="sim-start" ${disabled ? "disabled" : ""}>${disabled ? "Avvio…" : "Avvia simulazione"}</button>
      </div>
    </div>
  `;
}

function renderSimulationProgress(sim) {
  const job = sim.job;
  const pct = job.progress_percent;
  const label = job.status === "queued" ? "In coda…" : `In corso — ${fmtNum(pct, 0)}%`;
  return `
    <div class="zone-section" style="margin-top:16px">
      <div class="zone-section-title">Simulazione in corso</div>
      <div class="sim-progress-track"><div class="sim-progress-fill" style="width:${Math.max(job.status === "queued" ? 3 : 0, pct)}%"></div></div>
      <div class="empty-note" style="margin-top:10px">${escapeHtml(label)} · ${job.completed_steps}/${job.total_steps} cicli di controllo simulati</div>
      <div style="display:flex;justify-content:flex-end;margin-top:16px">
        <button type="button" class="btn btn-danger-ghost" data-action="sim-cancel">Annulla simulazione</button>
      </div>
    </div>
  `;
}

/** "Umidità del terriccio (%)" / "pH" style label+unit combo for one
 * variable — used by the sensor chart's legend so it's obvious which
 * physical quantity, and in what unit, the simulated line represents
 * (VARIABLES.label alone omits the unit; pH's own unit equals its label,
 * so that one case is left bare instead of showing "pH · pH"). */
function varLegendLabel(v) {
  if (!v.unit || v.unit === v.label) return v.label;
  return `${v.label} · ${v.unit}`;
}

/** The single-chart-view legend's third item, describing the second
 * (blue) line drawn by drawSimulationSeriesChart — a 24h rolling average
 * for every variable except light. For light that rolling average (see the
 * git history of this function) turned out too noisy to read against the
 * setpoint: the simulated weather makes raw PPFD swing a lot hour to hour,
 * so a window recomputed at every bucket zigzagged well above/below the
 * target with no stable meaning. DLI is BY DEFINITION a daily total, not a
 * rolling average — so for light this line is instead lightCompletedDayTotal
 * (see drawSimulationSeriesChart), a step line holding the most recently
 * FINISHED solar day's total DLI constant until the next day finishes too:
 * exactly the value the green sawtooth's tip reaches right before it resets,
 * kept visible (not just for an instant) so it's actually readable against
 * the dashed target line. Carries the id sim-chart-legend-average so the
 * variable dropdown's change handler can patch it in place, the same
 * targeted-DOM-update pattern already used for sim-chart-legend-label —
 * switching variable never re-renders the whole modal, only redraws the
 * canvas. */
function simAverageLegendItem(varMeta) {
  return varMeta.key === "light"
    ? `<span class="legend-item" id="sim-chart-legend-average"><span class="legend-average"></span>DLI totale dell'ultimo giorno concluso (a gradini, un salto per giorno — confronta questo col setpoint; la linea verde è il DLI maturato da inizio giornata odierna, si azzera ogni notte)</span>`
    : `<span class="legend-item" id="sim-chart-legend-average"><span class="legend-average"></span>Media mobile 24h (confronta questa col setpoint, non il valore istantaneo)</span>`;
}

/** The single-chart-view legend's second item, describing the shaded band
 * drawn by drawSimulationSeriesChart. For every variable except light it's
 * a closed range (fill from allowed_minimum to allowed_maximum) — "stay
 * inside this". For light there is no ceiling to respect (no actuator
 * reduces natural sunlight, only lamps that can supplement a deficit — see
 * control_system.cpp's LIGHT branch, which only ever compares against
 * target.setpoint, never allowed_maximum): the fill there instead covers
 * everything ABOVE the minimum up to the top of the chart (see the isLight
 * branch of the phases.forEach band-drawing loop), so the caption needs to
 * say "minimum", not "band", or a value sitting comfortably above target
 * reads as if it broke out of a range it was supposed to stay inside.
 * Carries the id sim-chart-legend-band for the same targeted-DOM-patch
 * pattern as sim-chart-legend-label/-average. */
function simBandLegendItem(varMeta) {
  return varMeta.key === "light"
    ? `<span class="legend-item" id="sim-chart-legend-band"><span class="legend-band"></span>Minimo richiesto di fase (tratteggio = setpoint) — sopra va sempre bene, non esiste un tetto</span>`
    : `<span class="legend-item" id="sim-chart-legend-band"><span class="legend-band"></span>Banda target di fase (tratteggio = setpoint)</span>`;
}

/** The SimulationPreview currently on screen. For "Simula un settore" (the
 * original mode) sim.result is a single preview object, straight from
 * GET /simulations/{id}/result. For "Simula l'intera serra" (STATE.
 * simulation.greenhouse) that same endpoint returns an ARRAY instead — one
 * preview per zone with an assigned recipe — and sim.activeZoneId (see the
 * zone <select> in renderSimulationResult) picks which one every chart and
 * summary below reads from; everything downstream stays written against a
 * single preview object either way. */
function activeSimulationPreview(sim) {
  if (!sim || !sim.result) return null;
  if (!Array.isArray(sim.result)) return sim.result;
  return sim.result.find((p) => p.zone_id === sim.activeZoneId) || sim.result[0] || null;
}

/** Label for one entry of the "Simula l'intera serra" zone <select> — the
 * preview only carries zone_id (see SimulationPreview.zone_id server-side),
 * everything human-readable is resolved locally from STATE.zones, same as
 * the rest of the app (zoneLabel). Falls back to the bare id for a zone
 * removed mid-simulation (defensive only — a batch preview never mutates
 * zones, so this should not normally happen). */
function simZoneOptionLabel(preview) {
  const zone = STATE.zones.find((z) => z.id === preview.zone_id);
  return zone ? `${zoneLabel(zone)} · ${preview.recipe.plant_type}` : preview.zone_id;
}

function renderSimulationResult(sim) {
  const greenhouse = Array.isArray(sim.result);
  const result = activeSimulationPreview(sim);
  const gridView = sim.chartView === "grid";
  const varMeta = VARIABLES_BY_KEY[sim.chartVariable];
  return `
    <div class="sim-nonop-banner" style="margin-top:16px">${escapeHtml(result.source_label)}</div>
    ${greenhouse ? `
    <div class="zone-section" style="margin-top:16px">
      <div class="chart-head" style="margin-bottom:0">
        <span class="title">Settore mostrato</span>
        <select id="sim-zone-select">
          ${sim.result.map((p) => `<option value="${escapeAttr(p.zone_id)}" ${p.zone_id === result.zone_id ? "selected" : ""}>${escapeHtml(simZoneOptionLabel(p))}</option>`).join("")}
        </select>
        <span class="hint">${sim.result.length} settori simulati sullo stesso arco temporale</span>
      </div>
    </div>` : ""}
    <div class="zone-section" style="margin-top:16px">
      <div class="chart-head">
        <span class="title">Andamento simulato</span>
        ${gridView ? "" : `
        <select id="sim-chart-variable-select">
          ${VARIABLES.map((v) => `<option value="${v.key}" ${sim.chartVariable === v.key ? "selected" : ""}>${v.label}</option>`).join("")}
        </select>`}
        <button type="button" class="chart-view-toggle" data-action="sim-chart-view-toggle">
          ${gridView ? "◧ Un grafico alla volta" : "▦ Vedi tutti i grafici"}
        </button>
        <span class="hint">durata simulata: ${fmtSimDuration(result.duration_seconds)}</span>
      </div>
      <div class="chart-legend">
        ${gridView
          ? `<span class="legend-item"><span class="legend-line"></span>Valore simulato</span>`
          : `<span class="legend-item"><span class="legend-line"></span><span id="sim-chart-legend-label">${escapeHtml(varLegendLabel(varMeta))} — valore simulato</span></span>`}
        ${gridView
          ? `<span class="legend-item"><span class="legend-band"></span>Banda/minimo target di fase (tratteggio = setpoint; per la Luce sopra va sempre bene, vedi il titolo del riquadro)</span>`
          : simBandLegendItem(varMeta)}
        ${gridView ? "" : simAverageLegendItem(varMeta)}
      </div>
      <div class="sim-chart-toggles">
        <span class="hint">Linee da mostrare:</span>
        <label class="sim-chart-toggle"><input type="checkbox" data-action="sim-chart-layer-toggle" data-layer="value" ${sim.chartLayers?.value === false ? "" : "checked"}> Valore misurato</label>
        <label class="sim-chart-toggle"><input type="checkbox" data-action="sim-chart-layer-toggle" data-layer="band" ${sim.chartLayers?.band === false ? "" : "checked"}> Banda</label>
        <label class="sim-chart-toggle"><input type="checkbox" data-action="sim-chart-layer-toggle" data-layer="setpoint" ${sim.chartLayers?.setpoint === false ? "" : "checked"}> Setpoint</label>
        <label class="sim-chart-toggle"><input type="checkbox" data-action="sim-chart-layer-toggle" data-layer="average" ${sim.chartLayers?.average === false ? "" : "checked"}> Valore medio</label>
        <label class="sim-chart-toggle"><input type="checkbox" data-action="sim-actuator-strip-toggle" ${sim.showActuatorStrips === false ? "" : "checked"}> Attuatori<span class="legend-glyph active" style="margin-left:5px">■</span> attivo <span class="legend-glyph inactive">□</span> spento</label>
      </div>
      ${gridView
        ? `<div class="sim-chart-grid">
            ${VARIABLES.map((v) => `
              <div class="sim-chart-grid-cell">
                <div class="sim-chart-grid-title">${escapeHtml(varLegendLabel(v))}${v.key === "light" ? " · minimo di fase" : ""}</div>
                <canvas id="simulation-chart-${v.key}" class="sim-chart-grid-canvas"></canvas>
                ${sim.showActuatorStrips === false ? "" : `<canvas id="simulation-actuator-strip-${v.key}" class="sim-chart-grid-strip"></canvas>`}
              </div>`).join("")}
          </div>`
        : `<div class="chart-canvas-wrap"><canvas id="simulation-chart" style="width:100%;height:100%;display:block"></canvas></div>
          ${sim.showActuatorStrips === false ? "" : `<canvas id="simulation-actuator-strip-single" class="sim-actuator-strip-single"></canvas>`}`}
    </div>
    <div class="zone-section" style="margin-top:16px">
      <div class="zone-section-title">Riepilogo</div>
      ${renderSimulationSummary(result.summary)}
    </div>
    <div style="display:flex;justify-content:flex-end;margin-top:16px">
      <button type="button" class="btn" data-action="sim-restart">Nuova simulazione</button>
    </div>
  `;
}

const SIM_FERTILIZER_LABELS = {
  nitrogen: "Azoto (N)", phosphorus: "Fosforo (P)", potassium: "Potassio (K)",
  "ph-up": "pH+", "ph-down": "pH−",
};
const SIM_ACTUATOR_LABELS = {
  water_pump: "Pompa acqua", lighting: "Barra LED",
  valve_nitrogen: "Elettrovalvola azoto (N)", valve_phosphorus: "Elettrovalvola fosforo (P)",
  valve_potassium: "Elettrovalvola potassio (K)", "valve_ph-up": "Elettrovalvola pH+",
  "valve_ph-down": "Elettrovalvola pH−",
};

/** Which actuator_intervals[*].actuator key(s) belong to each VARIABLES
 * entry — used by that variable's own companion actuator strip (see
 * drawActuatorStrip) to filter the full actuator_intervals list down to
 * just the actuator(s) that affect this one variable. pH lists both
 * valves: a single thin row has no room for a per-direction breakdown, so
 * "active" there means "either ph-up or ph-down was open". */
const VARIABLE_ACTUATOR_KEYS = {
  soil_moisture: ["water_pump"],
  light: ["lighting"],
  ph: ["valve_ph-up", "valve_ph-down"],
  nitrogen: ["valve_nitrogen"],
  phosphorus: ["valve_phosphorus"],
  potassium: ["valve_potassium"],
};

/** Short gutter label drawn on each variable's own actuator strip —
 * distinct from VARIABLE_ACTUATOR_KEYS' backend interval keys, this is
 * just the human-readable short name shown on the canvas itself. */
const VARIABLE_ACTUATOR_LABEL = {
  soil_moisture: "Acqua",
  light: "Luce",
  ph: "pH+/pH−",
  nitrogen: "N",
  phosphorus: "P",
  potassium: "K",
};

function renderSimulationSummary(summary) {
  const fertRows = Object.entries(summary.delivered_fertilizer_milliliters || {})
    .map(([k, v]) => `<div class="kv-row"><span class="k">${escapeHtml(SIM_FERTILIZER_LABELS[k] || k)}</span><b class="v">${fmtNum(v, 0)} mL</b></div>`)
    .join("");
  const actuatorEntries = Object.entries(summary.actuator_active_seconds || {}).sort((a, b) => b[1] - a[1]);
  const actRows = actuatorEntries
    .map(([k, v]) => `<div class="kv-row"><span class="k">${escapeHtml(SIM_ACTUATOR_LABELS[k] || k)}</span><b class="v">${fmtDurationHM(v)}</b></div>`)
    .join("");
  return `
    <div class="kv-row"><span class="k">Acqua erogata (totale)</span><b class="v">${fmtNum(summary.delivered_water_liters, 1)} L</b></div>
    <div class="status-sep"></div>
    <div class="field-label" style="margin:10px 0 6px">Fertilizzante erogato</div>
    ${fertRows || '<div class="empty-note">Nessun fertilizzante erogato.</div>'}
    <div class="status-sep"></div>
    <div class="field-label" style="margin:10px 0 6px">Tempo di attivazione attuatori</div>
    ${actRows || '<div class="empty-note">Nessun attuatore attivato.</div>'}
    <div class="status-sep"></div>
    <div class="kv-row"><span class="k">Cicli di controllo calcolati</span><b class="v">${summary.control_cycles}</b></div>
  `;
}

/** "30 giorni" style phrasing for the result hint text (duration_seconds
 * is always a whole number of days here since every preset is). */
function fmtSimDuration(totalSeconds) {
  const days = Math.round(totalSeconds / 86400);
  return days === 1 ? "1 giorno" : `${days} giorni`;
}

/** Compact elapsed-time axis label ("3h", "2g 6h") — distinct from
 * fmtSimDuration (whole-days phrasing for the header hint) since chart
 * axis ticks land at arbitrary points within the simulated span. */
function fmtElapsedSeconds(totalSeconds) {
  const totalMinutes = Math.round(Math.max(0, totalSeconds) / 60);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  if (days > 0) return hours > 0 ? `${days}g ${hours}h` : `${days}g`;
  return `${hours}h`;
}

/** "7h 45m" style phrasing for a raw seconds duration (summary's
 * actuator_active_seconds) — same shape as fmtElapsed but from a plain
 * seconds count instead of "elapsed since an ISO timestamp". */
function fmtDurationHM(totalSeconds) {
  const totalMinutes = Math.round(Math.max(0, totalSeconds) / 60);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}g ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

/** Draws either the single selected-variable canvas, or (chartView ===
 * "grid", see the "Vedi tutti i grafici" toggle) every one of the 6
 * VARIABLES into its own small canvas at once — same underlying
 * drawSimulationSeriesChart either way, just once per variable in grid
 * mode, and both pass sim.chartLayers through so the "Linee da mostrare"
 * toggle panel (value/band/setpoint/average — which LINES to draw inside
 * every chart, not which charts to show) applies identically everywhere.
 * Every chart (grid AND the single selected one) also gets its own
 * companion actuator strip right underneath, unless sim.showActuatorStrips
 * is off — see drawActuatorStrip. There is no combined multi-row Gantt
 * anymore (removed): each chart's own strip is now the only place
 * actuator activity shows up, one row scoped to exactly that variable
 * instead of a shared 7-row timeline you had to scroll to and cross-
 * reference by eye. */
function drawSimulationChart() {
  const sim = STATE.simulation;
  const preview = activeSimulationPreview(sim);
  if (!preview) return;
  const minT = preview.series[0]?.start_seconds ?? 0;
  const maxT = preview.series[preview.series.length - 1]?.end_seconds ?? 0;
  if (sim.chartView === "grid") {
    VARIABLES.forEach((v) => {
      const canvas = document.getElementById(`simulation-chart-${v.key}`);
      if (canvas) drawSimulationSeriesChart(canvas, preview.series, preview.phases, v, sim.chartLayers);
      if (sim.showActuatorStrips === false) return;
      const stripCanvas = document.getElementById(`simulation-actuator-strip-${v.key}`);
      if (stripCanvas) {
        drawActuatorStrip(
          stripCanvas,
          preview.actuator_intervals || [],
          VARIABLE_ACTUATOR_KEYS[v.key] || [],
          VARIABLE_ACTUATOR_LABEL[v.key] || "",
          minT,
          maxT);
      }
    });
    return;
  }
  const canvas = document.getElementById("simulation-chart");
  if (canvas) {
    const varMeta = VARIABLES_BY_KEY[sim.chartVariable];
    drawSimulationSeriesChart(canvas, preview.series, preview.phases, varMeta, sim.chartLayers);
  }
  if (sim.showActuatorStrips === false) return;
  const stripCanvas = document.getElementById("simulation-actuator-strip-single");
  if (stripCanvas) {
    drawActuatorStrip(
      stripCanvas,
      preview.actuator_intervals || [],
      VARIABLE_ACTUATOR_KEYS[sim.chartVariable] || [],
      VARIABLE_ACTUATOR_LABEL[sim.chartVariable] || "",
      minT,
      maxT);
  }
}

// Finestra della media mobile mostrata sopra la curva grezza — un giorno
// solare intero. Per la luce questo è indispensabile: il setpoint di fase
// è la luce MEDIA che deve arrivare alla pianta nell'arco della giornata,
// non un livello istantaneo, quindi confrontarlo con l'andamento grezzo
// (che segue il ciclo giorno/notte, zero di notte e un picco di giorno) non
// dice nulla — va confrontato con una media su un ciclo completo. Per le
// altre variabili la stessa finestra aiuta a rispondere alla stessa
// domanda in generale ("la media converge sul setpoint, o resta scostata
// per via del dosaggio a impulsi mono-direzionale?"), quindi si applica a
// ogni grafico, non solo a quello della luce.
const SIM_ROLLING_AVERAGE_WINDOW_SECONDS = 24 * 3600;

/**
 * Media mobile "trailing" pesata sulla durata di ogni bucket: per ogni punto
 * ricalcola la media di tutti i bucket (anche parziali, ai bordi della
 * finestra) i cui intervalli [t0,t1] ricadono almeno in parte nelle 24 ore
 * precedenti. I bucket non hanno tutti la stessa durata (vedi
 * _reduce_series lato backend, che ne allarga la dimensione per le
 * simulazioni lunghe), da cui la ponderazione per durata invece che una
 * semplice media aritmetica degli ultimi N punti. All'inizio della
 * simulazione la finestra è inevitabilmente più corta di 24h (si allarga
 * mano a mano che ci sono più dati precedenti): non è un errore, converge
 * al pieno intervallo dopo il primo giorno.
 */
function rollingAverage(points, windowSeconds) {
  const result = new Array(points.length);
  for (let i = 0; i < points.length; i++) {
    const windowEnd = points[i].t1;
    const windowStart = windowEnd - windowSeconds;
    let weightedSum = 0;
    let totalWeight = 0;
    for (let j = i; j >= 0; j--) {
      const p = points[j];
      if (p.t1 <= windowStart) break;
      const overlap = Math.min(p.t1, windowEnd) - Math.max(p.t0, windowStart);
      if (overlap <= 0) continue;
      weightedSum += p.avg * overlap;
      totalWeight += overlap;
    }
    result[i] = totalWeight > 0 ? weightedSum / totalWeight : points[i].avg;
  }
  return result;
}

/**
 * Unlike drawTelemetryChart (a single flat setpoint line, since live data
 * only ever has ONE current setpoint), a simulation spans potentially many
 * phases with different targets — and, unlike the live-data case, we know
 * exactly when each phase starts and ends, so the target band is drawn
 * precisely per phase instead of approximated. Phases past the recipe's
 * own total duration don't exist (a finished recipe just holds on its
 * last phase), so the last phase's band is extended to the end of the
 * simulated span rather than leaving a gap.
 */
function drawSimulationSeriesChart(canvas, series, phases, varMeta, layers) {
  // Quattro livelli disegnabili indipendentemente, ciascuno spentabile da
  // sim.chartLayers (vedi il pannello di spunte "Linee da mostrare" sopra
  // la griglia/il grafico singolo — non "quali variabili mostrare", che
  // era il fraintendimento della versione precedente): value = valore
  // misurato (linea verde + la sua fascia min/max osservata per bucket),
  // band = banda/minimo target ombreggiato, setpoint = riga tratteggiata,
  // average = il valore comparabile col setpoint (media mobile 24h, o per
  // la luce il DLI a gradini — vedi isLight sotto). undefined = mostrata,
  // stessa convenzione "assente = true" usata altrove in questo file.
  const showValue = layers?.value !== false;
  const showBand = layers?.band !== false;
  const showSetpoint = layers?.setpoint !== false;
  const showAverage = layers?.average !== false;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 1);
  const height = Math.max(rect.height, 1);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!series.length) {
    ctx.fillStyle = "#8aa39a";
    ctx.font = "11px Poppins, sans-serif";
    ctx.fillText("Nessun dato nella simulazione", 16, height / 2);
    return;
  }

  const key = varMeta.simKey;
  let points = series
    .map((s) => ({ t0: s.start_seconds, t1: s.end_seconds, avg: s.average[key], min: s.minimum[key], max: s.maximum[key] }))
    .filter((p) => isFinite(p.avg) && p.avg !== null && p.avg !== undefined);
  if (!points.length) {
    ctx.fillStyle = "#8aa39a";
    ctx.font = "11px Poppins, sans-serif";
    ctx.fillText("Variabile non disponibile in questa simulazione", 16, height / 2);
    return;
  }

  // Il target della luce è ormai un DLI giornaliero (mol/m²/giorno, vedi
  // ControlledVariable.LIGHT), non più un livello PPFD istantaneo: il PPFD
  // grezzo (centinaia di µmol/m²s, zero di notte) e il target (poche
  // decine di mol/m²) non sono confrontabili sulla stessa scala — è
  // esattamente il problema segnalato. Per la luce disegniamo quindi il
  // DLI maturato dall'inizio del giorno solare corrente (stesso confine
  // "giorno" — floor(secondi/86400) — usato lato Edge per azzerare
  // l'accumulo reale), non il PPFD: una curva a dente di sega che riparte
  // da zero ogni notte e si confronta direttamente con la riga tratteggiata
  // del target giornaliero.
  const isLight = varMeta.key === "light";
  const kSecondsPerDay = 86400;
  // Il dente di sega (sotto) resta indispensabile per seguire l'andamento
  // reale entro la giornata, ma da solo non da' un singolo valore stabile
  // da confrontare col target: per quello serve una seconda linea — vedi
  // pero' simAverageLegendItem per il perche' NON e' una media mobile 24h
  // ricalcolata punto per punto (prima versione: troppo rumorosa contro un
  // meteo simulato variabile, "non comprensibile rispetto al setpoint").
  // Il DLI e' un totale giornaliero: la metrica giusta e' quindi il totale
  // dell'ultimo giorno solare gia' CONCLUSO, calcolato nello stesso passaggio
  // che costruisce il dente di sega sotto (stesso confine di giorno,
  // floor(secondi/86400)) — lightCompletedDayTotal[i] e' quel totale,
  // allineato per indice a points, null finche' nessun giorno e' ancora
  // concluso (durante il primissimo giorno di simulazione).
  const lightCompletedDayTotal = isLight ? new Array(points.length).fill(null) : null;
  if (isLight) {
    let dayIndex = null;
    let cumulative = 0;
    let previousDayTotal = null;
    points = points.map((p, i) => {
      const pointDay = Math.floor(p.t0 / kSecondsPerDay);
      if (dayIndex === null) {
        dayIndex = pointDay;
      } else if (pointDay !== dayIndex) {
        previousDayTotal = cumulative;
        cumulative = 0;
        dayIndex = pointDay;
      }
      cumulative += (p.avg * (p.t1 - p.t0)) / 1e6;
      lightCompletedDayTotal[i] = previousDayTotal;
      return { t0: p.t0, t1: p.t1, avg: cumulative, min: cumulative, max: cumulative };
    });
  }
  const displayUnit = isLight ? "mol/m²" : varMeta.unit;
  const displayDecimals = isLight ? 1 : varMeta.decimals;

  const pad = { l: 46, r: 14, t: 14, b: 20 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;

  const minT = series[0].start_seconds;
  const maxT = series[series.length - 1].end_seconds;
  const spanT = Math.max(maxT - minT, 1);

  const lightDailyTotalsForScale = isLight
    ? lightCompletedDayTotal.filter((v) => v !== null)
    : null;
  let minV = Math.min(...points.map((p) => p.min), ...(lightDailyTotalsForScale || []));
  let maxV = Math.max(...points.map((p) => p.max), ...(lightDailyTotalsForScale || []));
  // Solo le fasi che ricadono davvero nell'intervallo simulato [minT, maxT]
  // contano per la scala dell'asse — esattamente lo stesso controllo di
  // sovrapposizione usato sotto per decidere se disegnare la banda di una
  // fase. Senza questo filtro, una simulazione breve che resta sempre nella
  // prima fase vedrebbe comunque l'asse allungato fino ai target (spesso
  // molto più ampi) delle fasi successive mai raggiunte, schiacciando la
  // curva vicino al fondo pur essendo perfettamente in banda.
  phases.forEach((ph) => {
    const segStart = Math.max(ph.start_seconds, minT);
    const segEnd = Math.min(ph.end_seconds, maxT);
    if (segEnd <= segStart) return;
    const t = ph.targets[varMeta.key];
    if (t) { minV = Math.min(minV, t.allowed_minimum); maxV = Math.max(maxV, t.allowed_maximum); }
  });
  if (minV === maxV) { minV -= 1; maxV += 1; }
  const spanPad = (maxV - minV) * 0.1;
  minV -= spanPad;
  maxV += spanPad;

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

  // Target band + setpoint, per phase — the last phase's band is extended
  // to maxT since the Edge just holds on it once the recipe is "done".
  phases.forEach((ph, i) => {
    const target = ph.targets[varMeta.key];
    if (!target) return;
    const segStart = Math.max(ph.start_seconds, minT);
    const segEnd = i === phases.length - 1 ? maxT : Math.min(ph.end_seconds, maxT);
    const bx0 = x(segStart);
    const bx1 = x(segEnd);
    if (bx1 <= bx0) return;
    if (showBand) {
      ctx.fillStyle = "rgba(63,122,96,0.10)";
      if (isLight) {
        // Per la luce non esiste un tetto da rispettare (nessun attuatore
        // riduce il sole, vedi control_system.cpp — solo un minimo da
        // colmare): l'ombreggiatura "zona conforme" copre quindi tutto lo
        // spazio SOPRA il minimo fino in cima al grafico, non un intervallo
        // chiuso come per le altre variabili — coerente con la stessa
        // convenzione "ombreggiato = conforme" usata sotto.
        ctx.fillRect(bx0, pad.t, bx1 - bx0, y(target.allowed_minimum) - pad.t);
      } else {
        ctx.fillRect(bx0, y(target.allowed_maximum), bx1 - bx0, y(target.allowed_minimum) - y(target.allowed_maximum));
      }
    }
    if (showSetpoint) {
      ctx.strokeStyle = "#c9803f";
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(bx0, y(target.setpoint));
      ctx.lineTo(bx1, y(target.setpoint));
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (i > 0) {
      ctx.strokeStyle = "#d8e2da";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(bx0, pad.t);
      ctx.lineTo(bx0, pad.t + h);
      ctx.stroke();
    }
  });

  // Observed min/max spread per bucket (visible mainly on aggregated,
  // long-duration simulations where each point covers many raw steps).
  if (points.length && showValue) {
    ctx.fillStyle = "rgba(31,122,81,0.14)";
    ctx.beginPath();
    points.forEach((p, i) => {
      const px = x((p.t0 + p.t1) / 2);
      const py = y(p.max);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    for (let i = points.length - 1; i >= 0; i--) {
      const px = x((points[i].t0 + points[i].t1) / 2);
      ctx.lineTo(px, y(points[i].min));
    }
    ctx.closePath();
    ctx.fill();

    // Average line.
    ctx.strokeStyle = "#1f7a51";
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
      const px = x((p.t0 + p.t1) / 2);
      const py = y(p.avg);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  if (points.length && showAverage) {
    if (isLight) {
      // Linea a gradini (non una media mobile, vedi simAverageLegendItem):
      // un salto netto per giorno concluso, mai una diagonale — necessario
      // esplicitamente perche' su simulazioni lunghe e aggregate (bucket
      // larghi, vedi _reduce_series lato backend) un semplice lineTo fra
      // due punti su giorni diversi disegnerebbe una diagonale invece di
      // un gradino netto.
      ctx.strokeStyle = "#3d6ea5";
      ctx.lineWidth = 2;
      ctx.beginPath();
      let started = false;
      let lastPy = null;
      points.forEach((p, i) => {
        const v = lightCompletedDayTotal[i];
        if (v === null) return; // nessun giorno ancora concluso
        const px = x((p.t0 + p.t1) / 2);
        const py = y(v);
        if (!started) {
          ctx.moveTo(px, py);
          started = true;
        } else if (py !== lastPy) {
          ctx.lineTo(px, lastPy);
          ctx.lineTo(px, py);
        } else {
          ctx.lineTo(px, py);
        }
        lastPy = py;
      });
      if (started) ctx.stroke();
    } else {
      // Media mobile 24h — quella da confrontare col setpoint tratteggiato,
      // non la linea grezza sopra (vedi SIM_ROLLING_AVERAGE_WINDOW_SECONDS).
      const rolling = rollingAverage(points, SIM_ROLLING_AVERAGE_WINDOW_SECONDS);
      ctx.strokeStyle = "#3d6ea5";
      ctx.lineWidth = 2;
      ctx.beginPath();
      points.forEach((p, i) => {
        const px = x((p.t0 + p.t1) / 2);
        const py = y(rolling[i]);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
  }

  ctx.fillStyle = "#8aa39a";
  ctx.font = "10px 'IBM Plex Mono', monospace";
  ctx.fillText(`${maxV.toFixed(displayDecimals)} ${displayUnit}`, 2, pad.t + 8);
  ctx.fillText(`${minV.toFixed(displayDecimals)} ${displayUnit}`, 2, pad.t + h);
  ctx.fillText(fmtElapsedSeconds(minT), pad.l, height - 4);
  ctx.textAlign = "right";
  ctx.fillText(fmtElapsedSeconds(maxT), pad.l + w, height - 4);
  ctx.textAlign = "left";
}

/**
 * Compact one-row companion strip drawn directly under a variable chart
 * (grid cell or the single selected one — see drawSimulationChart/
 * sim.showActuatorStrips), showing when THAT variable's own actuator(s)
 * were active. There used to also be one combined 7-row Gantt shared by
 * every chart (removed — redundant now that every chart has its own row,
 * and it forced scrolling down + cross-referencing by eye which row
 * belonged to which chart). actuatorKeys (VARIABLE_ACTUATOR_KEYS) selects
 * which actuator_intervals[*].actuator values count as "active" for this
 * one strip — a pH strip lists two keys (ph-up, ph-down): "active" means
 * "either one", since a single thin row has no room for a per-direction
 * breakdown. `label` (VARIABLE_ACTUATOR_LABEL) is the short gutter text
 * identifying which actuator this row is, since there is no shared row
 * gutter to label it once the way the old combined Gantt did.
 *
 * minT/maxT are passed in by the caller (not derived from `series` here)
 * so this stays pixel-aligned with the chart canvas immediately above it,
 * which computes the exact same values from the exact same series — and
 * pad.l/pad.r match drawSimulationSeriesChart's for the same reason, even
 * though this strip has no y-axis labels of its own to make room for.
 */
function drawActuatorStrip(canvas, intervals, actuatorKeys, label, minT, maxT) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 1);
  const height = Math.max(rect.height, 1);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const pad = { l: 46, r: 14 };
  const w = width - pad.l - pad.r;
  const spanT = Math.max(maxT - minT, 1);
  const x = (t) => pad.l + ((t - minT) / spanT) * w;

  ctx.fillStyle = "#eef3ef";
  ctx.fillRect(pad.l, 0, w, height);

  ctx.fillStyle = "rgba(31,122,81,0.55)";
  intervals
    .filter((iv) => actuatorKeys.includes(iv.actuator) && iv.start_seconds <= maxT)
    .forEach((iv) => {
      const end = Math.min(iv.end_seconds, maxT);
      if (end <= iv.start_seconds) return;
      const bx0 = x(iv.start_seconds);
      const bx1 = x(end);
      if (bx1 <= bx0) return;
      ctx.fillRect(bx0, 1, Math.max(bx1 - bx0, 1.5), height - 2);
    });

  ctx.fillStyle = "#8aa39a";
  ctx.font = "9px 'IBM Plex Mono', monospace";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 2, height / 2);
  ctx.textBaseline = "alphabetic";
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
    // No selected_strategy here: Strategy is a plant-wide setting (see
    // Controllo/renderGlobalStrategyPanel), not something this form lets
    // the user choose per recipe — buildRecipePayload() reads the current
    // one from STATE.controlStrategy when it assembles the payload.
    controllers: Object.fromEntries(VARIABLES.map((v) => [v.key, {
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
    // See buildBlankDraft's comment: c.selected_strategy (whatever the
    // backend last stamped this recipe with) isn't editable here.
    controllers: Object.fromEntries(recipe.controllers.map((c) => [c.variable, {
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
 * PhaseVariableTarget._check_range_chain: every rule checked here is
 * checked again server-side, so this exists purely to turn a would-be 422
 * into a clear Italian message before the request is ever sent.
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
    // Strategy is plant-wide now (see Controllo/renderGlobalStrategyPanel):
    // this form has no per-variable choice to read, so the payload just
    // carries the current global setting — the backend re-derives Strategy
    // and parameters from it on every read regardless (recipes/
    // repository.py), so this is a schema-satisfying placeholder, not what
    // ends up in effect.
    const selected = currentControlStrategy(v.key);
    const firstTarget = phases[0].targets.find((t) => t.variable === v.key);
    return {
      variable: v.key,
      input_source: REQUIRED_INPUT_SOURCE[v.key],
      actuator: REQUIRED_ACTUATOR[v.key],
      default_strategy: REQUIRED_DEFAULT_STRATEGY[v.key],
      selected_strategy: selected,
      parameters: buildStrategyParameters(selected, firstTarget, v.key),
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

  // The Strategy cells below read STATE.controlStrategy — load it once per
  // visit, same lazy pattern as ensureUsersLoaded()/renderUserManagementPanel.
  const cs = STATE.controlStrategy;
  if (!cs.loaded && !cs.loading) ensureControlStrategyLoaded();

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
    // Strategy si sceglie da Controllo, non qui — vedi lo stesso pattern
    // di sola lettura in renderZoneAdvanced/renderZoneSummary.
    const strategyCell = `<div class="readonly-cell" title="La Strategia si imposta a livello di impianto dalla pagina Controllo, non per singola ricetta">${escapeHtml(currentControlStrategy(v.key))}</div>`;
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
/* Control view: plant-wide Strategy panel + all sectors in a table   */
/*                                                                    */
/* Amministratore-only page (see isAdmin()/switchView()). The panel   */
/* below sends PUT /control-strategy/{variable}: the backend persists  */
/* the choice once for the whole impianto (used by every future        */
/* ricetta/settore/simulazione — see recipes/repository.py's read-time */
/* stamping), then does the same "one ChangeStrategy(+Confirm          */
/* Configuration) command per currently-active production zone" fan-   */
/* out server-side that this file used to do itself (see               */
/* backend/app/features/control_strategy/routes.py). What's left here  */
/* is just showing each zone's real live-confirmation outcome — not a  */
/* single generic "fatto", since that fan-out can (and, on a zone      */
/* that isn't Running, will) succeed for some sectors and not others.  */
/* ------------------------------------------------------------------ */

/** Whether the one thing worth protecting from a background re-render on
 * Controllo — a filter <select> the admin has open/mid-choosing — currently
 * has focus. Deliberately narrower than isFocusedInside("view-control"):
 * that blanket check also matches a just-clicked "Applica a tutto
 * l'impianto" button, which keeps DOM focus after a click in every
 * browser — so using it here would block renderControlIfSafe() from ever
 * showing the per-zone results a click just kicked off, until the admin
 * happened to click/tab somewhere else. A focused button (or a focused
 * global-strategy <select> — its choice already survives a re-render via
 * STATE.controlStrategy.drafts, same as the old per-zone code protected
 * strategyDrafts) has nothing left to lose from being rebuilt underneath it.
 * (The "Gestione utenti" create-account fields used to need the same
 * protection here too, back when that panel was embedded in this page —
 * now that it's its own "Utenti" view, see isFocusedInUserForm() instead.) */
function isFocusedInControlFilter() {
  const active = document.activeElement;
  return !!(active && active.closest && active.closest('[data-action="control-filter"]'));
}

/** Only re-renders while the Amministratore is actually on Controllo, and
 * never while a filter <select> has focus — see isFocusedInControlFilter(). */
function renderControlIfSafe() {
  if (STATE.view === "control" && !isFocusedInControlFilter()) renderControl();
}

function clearGlobalStrategyPolls() {
  STATE.controlAdhocIntervals.forEach(clearInterval);
  STATE.controlAdhocIntervals = [];
  // A variable batch still "sending"/"waiting" when the admin navigates
  // away is left at whatever per-zone results it already has — marked
  // "done" so a later revisit doesn't show a stuck spinner for a poll that
  // isn't actually running anymore.
  const status = STATE.controlStrategy.status;
  Object.keys(status).forEach((k) => {
    if (status[k] === "sending" || status[k] === "waiting") status[k] = "done";
  });
}

/** Returns a cached full recipe (with `phases`, needed by e.g. the
 * Simulatore's phase/target lookups) or fetches+caches it — mirrors the
 * same STATE.recipesById cache openRecipeModal()/tickRecipes() use, so a
 * recipe already seen on the Ricette/Simulatore pages isn't re-fetched. */
async function ensureRecipeLoaded(recipeId) {
  if (!recipeId) return null;
  const cached = STATE.recipesById[recipeId];
  if (cached) return cached;
  const recipe = await apiGet(`/recipes/${encodeURIComponent(recipeId)}`);
  STATE.recipesById[recipe.id] = recipe;
  return recipe;
}

function globalStrategyResultText(r) {
  if (r.status === "waiting") return "in attesa…";
  if (r.status === "success") return "confermata ✓";
  if (r.status === "timeout") return "nessuna conferma (verifica che sia Running)";
  return "";
}

function onGlobalStrategyDraftChange(variableKey, value) {
  STATE.controlStrategy.drafts[variableKey] = value;
  renderControlIfSafe();
}

function finishGlobalStrategyPoll(iv, variableKey) {
  clearInterval(iv);
  STATE.controlAdhocIntervals = STATE.controlAdhocIntervals.filter((x) => x !== iv);
  STATE.controlStrategy.status[variableKey] = "done";
  renderControlIfSafe();
}

/** Polls only STATE.controlStrategy.results[variableKey] entries still
 * "waiting" — the live confirmation of the ChangeStrategy+
 * ConfirmConfiguration pair the backend already fanned out to every active
 * production zone inside PUT /control-strategy/{variable} (see
 * applyGlobalStrategy). Same GET-the-zone-and-compare-current_strategies
 * approach and COMMAND_TIMEOUT_MS timeout the old client-side fan-out used.
 * A zone that never confirms (e.g. not Running) ends up "timeout", not
 * silently forgotten. */
function pollGlobalStrategyResults(variableKey, strategy) {
  const startedAt = Date.now();
  const iv = setInterval(async () => {
    const results = STATE.controlStrategy.results[variableKey] || [];
    const pending = results.filter((r) => r.status === "waiting");
    if (pending.length === 0) {
      finishGlobalStrategyPoll(iv, variableKey);
      return;
    }
    const timedOut = Date.now() - startedAt >= COMMAND_TIMEOUT_MS;
    const fetched = await Promise.allSettled(pending.map((r) => apiGet(`/zones/${encodeURIComponent(r.zoneId)}`)));
    fetched.forEach((res, i) => {
      const entry = pending[i];
      if (res.status === "fulfilled") {
        updateZoneInState(res.value);
        if (res.value.current_strategies[variableKey] === strategy) {
          entry.status = "success";
          return;
        }
      }
      if (timedOut) entry.status = "timeout";
    });
    renderControlIfSafe();
    if (results.every((r) => r.status !== "waiting")) {
      finishGlobalStrategyPoll(iv, variableKey);
    }
  }, COMMAND_POLL_MS);
  STATE.controlAdhocIntervals.push(iv);
}

/** Persists the plant-wide Strategy for one variable (PUT
 * /control-strategy/{variable}) — a single call, replacing what used to be
 * an N-zone client-side fan-out (see the section comment above): the
 * backend both saves the setting (so it's picked up by every future
 * ricetta/settore/simulazione, no exceptions) and pushes the same live
 * ChangeStrategy+ConfirmConfiguration pair to every zone already in
 * coltivazione. What's left to do here is just watch those zones confirm,
 * same as before. */
async function applyGlobalStrategy(variableKey) {
  const gs = STATE.controlStrategy;
  if (gs.status[variableKey] === "sending" || gs.status[variableKey] === "waiting") return;
  const strategy = gs.drafts[variableKey] || currentControlStrategy(variableKey);
  gs.drafts[variableKey] = strategy;
  gs.status[variableKey] = "sending";
  renderControlIfSafe();

  try {
    await apiPut(`/control-strategy/${encodeURIComponent(variableKey)}`, { selected_strategy: strategy });
    gs.settings[variableKey] = strategy;
  } catch (err) {
    gs.status[variableKey] = "done";
    renderControlIfSafe();
    showToast(`Impossibile aggiornare la Strategy: ${err.message}`, "error");
    return;
  }

  const targets = activeProductionZones();
  if (targets.length === 0) {
    gs.status[variableKey] = "done";
    gs.results[variableKey] = [];
    renderControlIfSafe();
    return;
  }
  gs.status[variableKey] = "waiting";
  gs.results[variableKey] = targets.map((z) => ({ zoneId: z.id, label: zoneLabel(z), status: "waiting", message: null }));
  renderControlIfSafe();
  pollGlobalStrategyResults(variableKey, strategy);
}

function renderGlobalStrategyPanel() {
  ensureControlStrategyLoaded();
  const targets = activeProductionZones();
  const gs = STATE.controlStrategy;

  const editableRows = GLOBAL_STRATEGY_VARIABLES.map((key) => {
    const v = VARIABLES_BY_KEY[key];
    const draft = gs.drafts[key] || currentControlStrategy(key);
    const status = gs.status[key];
    const busy = status === "sending" || status === "waiting";
    const results = gs.results[key] || [];

    let buttonLabel = "Applica a tutto l'impianto";
    if (status === "sending") buttonLabel = "Invio…";
    else if (status === "waiting") buttonLabel = "In attesa di conferma…";

    const successCount = results.filter((r) => r.status === "success").length;
    const failCount = results.filter((r) => r.status === "timeout").length;

    const resultsHtml = results.length ? `
      <div class="global-strategy-results">
        <div class="global-strategy-summary">
          ${busy
            ? `Conferma in corso su ${results.length} settor${results.length === 1 ? "e" : "i"} gia' in coltivazione…`
            : `${successCount} di ${results.length} settor${results.length === 1 ? "e confermato" : "i confermati"}${failCount ? ` · ${failCount} senza conferma` : ""}`}
        </div>
        ${results.map((r) => `
          <div class="global-strategy-result-row">
            <span class="label mono">${escapeHtml(r.label)}</span>
            <span class="strategy-status ${r.status === "success" ? "applied" : r.status === "waiting" ? "pending" : "failed"}">${globalStrategyResultText(r)}</span>
          </div>
        `).join("")}
      </div>
    ` : "";

    return `
      <div class="data-table-row cols-global-strategy">
        <div><div class="var-name">${v.label}</div><div class="var-unit">${v.unit}</div></div>
        <div class="strategy-cell">
          <select data-action="global-strategy-select" data-variable="${key}" ${busy ? "disabled" : ""}>
            ${CHOOSABLE_STRATEGIES.map((s) => `<option value="${s}" ${draft === s ? "selected" : ""}>${s}</option>`).join("")}
          </select>
          <button type="button" class="btn btn-warn" data-action="apply-global-strategy" data-variable="${key}" ${busy ? "disabled" : ""}>${buttonLabel}</button>
        </div>
        <div class="global-strategy-scope">${targets.length} settor${targets.length === 1 ? "e attivo" : "i attivi"}</div>
      </div>
      ${resultsHtml}
    `;
  }).join("");

  return `
    <div class="zone-section" style="margin-bottom:22px">
      <div class="zone-section-title">Strategia di controllo — a livello di impianto</div>
      <div class="empty-note" style="margin-bottom:16px">
        Scegli una Strategy per variabile: vale subito per l'intero impianto — ogni ricetta, nuovo settore o simulazione la
        userà, non solo i settori con una coltivazione già in corso. Chi è già in coltivazione
        ${targets.length ? ` (<b>${targets.length}</b> al momento: ${targets.map(zoneLabel).join(", ")})` : " (nessuno al momento)"}
        riceve in più una conferma live immediata, tracciata qui sotto.
        Azoto, Fosforo e Potassio scelgono fra Threshold, PID e Predictive come le altre variabili.
      </div>
      <div class="data-table">
        <div class="data-table-head cols-global-strategy"><span>VARIABILE</span><span>STRATEGIA</span><span>SETTORI IN COLTIVAZIONE</span></div>
        ${editableRows}
      </div>
      <div class="recipe-hint-box" style="margin-top:16px">
        <span>Questa scelta è permanente: resta in vigore fino al prossimo cambio da questa pagina, per ogni settore, ricetta futura e per il Simulatore.</span>
      </div>
    </div>
  `;
}

/* ------------------------------------------------------------------ */
/* Pagina "Utenti" (admin-only)                                        */
/*                                                                       */
/* Lets an Amministratore create other accounts — admin or agronomo —   */
/* and see who already has one. The backend independently enforces      */
/* require_admin on both /users endpoints (see                          */
/* backend/app/features/users/dependencies.py), so this page being      */
/* admin-only client-side (gated the same way as Controllo, see         */
/* switchView()/updateNavForRole()) is a UX convenience, not the real    */
/* boundary. Used to live embedded inside the Controllo page ("Gestione */
/* utenti" panel); moved out to its own top-level "Utenti" nav item so   */
/* it doesn't depend on Controllo's zones data/poll at all.             */
/* ------------------------------------------------------------------ */

/** Loads the account list once (see renderUsersView()) — re-fetched from
 * scratch after every successful creation instead of just appending
 * locally, so the list stays exactly what the backend has even if another
 * admin session created an account in the meantime. */
async function ensureUsersLoaded() {
  const state = STATE.users;
  if (state.loaded || state.loading) return;
  state.loading = true;
  try {
    state.list = await apiGet("/users");
    state.loaded = true;
    state.error = null;
  } catch (err) {
    state.error = err.message;
  } finally {
    state.loading = false;
    renderUsersIfSafe();
  }
}

/** Writes straight into STATE.users.form without a re-render — same
 * "don't fight the caret" pattern as the recipe form's text inputs (see
 * the "recipe-form-input" input-delegation handler). */
function onUserFormInput(field, value) {
  STATE.users.form[field] = value;
}

function onUserFormRoleChange(value) {
  STATE.users.form.role = value;
  renderUsersIfSafe();
}

/** Creates a new account with the role an Amministratore picked — this is
 * the one path through which an admin can create another admin or an
 * agronomo (see backend POST /users). Re-fetches the account list on
 * success rather than trusting the response alone, and never clears a
 * failed form's fields so a typo can just be fixed in place. */
async function submitCreateUser() {
  const state = STATE.users;
  if (state.status && state.status.kind === "sending") return;
  const form = state.form;
  const username = form.username.trim();
  const password = form.password;

  if (username.length < 3) {
    state.status = { kind: "error", message: "L'utente deve avere almeno 3 caratteri." };
    renderUsersIfSafe();
    return;
  }
  // Stesso pattern di UserCreate.username lato backend (vedi
  // backend/app/features/users/models.py): controllarlo anche qui evita di
  // mostrare all'amministratore il messaggio grezzo di validazione Pydantic
  // di un 422, che apiRequest() non traduce in qualcosa di leggibile come
  // fa invece per il 409 di username duplicato qui sotto.
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(username)) {
    state.status = {
      kind: "error",
      message: "L'utente può contenere solo lettere, cifre, underscore e trattini, e non può iniziare con questi ultimi.",
    };
    renderUsersIfSafe();
    return;
  }
  if (password.length < 4) {
    state.status = { kind: "error", message: "La password deve avere almeno 4 caratteri." };
    renderUsersIfSafe();
    return;
  }

  state.status = { kind: "sending" };
  renderUsersIfSafe();
  try {
    const created = await apiPost("/users", {
      username,
      password,
      role: form.role,
      display_name: form.displayName.trim() || undefined,
    });
    state.form = { username: "", password: "", displayName: "", role: "agronomo" };
    state.status = { kind: "success", message: `Account "${created.username}" (${roleLabel(created.role)}) creato.` };
    state.loaded = false;
    await ensureUsersLoaded();
  } catch (err) {
    state.status = {
      kind: "error",
      message: err.status === 409 ? "Questo nome utente è già in uso." : err.message,
    };
    renderUsersIfSafe();
  }
}

function renderUserManagementPanel() {
  const state = STATE.users;
  if (!state.loaded && !state.loading) ensureUsersLoaded();

  const rowsHtml = state.list.map((u) => `
    <div class="data-table-row cols-users">
      <div>${escapeHtml(u.display_name)}</div>
      <div class="mono" style="font-size:11.5px;color:var(--ink-mute)">${escapeHtml(u.username)}</div>
      <div><span class="pill role-${u.role}">${roleLabel(u.role)}</span></div>
      <div class="mono" style="font-size:10.5px;color:var(--ink-faint)">${fmtDateTime(u.created_at)}</div>
    </div>
  `).join("");

  const listHtml = state.error
    ? `<div class="empty-note">Impossibile caricare gli account: ${escapeHtml(state.error)}</div>`
    : `
      <div class="data-table" style="margin-bottom:18px">
        <div class="data-table-head cols-users"><span>NOME</span><span>UTENTE</span><span>RUOLO</span><span>CREATO IL</span></div>
        ${rowsHtml || '<div class="empty-note">Caricamento…</div>'}
      </div>
    `;

  const form = state.form;
  const status = state.status;
  const busy = !!status && status.kind === "sending";

  const statusHtml = status
    ? `<div class="${status.kind === "error" ? "login-error" : "login-success"}" style="margin-top:12px">${escapeHtml(status.message)}</div>`
    : "";

  return `
    <div class="zone-section">
      <div class="zone-section-title">Gestione utenti</div>
      <div class="empty-note" style="margin-bottom:16px">
        Crea nuovi account amministratore o agronomo. Un amministratore può creare anche altri amministratori, oltre ad agronomi.
      </div>
      ${listHtml}
      <div class="user-create-form">
        <div class="login-field">
          <label class="login-label">UTENTE</label>
          <input type="text" class="login-input" data-action="user-form-input" data-field="username" value="${escapeAttr(form.username)}" placeholder="es. mario_rossi" autocomplete="off" ${busy ? "disabled" : ""}>
        </div>
        <div class="login-field">
          <label class="login-label">PASSWORD</label>
          <input type="password" class="login-input" data-action="user-form-input" data-field="password" value="${escapeAttr(form.password)}" placeholder="minimo 4 caratteri" autocomplete="new-password" ${busy ? "disabled" : ""}>
        </div>
        <div class="login-field">
          <label class="login-label">NOME VISUALIZZATO</label>
          <input type="text" class="login-input" data-action="user-form-input" data-field="displayName" value="${escapeAttr(form.displayName)}" placeholder="opzionale" autocomplete="off" ${busy ? "disabled" : ""}>
        </div>
        <div class="login-field">
          <label class="login-label">RUOLO</label>
          <select class="login-input login-select" data-action="user-form-role" ${busy ? "disabled" : ""}>
            <option value="agronomo" ${form.role === "agronomo" ? "selected" : ""}>Agronomo</option>
            <option value="admin" ${form.role === "admin" ? "selected" : ""}>Amministratore</option>
          </select>
        </div>
        <button type="button" class="btn btn-primary" data-action="submit-create-user" ${busy ? "disabled" : ""}>${busy ? "Creazione…" : "Crea account"}</button>
      </div>
      ${statusHtml}
    </div>
  `;
}

/** Only re-renders while the Amministratore is actually sulla pagina Utenti,
 * e mai mentre uno dei campi del form ha il focus — stesso motivo di
 * isFocusedInControlFilter()/renderControlIfSafe() per Controllo: qui non
 * c'e' un poll periodico (vedi switchView()), ma un re-render puo' comunque
 * scattare mentre si sta scrivendo (l'elenco che finisce di caricare,
 * ensureUsersLoaded() dopo una creazione), e perdere il focus in quel
 * momento sarebbe comunque fastidioso. */
function isFocusedInUserForm() {
  const active = document.activeElement;
  return !!(active && active.closest && active.closest('[data-action="user-form-input"]'));
}

function renderUsersIfSafe() {
  if (STATE.view === "users" && !isFocusedInUserForm()) renderUsersView();
}

/** Pagina "Utenti" (nav item dedicato, admin-only — vedi switchView()/
 * updateNavForRole()): elenco account esistenti + form di creazione. */
function renderUsersView() {
  if (!isAdmin()) {
    // Difensivo soltanto — switchView() e' il vero gate e non lascia mai
    // STATE.view a "users" per chi non e' Amministratore, quindi questo
    // percorso non dovrebbe essere raggiungibile in pratica.
    document.getElementById("view-users").innerHTML = '<div class="empty-note">Sezione riservata agli amministratori.</div>';
    return;
  }
  setPageTitle("Utenti", "Crea e consulta gli account che possono accedere alla dashboard · solo Amministratore");
  document.getElementById("view-users").innerHTML = renderUserManagementPanel();
}

function renderControl() {
  if (!isAdmin()) {
    // Defensive only — switchView() is the real gate and never leaves
    // STATE.view as "control" for a non-Amministratore, so this path
    // shouldn't be reachable in practice.
    document.getElementById("view-control").innerHTML = '<div class="empty-note">Sezione riservata agli amministratori.</div>';
    return;
  }
  const zones = STATE.zones;
  // Department order everywhere is DEPT_ORDER (plain numeric, 1-5) — not
  // alphabetical by name (which uniqueSorted would give). Only departments
  // that actually have a registered zone show up in the filter, same as
  // before, just in the fixed order.
  const deptRank = Object.fromEntries(DEPT_ORDER.map((n, i) => [n, i]));
  const deptNumbersPresent = DEPT_ORDER.filter((n) => zones.some((z) => z.department_number === n));
  const deptOptions = deptNumbersPresent.map((n) => {
    const z = zones.find((zz) => zz.department_number === n);
    return { number: n, name: (z && z.department_name) || DEPT_FALLBACK_NAMES[n] };
  });
  const speciesNames = uniqueSorted(zones.map((z) => z.plant_species).filter(Boolean));
  const f = STATE.controlFilters;

  const rows = zones.filter((z) => {
    if (f.dept !== "all" && z.department_name !== f.dept) return false;
    if (f.species !== "all" && z.plant_species !== f.species) return false;
    if (f.strategy !== "all" && !Object.values(z.current_strategies).includes(f.strategy)) return false;
    return true;
  }).sort((a, b) => (deptRank[a.department_number] - deptRank[b.department_number]) || (a.sector_number - b.sector_number));

  setPageTitle("Controllo", `Strategia a livello di impianto · accesso diretto al controllo avanzato di ogni settore · ${zones.length} settori registrati (massimo 9) · solo Amministratore`);

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
    ${renderGlobalStrategyPanel()}
    <div class="zone-section-title" style="margin-bottom:14px">Tutti i settori</div>
    <div class="toolbar">
      <span class="mono" style="font:500 10.5px var(--mono);letter-spacing:.07em;color:var(--ink-faint)">FILTRI</span>
      <select data-action="control-filter" data-filter="dept">
        <option value="all" ${f.dept === "all" ? "selected" : ""}>Tutti i reparti</option>
        ${deptOptions.map((d) => `<option value="${escapeAttr(d.name)}" ${f.dept === d.name ? "selected" : ""}>${escapeHtml(d.name)}</option>`).join("")}
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
  STATE.recipeHintVisible = false;
  STATE.phasePending = false;
  STATE.modalChartVariable = "soil_moisture";
  STATE.modalChartData = [];
  STATE.deleteZoneConfirm = false;
  STATE.deleteZoneStatus = null;
  STATE.deleteZoneError = null;
  STATE.stopCultivationConfirm = false;
  STATE.stopCultivationStatus = null;
  STATE.stopCultivationError = null;
  STATE.startCultivationStatus = null;
  STATE.startCultivationError = null;
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
  // Closing the simulation pop-up specifically does NOT discard
  // STATE.simulation (unlike every other modal kind, which has nothing
  // equivalent to keep): the job keeps running/polling and any result
  // stays in memory, so clicking a different sector on the grid (see
  // renderSimulatorSectorRow -> openGreenhouseSimulatorModal) reopens the
  // very same simulation instead of losing it. It's discarded only by
  // starting a genuinely new run (restartSimulationSetup) or by leaving
  // the Simulatore page entirely (switchView).
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
  STATE.stopCultivationConfirm = false;
  STATE.stopCultivationStatus = null;
  STATE.stopCultivationError = null;
  STATE.startCultivationStatus = null;
  STATE.startCultivationError = null;
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
  if (STATE.modalKind === "simulator") { renderSimulatorModal(); return; }
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
        ${zone.active_cultivation_id
          ? renderStopCultivationBlock()
          : (zone.active_recipe_id ? renderStartCultivationBlock() : "")}
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
    STATE.deleteZoneError = translateApiError(err.message);
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

    return `
      <div class="data-table-row cols-advanced">
        <div><div class="var-name">${v.label}</div><div class="var-unit">${v.unit}</div></div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.setpoint, v.decimals) : "—"}</div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.allowed_range.minimum, v.decimals) : "—"}</div>
        <div class="readonly-cell" data-action="show-recipe-hint" title="Valore dalla ricetta attiva">${target ? fmtNum(target.allowed_range.maximum, v.decimals) : "—"}</div>
        <div class="readonly-cell" title="La Strategia si imposta a livello di impianto dalla pagina Controllo, non per singolo settore">${escapeHtml(live || "—")}</div>
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
        <span class="hint">Setpoint / Min / Max / Strategia sono di sola lettura qui — la Strategia si imposta a livello di impianto dalla pagina Controllo</span>
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
      showToast("Nessuna conferma dall'Edge Controller entro il timeout (verifica che sia in esecuzione).", "warn");
    }
  }, COMMAND_POLL_MS);
  STATE.adhocIntervals.push(iv);
}

/* Per-zone Strategy editing used to live here (onStrategyDraftChange/       */
/* sendStrategyChange/pollForStrategyApplied) — removed when Strategy became */
/* a plant-wide choice made from the Controllo page instead (see             */
/* applyGlobalStrategy/sendGlobalStrategyToZone/pollGlobalStrategyResults    */
/* near renderControl). buildStrategyParameters/findPhaseTarget are shared   */
/* by both eras and still live in the "Strategy-change command payloads"    */
/* section near the top of this file.                                       */

/* ---- stop cultivation --------------------------------------------------- */

/**
 * Only rendered when zone.active_cultivation_id is set — i.e. exactly the
 * condition that also blocks "Rimuovi settore" (see translateApiError /
 * renderDeleteZoneSection), so this is the real resolution path for that
 * block, not just a generic lifecycle control. POST /cultivations/{id}/
 * terminate (not a raw /zones/{id}/commands StopCultivation) since it also
 * validates the cultivation is in a state that can actually be stopped and
 * moves it to "stopping" right away; the zone only clears
 * active_cultivation_id once the Edge Controller reports the command back,
 * hence the same poll/timeout pattern as advance-phase and strategy change.
 */
function renderStopCultivationBlock() {
  const sending = STATE.stopCultivationStatus === "sending" || STATE.stopCultivationStatus === "waiting";
  if (!STATE.stopCultivationConfirm) {
    return `
      <div style="margin-top:12px">
        <button type="button" class="btn btn-danger-ghost" data-action="open-stop-cultivation-confirm" ${sending ? "disabled" : ""}>Ferma coltivazione</button>
      </div>
    `;
  }
  const label = STATE.stopCultivationStatus === "sending" ? "Invio…"
    : STATE.stopCultivationStatus === "waiting" ? "Attesa conferma…"
    : "Conferma stop";
  return `
    <div class="zone-danger-confirm" style="margin-top:12px">
      <p>Fermare la coltivazione in corso in questo settore? La ricetta attiva viene interrotta e archiviata nello storico e il settore torna inattivo, senza coltivazione assegnata. Potrai assegnarne una nuova in seguito.</p>
      ${STATE.stopCultivationError ? `<div class="zone-danger-error">${escapeHtml(STATE.stopCultivationError)}</div>` : ""}
      <div class="zone-danger-actions">
        <button type="button" class="btn" data-action="cancel-stop-cultivation" ${sending ? "disabled" : ""}>Annulla</button>
        <button type="button" class="btn btn-danger" data-action="confirm-stop-cultivation" ${sending ? "disabled" : ""}>${label}</button>
      </div>
    </div>
  `;
}

async function confirmStopCultivation() {
  const zone = STATE.modalZone;
  if (!zone || !zone.active_cultivation_id) return;
  if (STATE.stopCultivationStatus === "sending" || STATE.stopCultivationStatus === "waiting") return;
  STATE.stopCultivationStatus = "sending";
  STATE.stopCultivationError = null;
  renderModal();
  try {
    await apiPost(`/cultivations/${encodeURIComponent(zone.active_cultivation_id)}/terminate`);
    STATE.stopCultivationStatus = "waiting";
    renderModal();
    showToast("Comando di stop inviato: in attesa che l'Edge Controller lo applichi.");
    pollForCultivationStopped(zone.id);
  } catch (err) {
    STATE.stopCultivationStatus = null;
    STATE.stopCultivationError = translateApiError(err.message);
    renderModal();
  }
}

function pollForCultivationStopped(zoneId) {
  let elapsed = 0;
  const iv = setInterval(async () => {
    elapsed += COMMAND_POLL_MS;
    try {
      const zone = await apiGet(`/zones/${encodeURIComponent(zoneId)}`);
      updateZoneInState(zone);
      if (!zone.active_cultivation_id) {
        clearInterval(iv);
        STATE.stopCultivationStatus = null;
        STATE.stopCultivationConfirm = false;
        STATE.modalRecipe = null;
        STATE.modalRecipeId = null;
        renderModalIfSafe();
        showToast("Coltivazione fermata: il settore è ora inattivo.");
        return;
      }
    } catch (e) { /* transient error, keep polling */ }
    if (elapsed >= COMMAND_TIMEOUT_MS) {
      clearInterval(iv);
      if (STATE.stopCultivationStatus === "waiting") {
        STATE.stopCultivationStatus = null;
        renderModalIfSafe();
        showToast("Nessuna conferma dall'Edge Controller entro il timeout (verifica che sia in esecuzione).", "warn");
      }
    }
  }, COMMAND_POLL_MS);
  STATE.adhocIntervals.push(iv);
}

/* ---- start cultivation -------------------------------------------------- */

/**
 * Mirror of renderStopCultivationBlock, rendered in the same spot instead
 * of it — only reached when the zone has NO active_cultivation_id but
 * already has a recipe assigned (active_recipe_id), see the ternary in
 * renderZoneSummary. Mutually exclusive with the stop block by
 * construction: one is gated on active_cultivation_id being set, this one
 * on it being unset, so a zone can never show both at once. No confirm
 * step (starting isn't destructive, unlike stopping) and no recipe picker
 * — POST /cultivations always takes the zone's own active_recipe_id, per
 * the diagnostic finding that nothing in the UI could start one before
 * this. Same async confirm-from-Edge poll pattern as stop/advance
 * (ActivateCultivation is applied by the Edge Controller, not instantly).
 */
function renderStartCultivationBlock() {
  const sending = STATE.startCultivationStatus === "sending" || STATE.startCultivationStatus === "waiting";
  const label = STATE.startCultivationStatus === "sending" ? "Invio…"
    : STATE.startCultivationStatus === "waiting" ? "Attesa conferma…"
    : "Avvia coltivazione";
  return `
    <div style="margin-top:12px">
      <button type="button" class="btn btn-primary" data-action="start-cultivation" ${sending ? "disabled" : ""}>${label}</button>
      ${STATE.startCultivationError ? `<div class="zone-danger-error" style="margin-top:10px">${escapeHtml(STATE.startCultivationError)}</div>` : ""}
    </div>
  `;
}

async function startCultivation() {
  const zone = STATE.modalZone;
  if (!zone || zone.active_cultivation_id || !zone.active_recipe_id) return;
  if (STATE.startCultivationStatus === "sending" || STATE.startCultivationStatus === "waiting") return;
  STATE.startCultivationStatus = "sending";
  STATE.startCultivationError = null;
  renderModal();
  try {
    await apiPost("/cultivations", { zone_id: zone.id, recipe_id: zone.active_recipe_id });
    STATE.startCultivationStatus = "waiting";
    renderModal();
    showToast("Comando di avvio inviato: in attesa che l'Edge Controller lo applichi.");
    pollForCultivationStarted(zone.id);
  } catch (err) {
    STATE.startCultivationStatus = null;
    STATE.startCultivationError = translateApiError(err.message);
    renderModal();
  }
}

function pollForCultivationStarted(zoneId) {
  let elapsed = 0;
  const iv = setInterval(async () => {
    elapsed += COMMAND_POLL_MS;
    try {
      const zone = await apiGet(`/zones/${encodeURIComponent(zoneId)}`);
      updateZoneInState(zone);
      // Unlike the stop flow, active_cultivation_id is set synchronously by
      // POST /cultivations itself (before any Edge involvement) — the real
      // "the Edge actually applied it" signal is lifecycle_state flipping
      // to Running, written only once apply_command_result processes the
      // ActivateCultivation result (backend/app/features/cultivations/
      // repository.py).
      if (zone.lifecycle_state === "Running") {
        clearInterval(iv);
        STATE.startCultivationStatus = null;
        renderModalIfSafe();
        showToast("Coltivazione avviata: il settore è ora in esecuzione.");
        return;
      }
    } catch (e) { /* transient error, keep polling */ }
    if (elapsed >= COMMAND_TIMEOUT_MS) {
      clearInterval(iv);
      if (STATE.startCultivationStatus === "waiting") {
        STATE.startCultivationStatus = null;
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

/**
 * Repaints the just-activated view synchronously, from whatever data is
 * already cached — or a "Caricamento…" placeholder on a view's very first
 * visit this session, when there is nothing to paint yet. Without this,
 * every switchView() left the content area fully empty (0 height) until
 * that view's poll made its first request and resolved: on a slow
 * connection this reads as a broken two-column layout (sidebar full
 * height, nothing beside it for a long stretch), and even on a fast one
 * it's a needless blank flash on every single revisit.
 */
function paintViewImmediately(view) {
  const el = document.getElementById("view-" + view);
  if (view === "home") {
    if (STATE.zonesLoaded) renderHome(); else el.innerHTML = '<div class="empty-note">Caricamento…</div>';
  } else if (view === "control") {
    if (STATE.zonesLoaded) renderControl(); else el.innerHTML = '<div class="empty-note">Caricamento…</div>';
  } else if (view === "recipes") {
    if (STATE.recipesLoaded) renderRecipes(); else el.innerHTML = '<div class="empty-note">Caricamento…</div>';
  } else if (view === "simulator") {
    // The grid is built from STATE.zones (active_recipe_id per sector), not
    // the recipe catalog — unlike the old catalog-wide picker this replaced.
    if (STATE.zonesLoaded) renderSimulatorView(); else el.innerHTML = '<div class="empty-note">Caricamento…</div>';
  } else if (view === "users") {
    // No zones dependency and no periodic poll (see switchView()): the
    // account list loads once via ensureUsersLoaded()/renderUsersView()
    // itself, which shows its own "Caricamento…" row while that's pending.
    renderUsersView();
  } else if (view === "alerts") {
    renderAlertsView(); // already shows its own "Caricamento…" until STATE.alertsPage.loaded
  }
}

function switchView(view) {
  // Controllo e Utenti sono Amministratore-only. Questo e' l'unico punto di
  // passaggio per ogni cambio di vista (il click sulla nav, e qualunque
  // altra cosa chiami switchView programmaticamente — non c'e' un routing
  // per-vista separato in questa app, quindi non c'e' un altro modo di
  // "raggiungere questa vista altrimenti"), quindi il gate qui copre
  // entrambe indipendentemente da come sono state richieste — non solo
  // nasconderle in updateNavForRole(). Un gate visivo/di navigazione, come
  // il resto di questo sistema di login: non protegge nulla di reale (il
  // backend applica lo stesso vincolo in modo indipendente, vedi
  // require_admin su GET/POST /users e require_role su ChangeStrategy/
  // ConfirmConfiguration).
  if ((view === "control" || view === "users") && !isAdmin()) {
    showToast("Sezione riservata agli amministratori.", "warn");
    view = "home";
  }
  if (STATE.view === "control" && view !== "control") {
    clearGlobalStrategyPolls();
  }
  if (STATE.view === "simulator" && view !== "simulator") {
    // Leaving the Simulatore page entirely: free the system's single
    // global batch-simulation slot right away if a job is still
    // queued/running, instead of leaving it occupied until the job
    // expires on its own — a second user shouldn't be blocked for no
    // reason just because this tab stopped watching. In practice the
    // simulation pop-up (STATE.modalKind === "simulator") covers the whole
    // viewport including the sidebar nav, so this path shouldn't be
    // reachable with it still open — kept as a defensive safety net.
    discardActiveSimulationIfAny();
    STATE.simulation = null;
    if (STATE.modalKind === "simulator") STATE.modalKind = null;
  }
  STATE.view = view;
  document.querySelectorAll("#main-nav .nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  ["home", "recipes", "simulator", "control", "users", "alerts"].forEach((v) => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== view);
  });
  // Retints the topbar title on the Simulatore page — one more visual cue
  // (alongside the intro banner and the sidebar dot) that this section is
  // an isolated preview environment, not operational data.
  document.querySelector(".main").classList.toggle("theme-simulator", view === "simulator");
  // A view switch always starts from the top — otherwise a tall previous
  // page (e.g. Ricette, scrolled down) can leave the viewport stranded
  // mid-page on a shorter or still-loading new one, looking exactly like
  // "content is missing further down" until the new content grows back up
  // to that scroll position.
  window.scrollTo(0, 0);
  paintViewImmediately(view);

  clearPoll("zones");
  clearPoll("recipes-poll");
  clearPoll("alerts-view");

  if (view === "home") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "control") {
    setPoll("zones", tickZones, ZONES_POLL_MS);
  } else if (view === "recipes") {
    setPoll("recipes-poll", tickRecipes, RECIPES_POLL_MS);
  } else if (view === "simulator") {
    // Needs both now: STATE.zones for the grid itself (species/phase/state/
    // active_recipe_id per sector) and the recipe catalog for whichever
    // sector's pop-up is open.
    setPoll("zones", tickZones, ZONES_POLL_MS);
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
    if (openAddSector && !openAddSector.disabled) {
      STATE.addSectorForm = {
        dept: Number(openAddSector.dataset.dept),
        sector: Number(openAddSector.dataset.sector),
        recipeId: "",
        confirmStep: false,
        status: null,
        error: null,
      };
      renderHome();
      return;
    }

    const cancelAddSector = e.target.closest('[data-action="cancel-add-sector"]');
    if (cancelAddSector) { STATE.addSectorForm = null; renderHome(); return; }

    const proceedAddSector = e.target.closest('[data-action="proceed-add-sector"]');
    if (proceedAddSector && !proceedAddSector.disabled) {
      const form = STATE.addSectorForm;
      if (form && form.recipeId) {
        STATE.addSectorForm = { ...form, confirmStep: true, error: null };
        renderHome();
      }
      return;
    }

    const cancelAddSectorConfirm = e.target.closest('[data-action="cancel-add-sector-confirm"]');
    if (cancelAddSectorConfirm) {
      const form = STATE.addSectorForm;
      if (form) { STATE.addSectorForm = { ...form, confirmStep: false, error: null }; renderHome(); }
      return;
    }

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

    const openStopCultivation = e.target.closest('[data-action="open-stop-cultivation-confirm"]');
    if (openStopCultivation && !openStopCultivation.disabled) {
      STATE.stopCultivationConfirm = true;
      STATE.stopCultivationError = null;
      renderModal();
      return;
    }

    const cancelStopCultivation = e.target.closest('[data-action="cancel-stop-cultivation"]');
    if (cancelStopCultivation) {
      STATE.stopCultivationConfirm = false;
      STATE.stopCultivationError = null;
      renderModal();
      return;
    }

    const confirmStopCultivationBtn = e.target.closest('[data-action="confirm-stop-cultivation"]');
    if (confirmStopCultivationBtn && !confirmStopCultivationBtn.disabled) { confirmStopCultivation(); return; }

    const startCultivationBtn = e.target.closest('[data-action="start-cultivation"]');
    if (startCultivationBtn && !startCultivationBtn.disabled) { startCultivation(); return; }

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

    const applyGlobalBtn = e.target.closest('[data-action="apply-global-strategy"]');
    if (applyGlobalBtn && !applyGlobalBtn.disabled) { applyGlobalStrategy(applyGlobalBtn.dataset.variable); return; }

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

    const openGreenhouseSimulator = e.target.closest('[data-action="open-greenhouse-simulator"]');
    if (openGreenhouseSimulator) { openGreenhouseSimulatorModal(openGreenhouseSimulator.dataset.zoneId); return; }

    const simSelectDuration = e.target.closest('[data-action="sim-select-duration"]');
    if (simSelectDuration && !simSelectDuration.disabled && STATE.simulation) {
      STATE.simulation.durationDays = Number(simSelectDuration.dataset.days);
      renderModal();
      return;
    }

    const simStartBtn = e.target.closest('[data-action="sim-start"]');
    if (simStartBtn && !simStartBtn.disabled) { startSimulation(); return; }

    const simCancelBtn = e.target.closest('[data-action="sim-cancel"]');
    if (simCancelBtn) { cancelSimulation(); return; }

    const simRestartBtn = e.target.closest('[data-action="sim-restart"]');
    if (simRestartBtn) { restartSimulationSetup(); return; }

    const simChartViewToggle = e.target.closest('[data-action="sim-chart-view-toggle"]');
    if (simChartViewToggle && STATE.simulation) {
      // Swaps the DOM (single canvas <-> one per VARIABLES entry), so this
      // needs the full renderModal() — unlike the variable <select> above,
      // which only ever redraws the one canvas that's already there.
      STATE.simulation.chartView = STATE.simulation.chartView === "grid" ? "single" : "grid";
      renderModal();
      return;
    }

    const submitCreateUserBtn = e.target.closest('[data-action="submit-create-user"]');
    if (submitCreateUserBtn && !submitCreateUserBtn.disabled) { submitCreateUser(); return; }
  });

  document.addEventListener("change", (e) => {
    const globalStrategySelect = e.target.closest('[data-action="global-strategy-select"]');
    if (globalStrategySelect) { onGlobalStrategyDraftChange(globalStrategySelect.dataset.variable, globalStrategySelect.value); return; }

    const filterSelect = e.target.closest('[data-action="control-filter"]');
    if (filterSelect) { STATE.controlFilters[filterSelect.dataset.filter] = filterSelect.value; renderControl(); return; }

    const userFormRole = e.target.closest('[data-action="user-form-role"]');
    if (userFormRole) { onUserFormRoleChange(userFormRole.value); return; }

    if (e.target.id === "chart-variable-select") { onChartVariableChange(e.target.value); return; }

    if (e.target.id === "sim-zone-select" && STATE.simulation) {
      // Unlike the variable <select> below, switching zones changes almost
      // everything on screen (recipe id/version, phase bands, summary
      // totals) — a full renderModal() is simpler and cheap enough here.
      STATE.simulation.activeZoneId = e.target.value;
      renderModal();
      return;
    }

    if (e.target.id === "sim-chart-variable-select" && STATE.simulation) {
      // The simulation's series/phases are already in memory (no re-fetch
      // needed, unlike the live chart) — just update which variable is
      // plotted and redraw the canvas directly.
      STATE.simulation.chartVariable = e.target.value;
      drawSimulationChart();
      const legendLabel = document.getElementById("sim-chart-legend-label");
      if (legendLabel) legendLabel.textContent = `${varLegendLabel(VARIABLES_BY_KEY[e.target.value])} — valore simulato`;
      const legendBand = document.getElementById("sim-chart-legend-band");
      if (legendBand) legendBand.outerHTML = simBandLegendItem(VARIABLES_BY_KEY[e.target.value]);
      const legendAverage = document.getElementById("sim-chart-legend-average");
      if (legendAverage) legendAverage.outerHTML = simAverageLegendItem(VARIABLES_BY_KEY[e.target.value]);
      return;
    }

    const chartLayerToggle = e.target.closest('[data-action="sim-chart-layer-toggle"]');
    if (chartLayerToggle && STATE.simulation) {
      // Which LINES to draw inside every chart (value/band/setpoint/
      // average) — no canvas is added or removed, just what gets drawn
      // into the ones already there, so a direct redraw is enough (same
      // as the variable <select> above, no full renderModal() needed).
      if (!STATE.simulation.chartLayers) STATE.simulation.chartLayers = {};
      STATE.simulation.chartLayers[chartLayerToggle.dataset.layer] =
        chartLayerToggle.checked;
      drawSimulationChart();
      return;
    }

    const actuatorStripToggle = e.target.closest('[data-action="sim-actuator-strip-toggle"]');
    if (actuatorStripToggle && STATE.simulation) {
      STATE.simulation.showActuatorStrips = actuatorStripToggle.checked;
      renderModal();
      return;
    }

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

    const addSectorRecipeSelect = e.target.closest('[data-action="add-sector-recipe-select"]');
    if (addSectorRecipeSelect && STATE.addSectorForm) {
      STATE.addSectorForm = { ...STATE.addSectorForm, recipeId: addSectorRecipeSelect.value, error: null };
      renderHome();
      return;
    }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "recipe-search") {
      STATE.recipeQuery = e.target.value;
      updateRecipeGridOnly();
    }

    const plantQuarantineReason = e.target.closest('[data-action="plant-quarantine-reason"]');
    if (plantQuarantineReason) {
      const plantId = plantQuarantineReason.dataset.plantId;
      const ui = STATE.plantUi[plantId] || {};
      // Write straight into STATE only — no re-render, so typing doesn't
      // fight the modal's periodic poll re-render for cursor position/focus.
      STATE.plantUi[plantId] = { ...ui, quarantineReason: plantQuarantineReason.value };
    }

    // "Gestione utenti" create-account form (pagina Utenti): same
    // no-re-render pattern as the recipe form's text inputs below, so typing
    // a password doesn't fight a renderUsersIfSafe() re-render (e.g. the
    // account list finishing its first load while the form is mid-typing).
    const userFormInput = e.target.closest('[data-action="user-form-input"]');
    if (userFormInput) { onUserFormInput(userFormInput.dataset.field, userFormInput.value); }

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
/* Login screen — real accounts, verified by the backend               */
/*                                                                       */
/* Only the session token is persisted (in this browser's localStorage);*/
/* the account itself (username/display_name/role) is always re-fetched*/
/* from GET /auth/me, both right after login and again on every page    */
/* load — so a stale/expired/revoked token never silently lets someone  */
/* back in with out-of-date information, it just falls back to the      */
/* login screen (see init() below). "ESCI" calls POST /auth/logout to   */
/* invalidate the token server-side too, not just forget it locally.    */
/* ------------------------------------------------------------------ */

const SESSION_TOKEN_KEY = "smarthydro_session_token";

function loadSessionToken() {
  try {
    return localStorage.getItem(SESSION_TOKEN_KEY) || null;
  } catch (e) { /* storage unavailable — treat as logged out */ return null; }
}

function saveSessionToken(token) {
  try { localStorage.setItem(SESSION_TOKEN_KEY, token); } catch (e) { /* session-only */ }
}

function clearSessionToken() {
  try { localStorage.removeItem(SESSION_TOKEN_KEY); } catch (e) {}
}

/** Italian display label for a backend role value — the value itself
 * ("admin"/"agronomo", see VALID_ROLES) stays the wire/comparison format
 * used by isAdmin()/updateNavForRole(); this only affects what's shown. */
function roleLabel(role) {
  return role === "admin" ? "Amministratore" : "Agronomo";
}

function applySidebarUser(user) {
  const name = user.display_name || user.username;
  document.getElementById("sidebar-user-avatar").textContent = name.trim().charAt(0).toUpperCase() || "?";
  document.getElementById("sidebar-user-name").textContent = name;
  document.getElementById("sidebar-user-role").textContent = roleLabel(user.role).toUpperCase();
}

/** Shows/hides the "Controllo" sidebar item for the current role. Purely a
 * client-side navigation gate — switchView() below independently refuses to
 * enter that view even if it were reached some other way, and the backend
 * enforces the real restriction on /users itself (require_admin) — so the
 * two checks don't rely on each other and neither is the only thing
 * protecting the account-management endpoints. */
function updateNavForRole(role) {
  ["control", "users"].forEach((view) => {
    const navItem = document.querySelector(`.nav-item[data-view="${view}"]`);
    if (navItem) navItem.classList.toggle("hidden", role !== "admin");
  });
}

/** Shows the login screen, clearing any previous input and — unlike the
 * old demo login — never prefilling credentials. `message` (e.g. after a
 * session expires or "ESCI") is shown as an inline notice instead of the
 * blank error area. */
function showLoginScreen(message) {
  document.getElementById("app-shell").classList.add("hidden");
  document.getElementById("bootstrap-screen").classList.add("hidden");
  document.getElementById("login-screen").classList.remove("hidden");
  const errorEl = document.getElementById("login-error");
  if (message) {
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  } else {
    errorEl.classList.add("hidden");
    errorEl.textContent = "";
  }
  const usernameInput = document.getElementById("login-username");
  usernameInput.value = "";
  document.getElementById("login-password").value = "";
  usernameInput.focus();
}

/** Reveals the already-built app behind the login gate and (re)starts it
 * fresh on Home — mirrors a normal first load, whether this is the very
 * first visit (user just submitted the form) or a re-entry after a
 * previous session was verified on boot. */
function enterApp(user) {
  STATE.currentUser = user;
  applySidebarUser(user);
  updateNavForRole(user.role);
  document.getElementById("login-screen").classList.add("hidden");
  document.getElementById("bootstrap-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.remove("hidden");
  switchView("home");
}

async function submitLogin() {
  const usernameInput = document.getElementById("login-username");
  const passwordInput = document.getElementById("login-password");
  const submitBtn = document.getElementById("login-submit");
  const errorEl = document.getElementById("login-error");
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    errorEl.textContent = "Inserisci utente e password per continuare.";
    errorEl.classList.remove("hidden");
    (username ? passwordInput : usernameInput).focus();
    return;
  }
  errorEl.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Accesso…";
  try {
    const { token, user } = await apiPost("/auth/login", { username, password });
    saveSessionToken(token);
    enterApp({ ...user, token });
  } catch (err) {
    errorEl.textContent = err.status === 401
      ? "Utente o password non validi."
      : `Accesso non riuscito: ${err.message}`;
    errorEl.classList.remove("hidden");
    passwordInput.value = "";
    passwordInput.focus();
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Accedi";
  }
}

/** "ESCI": invalidates the session on the backend (best-effort — an
 * already-expired token 404/401ing here is not a reason to keep the user
 * stuck on a page they've decided to leave), then drops the local token and
 * whatever view state is mid-flight (view polls, an in-progress batch
 * simulation, the "Gestione utenti" form) before returning to the login
 * screen — the same discipline switchView() already applies when navigating
 * away from any single view, just for the whole app at once. */
async function logout() {
  try { await apiPost("/auth/logout"); } catch (e) { /* token already invalid — fine, we're logging out anyway */ }
  clearSessionToken();
  STATE.currentUser = null;
  clearPoll("zones");
  clearPoll("recipes-poll");
  clearPoll("alerts-view");
  discardActiveSimulationIfAny();
  STATE.simulation = null;
  clearGlobalStrategyPolls();
  STATE.controlStrategy = { settings: {}, loaded: false, loading: false, drafts: {}, status: {}, results: {} };
  STATE.users = {
    list: [], loaded: false, loading: false, error: null,
    form: { username: "", password: "", displayName: "", role: "agronomo" },
    status: null,
  };
  showLoginScreen(null);
}

function initLoginScreen() {
  document.getElementById("login-submit").addEventListener("click", submitLogin);
  ["login-username", "login-password"].forEach((id) => {
    document.getElementById(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitLogin();
    });
  });
  document.getElementById("sidebar-logout").addEventListener("click", logout);
}

/* ------------------------------------------------------------------ */
/* First-run bootstrap — creates the very first (admin) account         */
/*                                                                       */
/* Shown instead of the login screen only while GET /auth/setup-required*/
/* answers true, i.e. the `users` table is still empty (see init()).    */
/* There's no role choice here: the first account is always admin (see  */
/* backend POST /auth/bootstrap-admin). The backend independently        */
/* refuses a second call once any account exists, so this form can't be */
/* used to create a second admin even if it were somehow shown again.   */
/* ------------------------------------------------------------------ */

/** Mirrors showLoginScreen(): clears any previous input and focuses the
 * username field, but for the bootstrap form instead of the normal login. */
function showBootstrapScreen() {
  document.getElementById("app-shell").classList.add("hidden");
  document.getElementById("login-screen").classList.add("hidden");
  document.getElementById("bootstrap-screen").classList.remove("hidden");
  const errorEl = document.getElementById("bootstrap-error");
  errorEl.classList.add("hidden");
  errorEl.textContent = "";
  const usernameInput = document.getElementById("bootstrap-username");
  usernameInput.value = "";
  document.getElementById("bootstrap-password").value = "";
  usernameInput.focus();
}

/** Creates the first account and logs straight into the app with the
 * session token /auth/bootstrap-admin returns — no separate /auth/login
 * round-trip needed, since the backend already opens a session for it. */
async function submitBootstrapAdmin() {
  const usernameInput = document.getElementById("bootstrap-username");
  const passwordInput = document.getElementById("bootstrap-password");
  const submitBtn = document.getElementById("bootstrap-submit");
  const errorEl = document.getElementById("bootstrap-error");
  const username = usernameInput.value.trim();
  const password = passwordInput.value;

  if (!username || !password) {
    errorEl.textContent = "Inserisci utente e password per continuare.";
    errorEl.classList.remove("hidden");
    (username ? passwordInput : usernameInput).focus();
    return;
  }
  if (username.length < 3) {
    errorEl.textContent = "L'utente deve avere almeno 3 caratteri.";
    errorEl.classList.remove("hidden");
    usernameInput.focus();
    return;
  }
  // Stesso pattern di BootstrapAdminRequest.username lato backend (vedi
  // backend/app/features/users/models.py) e dello stesso controllo già
  // fatto in submitCreateUser(): evita di mostrare qui l'errore grezzo di
  // validazione Pydantic di un 422.
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(username)) {
    errorEl.textContent = "L'utente può contenere solo lettere, cifre, underscore e trattini, e non può iniziare con questi ultimi.";
    errorEl.classList.remove("hidden");
    usernameInput.focus();
    return;
  }
  if (password.length < 4) {
    errorEl.textContent = "La password deve avere almeno 4 caratteri.";
    errorEl.classList.remove("hidden");
    passwordInput.focus();
    return;
  }

  errorEl.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Creazione…";
  try {
    const { token, user } = await apiPost("/auth/bootstrap-admin", { username, password });
    saveSessionToken(token);
    enterApp({ ...user, token });
  } catch (err) {
    // 409 significa che, fra il caricamento della pagina e l'invio del
    // form, qualcun altro ha già completato il primo avvio: non c'è nulla
    // da correggere nel form, occorre solo ricaricare per vedere il login.
    errorEl.textContent = err.status === 409
      ? "Esiste già un account su questo backend: ricarica la pagina per accedere con il login normale."
      : `Creazione non riuscita: ${err.message}`;
    errorEl.classList.remove("hidden");
    passwordInput.value = "";
    passwordInput.focus();
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Crea account amministratore";
  }
}

function initBootstrapScreen() {
  document.getElementById("bootstrap-submit").addEventListener("click", submitBootstrapAdmin);
  ["bootstrap-username", "bootstrap-password"].forEach((id) => {
    document.getElementById(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitBootstrapAdmin();
    });
  });
}

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

async function init() {
  initLoginScreen();
  initBootstrapScreen();
  initEventDelegation();
  setPoll("system-status", tickSystemStatus, STATUS_POLL_MS);

  // Il backend decide se mostrare la creazione del primo amministratore o
  // il login normale: se la tabella users è vuota, nessun token salvato può
  // comunque valere qualcosa, quindi questo controllo viene prima di tutto
  // il resto della logica di sessione qui sotto.
  let setupRequired = false;
  try {
    const status = await apiGet("/auth/setup-required");
    setupRequired = !!(status && status.setup_required);
  } catch (e) {
    // Backend irraggiungibile: si prosegue come se il setup non fosse
    // richiesto, mostrando il login normale — che comunque segnalerà
    // l'errore in modo chiaro al primo tentativo di accesso — invece di
    // bloccare la pagina su questo controllo preliminare.
    setupRequired = false;
  }
  if (setupRequired) {
    showBootstrapScreen();
    return;
  }

  const token = loadSessionToken();
  if (!token) {
    showLoginScreen(null);
    return;
  }
  // Tentatively set the token so apiRequest() attaches it to this very
  // call, then verify it against the backend rather than trusting a
  // possibly stale/expired/revoked value found in localStorage.
  STATE.currentUser = { token };
  try {
    const user = await apiGet("/auth/me");
    enterApp({ ...user, token });
  } catch (e) {
    STATE.currentUser = null;
    clearSessionToken();
    showLoginScreen("Sessione scaduta: accedi di nuovo.");
  }
}

document.addEventListener("DOMContentLoaded", init);
