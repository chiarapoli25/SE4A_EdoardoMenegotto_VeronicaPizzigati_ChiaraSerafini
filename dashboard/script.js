const DEFAULT_LOCAL_BACKEND = "http://127.0.0.1:8000";
const API = window.location.protocol === "file:" ? DEFAULT_LOCAL_BACKEND : "";

const departments = [
  { number: 1, name: "Piante Tropicali e da Fogliame", area: "d1", kind: "production" },
  { number: 2, name: "Piante da Fiore", area: "d2", kind: "production" },
  { number: 3, name: "Piante Grasse e Succulente", area: "d3", kind: "production" },
  { number: 4, name: "Piante da Frutto e Ortaggi", area: "d4", kind: "production" },
  { number: 5, name: "Quarantena", area: "d5", kind: "quarantine" },
];

const variableMeta = {
  soil_moisture: { label: "Umidità terreno", short: "H₂O", unit: "%" },
  light: { label: "Luce", short: "PAR", unit: "µmol/m²s" },
  ph: { label: "pH", short: "pH", unit: "pH" },
  nitrogen: { label: "Azoto", short: "N", unit: "mg/L" },
  phosphorus: { label: "Fosforo", short: "P", unit: "mg/L" },
  potassium: { label: "Potassio", short: "K", unit: "mg/L" },
};

const strategyFields = {
  Threshold: ["lower_threshold", "upper_threshold", "active_command", "inactive_command", "direction", "bidirectional"],
  PID: ["setpoint", "proportional_gain", "integral_gain", "derivative_gain", "command_minimum", "command_maximum", "direction"],
  Predictive: ["setpoint", "prediction_horizon_steps", "response_gain", "neutral_command", "command_minimum", "command_maximum", "direction", "water_dilution_gain", "cumulative_dose_gain", "substrate_gain"],
};

const fieldLabels = {
  lower_threshold: "Soglia minima",
  upper_threshold: "Soglia massima",
  active_command: "Comando attivo",
  inactive_command: "Comando inattivo",
  direction: "Direzione",
  bidirectional: "Bidirezionale",
  setpoint: "Setpoint",
  proportional_gain: "Guadagno P",
  integral_gain: "Guadagno I",
  derivative_gain: "Guadagno D",
  command_minimum: "Comando minimo",
  command_maximum: "Comando massimo",
  prediction_horizon_steps: "Orizzonte (passi)",
  response_gain: "Guadagno risposta",
  neutral_command: "Comando neutro",
  water_dilution_gain: "Guadagno diluizione",
  cumulative_dose_gain: "Guadagno dose cumulata",
  substrate_gain: "Guadagno substrato",
  maximum_water_volume_liters: "Acqua max (L)",
  maximum_pump_duration_seconds: "Pompa max (s)",
  water_pump_flow_liters_per_hour: "Portata pompa (L/h)",
  maximum_dose_per_command_milliliters: "Dose comando max (mL)",
  maximum_daily_dose_milliliters: "Dose giornaliera max (mL)",
  minimum_seconds_between_doses: "Intervallo dosi (s)",
  ph_settling_time_seconds: "Assestamento pH (s)",
};

const sensorTargets = ["temperature", "air_humidity", "soil_moisture", "soil_conductivity", "ph", "light"];
const actuatorTargets = ["water_pump", "lighting", "nitrogen_valve", "phosphorus_valve", "potassium_valve", "ph_up_valve", "ph_down_valve"];
const sensorFaultModes = ["sensor_dropout", "sensor_stuck", "sensor_offset"];
const actuatorFaultModes = ["actuator_stuck_off", "actuator_stuck_on", "actuator_slow_response"];

const chartPalette = {
  green: "#146c43",
  blue: "#356a8a",
  amber: "#b66a16",
  pink: "#a84f74",
};

const telemetryCharts = [
  { group: "Microclima", title: "Temperatura dell’aria", unit: "°C", series: [{ key: "temperature_c", label: "Temperatura misurata", color: chartPalette.green }] },
  { group: "Microclima", title: "Umidità dell’aria", unit: "%", domain: [0, 100], series: [{ key: "air_humidity_percent", label: "Umidità misurata", color: chartPalette.blue }] },
  { group: "Microclima", title: "Luce fotosintetica", unit: "µmol/m²s", targetVariable: "light", startAtZero: true, series: [{ key: "light_ppfd_umol_m2_s", label: "Luce misurata", color: chartPalette.amber }] },
  { group: "Substrato", title: "Umidità del substrato", unit: "%", targetVariable: "soil_moisture", domain: [0, 100], series: [{ key: "soil_moisture_percent", label: "Umidità misurata", color: chartPalette.green }] },
  { group: "Substrato", title: "Conducibilità del substrato", unit: "mS/cm", startAtZero: true, series: [{ key: "soil_bulk_ec_ms_cm", label: "EC apparente", color: chartPalette.amber }, { key: "soil_ec_ms_cm", label: "EC nei pori", color: chartPalette.green, dash: "7 5" }] },
  { group: "Substrato", title: "pH del substrato", unit: "pH", targetVariable: "ph", series: [{ key: "ph", label: "pH misurato", color: chartPalette.green }] },
  { group: "Nutrizione", title: "Azoto disponibile", unit: "mg/L", targetVariable: "nitrogen", startAtZero: true, series: [{ key: "nitrogen_estimate_mg_per_liter", label: "Azoto misurato", color: chartPalette.blue }] },
  { group: "Nutrizione", title: "Fosforo disponibile", unit: "mg/L", targetVariable: "phosphorus", startAtZero: true, series: [{ key: "phosphorus_estimate_mg_per_liter", label: "Fosforo misurato", color: chartPalette.amber }] },
  { group: "Nutrizione", title: "Potassio disponibile", unit: "mg/L", targetVariable: "potassium", startAtZero: true, series: [{ key: "potassium_estimate_mg_per_liter", label: "Potassio misurato", color: chartPalette.pink }] },
];

const actuatorSeries = [
  { key: "pump", label: "Pompa acqua", short: "P", active: (snapshot) => Boolean(snapshot.output?.water_pump_on) },
  { key: "lighting", label: "Illuminazione", short: "LED", active: (snapshot) => Number(snapshot.output?.lighting_power_watts || snapshot.command?.lighting_percent) > 0 },
  { key: "nitrogen", label: "Valvola azoto", short: "N", active: (snapshot) => Boolean(snapshot.output?.fertilizer_valves_open?.nitrogen) },
  { key: "phosphorus", label: "Valvola fosforo", short: "P", active: (snapshot) => Boolean(snapshot.output?.fertilizer_valves_open?.phosphorus) },
  { key: "potassium", label: "Valvola potassio", short: "K", active: (snapshot) => Boolean(snapshot.output?.fertilizer_valves_open?.potassium) },
  { key: "ph-up", label: "Correttore pH+", short: "pH+", active: (snapshot) => Boolean(snapshot.output?.fertilizer_valves_open?.["ph-up"] ?? snapshot.output?.fertilizer_valves_open?.ph_up) },
  { key: "ph-down", label: "Correttore pH−", short: "pH−", active: (snapshot) => Boolean(snapshot.output?.fertilizer_valves_open?.["ph-down"] ?? snapshot.output?.fertilizer_valves_open?.ph_down) },
];

const state = {
  zones: [],
  plants: [],
  recipes: [],
  cultivations: [],
  zoneDetails: {},
  route: { name: "dashboard" },
  recipe: null,
  persistedVersion: null,
  duplicateRecipe: false,
  edgeZoneId: "",
  simulationZoneId: "",
  chartWindow: "cycle",
  simulationJob: null,
  simulationPreview: null,
  simulationPollTimer: null,
  dialogReturnFocus: null,
  refreshTimer: null,
  refreshing: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
      ...options,
    });
  } catch (cause) {
    const detail = window.location.protocol === "file:"
      ? `Backend locale non raggiungibile su ${DEFAULT_LOCAL_BACKEND}. Avvia ./scripts/run_demo.sh oppure apri ${DEFAULT_LOCAL_BACKEND}/dashboard/.`
      : "Backend non raggiungibile. Verifica che il servizio SmartHydro sia avviato.";
    throw new Error(detail, { cause });
  }
  if (!response.ok) {
    let detail = `Errore HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg).join(" · ")
        : body.detail || detail;
    } catch (_) {
      // Mantiene il messaggio HTTP quando la risposta non e JSON.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

async function requestOptional(path) {
  try {
    return await request(path);
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
}

function toast(message, isError = false) {
  const element = document.createElement("div");
  element.className = `toast${isError ? " error" : ""}`;
  element.textContent = message;
  document.querySelector("#toast-region").append(element);
  window.setTimeout(() => element.remove(), 4400);
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("it-IT", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatDateTime(value) {
  if (!value) return "Non disponibile";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Non disponibile";
  return date.toLocaleString("it-IT", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatFreshness(value) {
  if (!value) return "scenario calcolato";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `aggiornato ${seconds}s fa`;
  if (seconds < 3600) return `aggiornato ${Math.floor(seconds / 60)} min fa`;
  if (seconds < 86400) return `aggiornato ${Math.floor(seconds / 3600)} h fa`;
  return `aggiornato ${Math.floor(seconds / 86400)} g fa`;
}

function formatSimulatedTime(value) {
  const totalSeconds = Math.max(0, Math.round(Number(value) || 0));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days) return `${days}g ${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

function currentBootHistory(history = []) {
  if (!history.length) return [];
  const latestBootId = history.at(-1)?.boot_id;
  return history
    .filter((sample) => !latestBootId || sample.boot_id === latestBootId)
    .sort((left, right) => Number(left.timestamp_seconds) - Number(right.timestamp_seconds));
}

function filteredHistory(history = []) {
  const samples = currentBootHistory(history);
  const seconds = { "6h": 21600, "24h": 86400, "7d": 604800 }[state.chartWindow];
  if (!seconds || !samples.length) return samples;
  const cutoff = Number(samples.at(-1).timestamp_seconds) - seconds;
  return samples.filter((sample) => Number(sample.timestamp_seconds) >= cutoff);
}

function targetForSample(recipe, sample, variable) {
  if (!recipe || !variable) return null;
  const phaseName = sample.current_phase || sample.phase_name;
  const phase = recipe.phases?.find((item) => item.name === phaseName) || recipe.phases?.[0];
  return phase?.targets?.find((target) => target.variable === variable) || null;
}

function chartTableMarkup(definition, samples) {
  if (!samples.length) return "";
  const rows = samples.slice(-100).reverse().map((sample) => `<tr><td>${escapeHtml(formatSimulatedTime(sample.timestamp_seconds))}</td>${definition.series.map((series) => `<td>${escapeHtml(formatNumber(chartValue(sample, series), 2))}</td>`).join("")}<td>${escapeHtml(sample.current_phase || sample.phase_name || "—")}</td></tr>`).join("");
  return `<details class="chart-data-table"><summary>Mostra dati tabellari (${Math.min(samples.length, 100)} più recenti)</summary><div class="table-scroll"><table><thead><tr><th>Tempo simulato</th>${definition.series.map((series) => `<th>${escapeHtml(series.label)} (${escapeHtml(definition.unit)})</th>`).join("")}<th>Fase</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

function chartValue(sample, series) {
  const value = series.value ? series.value(sample) : sample[series.key];
  return value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Number(value);
}

function chartDomain(definition, samples) {
  if (definition.domain) return definition.domain;
  const values = samples.flatMap((sample) => definition.series.map((series) => chartValue(sample, series))).filter((value) => value !== null);
  if (!values.length) return [0, 1];
  let minimum = definition.startAtZero ? 0 : Math.min(...values);
  let maximum = Math.max(...values);
  if (minimum === maximum) {
    const padding = Math.max(Math.abs(minimum) * 0.08, 1);
    minimum = definition.startAtZero ? 0 : minimum - padding;
    maximum += padding;
  } else {
    const padding = (maximum - minimum) * 0.08;
    minimum = definition.startAtZero ? 0 : minimum - padding;
    maximum += padding;
  }
  return [minimum, maximum];
}

function lineChartMarkup(definition, rawHistory = [], recipe = null, rawActuatorHistory = []) {
  const samples = filteredHistory(rawHistory);
  const subtitle = samples.length
    ? `${samples.length} campioni · tempo simulato ${formatSimulatedTime(samples[0].timestamp_seconds)}–${formatSimulatedTime(samples.at(-1).timestamp_seconds)} · ${formatFreshness(samples.at(-1).recorded_at)}${!definition.domain && !definition.startAtZero ? " · scala locale" : ""}`
    : "Nessun campione disponibile";
  const legend = definition.series.map((series) => `<span><i style="--series-color:${series.color};--series-dash:${series.dash || "none"}"></i>${escapeHtml(series.label)}</span>`).join("");
  if (samples.length < 2) {
    return `<article class="panel chart-card"><header><div><h3>${escapeHtml(definition.title)}</h3><p>${escapeHtml(subtitle)}</p></div><span class="chart-unit">${escapeHtml(definition.unit)}</span></header><div class="chart-legend">${legend}</div><div class="chart-empty"><strong>Storico non ancora sufficiente</strong><span>Servono almeno due campioni dello stesso avvio Edge.</span></div></article>`;
  }

  const width = 720;
  const height = 218;
  const left = 54;
  const right = 18;
  const top = 16;
  const bottom = 36;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const minimumTime = Number(samples[0].timestamp_seconds);
  const maximumTime = Number(samples.at(-1).timestamp_seconds);
  const timeSpan = Math.max(maximumTime - minimumTime, 1);
  let [minimumValue, maximumValue] = chartDomain(definition, samples);
  const targets = definition.targetVariable ? samples.map((sample) => targetForSample(recipe, sample, definition.targetVariable)) : [];
  const targetValues = targets.flatMap((target) => target ? [target.allowed_range?.minimum, target.allowed_range?.maximum, target.safety_range?.minimum, target.safety_range?.maximum, target.setpoint] : []).filter((value) => Number.isFinite(Number(value))).map(Number);
  if (targetValues.length && !definition.domain) {
    minimumValue = Math.min(minimumValue, ...targetValues);
    maximumValue = Math.max(maximumValue, ...targetValues);
  }
  const valueSpan = Math.max(maximumValue - minimumValue, 0.000001);
  const x = (sample) => left + ((Number(sample.timestamp_seconds) - minimumTime) / timeSpan) * plotWidth;
  const y = (value) => top + (1 - (value - minimumValue) / valueSpan) * plotHeight;

  const grid = [0, 0.5, 1].map((ratio) => {
    const value = maximumValue - ratio * valueSpan;
    const position = top + ratio * plotHeight;
    return `<line x1="${left}" y1="${position}" x2="${width - right}" y2="${position}" class="chart-grid-line"/><text x="${left - 9}" y="${position + 4}" text-anchor="end" class="chart-axis-label">${escapeHtml(formatNumber(value, valueSpan < 10 ? 1 : 0))}</text>`;
  }).join("");

  const targetBands = definition.targetVariable ? samples.slice(0, -1).map((sample, index) => {
    const target = targets[index];
    if (!target) return "";
    const x1 = x(sample);
    const x2 = x(samples[index + 1]);
    const y1 = y(Number(target.allowed_range.maximum));
    const y2 = y(Number(target.allowed_range.minimum));
    const setpointY = y(Number(target.setpoint));
    return `<rect x="${x1.toFixed(2)}" y="${Math.min(y1, y2).toFixed(2)}" width="${Math.max(x2 - x1, 1).toFixed(2)}" height="${Math.abs(y2 - y1).toFixed(2)}" class="target-band"><title>Fascia ammessa ${formatNumber(target.allowed_range.minimum)}–${formatNumber(target.allowed_range.maximum)} ${escapeHtml(definition.unit)}</title></rect><line x1="${x1.toFixed(2)}" y1="${setpointY.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${setpointY.toFixed(2)}" class="setpoint-line"/>`;
  }).join("") : "";
  const actuatorSamples = currentBootHistory(rawActuatorHistory);
  const actuatorMarkers = actuatorSamples.map((sample, index) => {
    if (Number(sample.timestamp_seconds) < minimumTime || Number(sample.timestamp_seconds) > maximumTime) return "";
    const previous = actuatorSamples[index - 1];
    const switchedOn = actuatorSeries.some((series) => series.active(sample) && (!previous || !series.active(previous)));
    if (!switchedOn) return "";
    const markerX = left + ((Number(sample.timestamp_seconds) - minimumTime) / timeSpan) * plotWidth;
    return `<line x1="${markerX.toFixed(2)}" y1="${top}" x2="${markerX.toFixed(2)}" y2="${top + plotHeight}" class="actuator-overlay-marker"><title>Accensione attuatore a ${escapeHtml(formatSimulatedTime(sample.timestamp_seconds))}</title></line>`;
  }).join("");

  const marks = definition.series.map((series) => {
    const segments = [];
    let current = [];
    samples.forEach((sample) => {
      const value = chartValue(sample, series);
      if (value === null) {
        if (current.length) segments.push(current);
        current = [];
      } else {
        current.push([x(sample), y(value), sample, value]);
      }
    });
    if (current.length) segments.push(current);
    const paths = segments.map((segment) => `<path d="${segment.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(" ")}" fill="none" stroke="${series.color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" ${series.dash ? `stroke-dasharray="${series.dash}"` : ""}/>`).join("");
    const hits = segments.flat().map(([pointX, pointY, sample, value]) => {
      const target = targetForSample(recipe, sample, definition.targetVariable);
      const outsideSafety = target && (value < Number(target.safety_range.minimum) || value > Number(target.safety_range.maximum));
      const accessibleLabel = `${series.label}: ${formatNumber(value, 2)} ${definition.unit} · ${formatSimulatedTime(sample.timestamp_seconds)}${outsideSafety ? " · oltre la soglia di sicurezza" : ""}`;
      return `${outsideSafety ? `<path d="M${pointX.toFixed(2)},${(pointY - 7).toFixed(2)} l6,11 h-12 z" class="safety-marker"><title>Superamento della soglia di sicurezza</title></path>` : ""}<circle cx="${pointX.toFixed(2)}" cy="${pointY.toFixed(2)}" r="7" fill="transparent" tabindex="0" role="img" aria-label="${escapeHtml(accessibleLabel)}"><title>${escapeHtml(accessibleLabel)}</title></circle>`;
    }).join("");
    const lastPoint = segments.flat().at(-1);
    const marker = lastPoint ? `<circle cx="${lastPoint[0].toFixed(2)}" cy="${lastPoint[1].toFixed(2)}" r="3.6" fill="white" stroke="${series.color}" stroke-width="2.2"/>` : "";
    return paths + hits + marker;
  }).join("");

  const latest = definition.series.map((series) => `${series.label}: ${formatNumber(chartValue(samples.at(-1), series), 1)} ${definition.unit}`).join(" · ");
  return `<article class="panel chart-card"><header><div><h3>${escapeHtml(definition.title)}</h3><p>${escapeHtml(subtitle)}</p><strong class="chart-latest">${escapeHtml(latest)}</strong></div><span class="chart-unit">${escapeHtml(definition.unit)}</span></header><div class="chart-legend">${legend}${definition.targetVariable ? '<span><i class="band-key"></i>Fascia ammessa</span><span><i class="setpoint-key"></i>Setpoint</span>' : ""}${actuatorMarkers ? '<span><i class="actuator-key"></i>Accensione attuatore</span>' : ""}</div><div class="chart-scroll"><svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(`${definition.title}, ${subtitle}`)}">${grid}${targetBands}${actuatorMarkers}<line x1="${left}" y1="${top + plotHeight}" x2="${width - right}" y2="${top + plotHeight}" class="chart-axis"/>${marks}<text x="${left}" y="${height - 10}" text-anchor="start" class="chart-axis-label">${escapeHtml(formatSimulatedTime(minimumTime))}</text><text x="${width - right}" y="${height - 10}" text-anchor="end" class="chart-axis-label">${escapeHtml(formatSimulatedTime(maximumTime))}</text></svg></div>${chartTableMarkup(definition, samples)}</article>`;
}

function telemetryHistoryMarkup(history = [], recipe = null, actuatorHistory = []) {
  const groups = ["Microclima", "Substrato", "Nutrizione"];
  return groups.map((group) => `<section class="chart-group"><div class="section-heading"><h3>${group}</h3><span class="muted">Asse temporale sincronizzato</span></div><div class="chart-grid">${telemetryCharts.filter((definition) => definition.group === group).map((definition) => lineChartMarkup(definition, history, recipe, actuatorHistory)).join("")}</div></section>`).join("");
}

function actuatorTimelineMarkup(rawHistory = []) {
  const samples = filteredHistory(rawHistory);
  if (samples.length < 2) return '<div class="chart-empty"><strong>Storico non ancora sufficiente</strong><span>La timeline apparirà dopo almeno due snapshot attuatori.</span></div>';
  const width = 760;
  const left = 142;
  const right = 18;
  const top = 16;
  const bottom = 35;
  const rowHeight = 31;
  const height = top + bottom + actuatorSeries.length * rowHeight;
  const plotWidth = width - left - right;
  const minimumTime = Number(samples[0].timestamp_seconds);
  const maximumTime = Number(samples.at(-1).timestamp_seconds);
  const timeSpan = Math.max(maximumTime - minimumTime, 1);
  const x = (sample) => left + ((Number(sample.timestamp_seconds) - minimumTime) / timeSpan) * plotWidth;
  const rows = actuatorSeries.map((series, rowIndex) => {
    const y = top + rowIndex * rowHeight;
    const intervals = samples.slice(0, -1).map((sample, index) => {
      if (!series.active(sample)) return "";
      const start = x(sample);
      const end = x(samples[index + 1]);
      const label = `${series.label} attivo · ${formatSimulatedTime(sample.timestamp_seconds)}–${formatSimulatedTime(samples[index + 1].timestamp_seconds)}`;
      return `<rect x="${start.toFixed(2)}" y="${y + 7}" width="${Math.max(end - start, 1).toFixed(2)}" height="15" rx="4" class="timeline-active" tabindex="0" role="img" aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title></rect>`;
    }).join("");
    const currentX = x(samples.at(-1));
    const current = series.active(samples.at(-1)) ? `<circle cx="${currentX}" cy="${y + 14.5}" r="4" class="timeline-current"><title>${escapeHtml(`${series.label} attualmente attivo`)}</title></circle>` : "";
    return `<text x="${left - 10}" y="${y + 18}" text-anchor="end" class="timeline-label">${escapeHtml(series.label)}</text><rect x="${left}" y="${y + 7}" width="${plotWidth}" height="15" rx="4" class="timeline-track"/>${intervals}${current}`;
  }).join("");
  return `<div class="chart-scroll"><svg class="actuator-timeline" viewBox="0 0 ${width} ${height}" role="img" aria-label="Cronologia di accensione degli attuatori">${rows}<text x="${left}" y="${height - 9}" text-anchor="start" class="chart-axis-label">${escapeHtml(formatSimulatedTime(minimumTime))}</text><text x="${width - right}" y="${height - 9}" text-anchor="end" class="chart-axis-label">${escapeHtml(formatSimulatedTime(maximumTime))}</text></svg></div>`;
}

function actuatorTransitionsMarkup(rawHistory = []) {
  const samples = filteredHistory(rawHistory);
  const transitions = [];
  samples.forEach((sample, index) => {
    actuatorSeries.forEach((series) => {
      const active = series.active(sample);
      const previous = index ? series.active(samples[index - 1]) : false;
      if (active !== previous) transitions.push({ label: series.label, active, sample });
    });
  });
  const visible = transitions.reverse().slice(0, 12);
  if (!visible.length) return '<div class="data-empty"><strong>Nessuna commutazione rilevata</strong><span>Gli attuatori sono rimasti spenti nello storico disponibile.</span></div>';
  return `<ol class="actuator-transition-list">${visible.map(({ label, active, sample }) => `<li><span class="transition-state ${active ? "on" : "off"}">${active ? "ON" : "OFF"}</span><div><strong>${escapeHtml(label)}</strong><span>${active ? "Accensione" : "Spegnimento"} a ${escapeHtml(formatSimulatedTime(sample.timestamp_seconds))}</span><small>${escapeHtml(formatDateTime(sample.recorded_at))}</small></div></li>`).join("")}</ol>`;
}

function actuatorHistoryMarkup(history = []) {
  const pumpChart = { title: "Portata pompa", unit: "L/h", startAtZero: true, series: [{ label: "Portata", color: chartPalette.blue, value: (snapshot) => snapshot.output?.water_pump_flow_liters_per_hour }] };
  const lightingChart = { title: "Potenza illuminazione", unit: "W", startAtZero: true, series: [{ label: "Potenza", color: chartPalette.amber, value: (snapshot) => snapshot.output?.lighting_power_watts }] };
  return `<div class="actuator-history-grid"><article class="panel actuator-timeline-card"><header><div><p class="eyebrow">Timeline ON/OFF</p><h3>Quando lavorano gli attuatori</h3><p class="muted">Le barre evidenziano gli intervalli attivi nel tempo simulato.</p></div><span class="badge outline">${currentBootHistory(history).length} snapshot</span></header>${actuatorTimelineMarkup(history)}</article><article class="panel actuator-transition-card"><div class="section-heading"><div><p class="eyebrow">Commutazioni</p><h3>Accensioni e spegnimenti</h3></div></div>${actuatorTransitionsMarkup(history)}</article></div><div class="chart-grid compact-charts">${lineChartMarkup(pumpChart, history)}${lineChartMarkup(lightingChart, history)}</div>`;
}

function departmentDefinition(number) {
  return departments.find((department) => department.number === number);
}

function zoneAt(departmentNumber, sectorNumber) {
  return state.zones.find((zone) => zone.department_number === departmentNumber && zone.sector_number === sectorNumber);
}

function zoneById(zoneId) {
  return state.zones.find((zone) => zone.id === zoneId);
}

function cultivationById(cultivationId) {
  return state.cultivations.find((cultivation) => cultivation.id === cultivationId);
}

function activeCultivations() {
  return state.cultivations.filter((cultivation) => cultivation.state !== "archived");
}

function cultivationForZone(zoneId) {
  return activeCultivations().find((cultivation) => cultivation.zone_id === zoneId);
}

function recipeById(recipeId) {
  return state.recipes.find((recipe) => recipe.id === recipeId);
}

function plantsInZone(zoneId) {
  return state.plants.filter((plant) => plant.current_zone_id === zoneId);
}

function quarantinedPlants() {
  return state.plants.filter((plant) => plant.is_quarantined);
}

function productionPlants() {
  return state.plants.filter((plant) => !plant.is_quarantined && zoneById(plant.current_zone_id)?.department_number !== 5);
}

function latestPhase(zone) {
  return zone?.current_phase || null;
}

function stateTone(value) {
  if (["online", "Running", "Nominal", "active", "running", "succeeded"].includes(value)) return "ready";
  if (["Paused", "Degraded", "maintenance", "pending", "activating", "pausing", "paused", "resuming", "stopping", "queued"].includes(value)) return "limited";
  if (["Error", "EmergencyLockdown", "rejected", "error", "failed"].includes(value)) return "blocked";
  return "neutral";
}

function stateLabel(value) {
  const labels = {
    online: "Online",
    offline: "Offline",
    Idle: "Inattivo",
    Running: "In esecuzione",
    Paused: "In pausa",
    Error: "Errore",
    Nominal: "Nominale",
    Degraded: "Degradato",
    EmergencyLockdown: "Blocco di emergenza",
    active: "Attivo",
    inactive: "Disattivato",
    maintenance: "Manutenzione",
    pending: "In attesa",
    succeeded: "Eseguito",
    rejected: "Rifiutato",
    activating: "Avvio in corso",
    running: "In coltivazione",
    pausing: "Pausa richiesta",
    paused: "In pausa",
    resuming: "Ripresa richiesta",
    stopping: "Termine richiesto",
    archived: "Archiviata",
    queued: "In coda",
    failed: "Non riuscito",
    canceled: "Annullato",
  };
  return labels[value] || value || "Non disponibile";
}

function badge(value) {
  return `<span class="badge ${stateTone(value)}"><span class="status-pin"></span>${escapeHtml(stateLabel(value))}</span>`;
}

function navigate(route) {
  const hash = `#${route}`;
  if (window.location.hash === hash) applyRoute();
  else window.location.hash = route;
}

function parseRoute() {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash || hash === "overview") return { name: "dashboard" };
  if (hash === "simulation") return { name: "simulations", redirectTo: "simulations" };
  if (["dashboard", "serra", "cultivations", "recipe", "simulations", "edge"].includes(hash)) return { name: hash };
  const [name, value] = hash.split("/");
  if (name === "cultivation" && value) return { name, cultivationId: decodeURIComponent(value) };
  if (name === "reparto" && /^[1-5]$/.test(value || "")) return { name, departmentNumber: Number(value) };
  if (name === "settore" && value) {
    try {
      return { name, zoneId: decodeURIComponent(value) };
    } catch (_) {
      return { name: "dashboard" };
    }
  }
  return { name: "dashboard" };
}

function updateShell(view, title, context, navView = view) {
  const actualView = ["dashboard", "serra"].includes(view) ? "serra" : view === "cultivation" ? "cultivations" : view;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${actualView}`));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === navView));
  document.querySelector("#page-title").textContent = title;
  document.querySelector("#page-context").textContent = context;
  document.querySelector(".sidebar").classList.remove("open");
}

function stopAutoRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

function startAutoRefresh(callback) {
  stopAutoRefresh();
  state.refreshTimer = window.setInterval(callback, 10000);
}

async function applyRoute() {
  const route = parseRoute();
  if (route.redirectTo) {
    navigate(route.redirectTo);
    return;
  }
  state.route = route;
  window.scrollTo(0, 0);
  stopAutoRefresh();
  syncTimeControl(route);

  if (route.name === "recipe") {
    updateShell("recipe", "Ricette", "Catalogo agronomico · 4 reparti produttivi");
    renderRecipeCatalog();
    return;
  }
  if (route.name === "edge") {
    updateShell("edge", "Area tecnica", "Diagnostica e sicurezza · Settori produttivi");
    await refreshEdge(false);
    startAutoRefresh(() => refreshEdge(true));
    return;
  }
  if (route.name === "cultivations" || route.name === "cultivation") {
    updateShell("cultivation", route.name === "cultivation" ? "Dettaglio coltivazione" : "Coltivazioni", "Cicli agronomici · Avvio e monitoraggio", "cultivations");
    await refreshCultivations(false);
    startAutoRefresh(() => refreshCultivations(true));
    return;
  }
  if (route.name === "simulations") {
    updateShell("simulations", "Simulazioni", "Scenari isolati · Non operativi");
    renderSimulations();
    return;
  }

  const zone = route.name === "settore" ? zoneById(route.zoneId) : null;
  if (route.name === "serra") {
    updateShell("serra", "Serra", "Mappa fisica · Reparti e settori", "serra");
  } else if (route.name === "reparto") {
    const department = departmentDefinition(route.departmentNumber);
    updateShell("serra", `Reparto ${route.departmentNumber}`, `Serra didattica · ${department.name}`, "serra");
  } else if (route.name === "settore") {
    updateShell("serra", zone?.name || "Dettaglio settore", zone ? `${zone.department_name} · Settore ${zone.sector_number}` : "Serra didattica", "serra");
  } else {
    updateShell("dashboard", "Dashboard", "Decisioni agronomiche · Stato della serra", "dashboard");
  }
  renderGreenhouse();
  await refreshGreenhouse(true);
  startAutoRefresh(() => refreshGreenhouse(true));
}

function setSystemNode(id, ready) {
  const node = document.querySelector(id);
  node.className = `dot ${ready ? "ready" : "error"}`;
}

async function refreshSystemStatus() {
  const pill = document.querySelector("#connection-pill");
  try {
    const health = await request("/health");
    const backendReady = health.status === "healthy";
    const edgeReady = state.zones.some((zone) => zone.assigned_edge_id && zone.status === "online");
    setSystemNode("#side-dashboard", true);
    setSystemNode("#side-backend", backendReady);
    setSystemNode("#side-edge", edgeReady);
    pill.className = `pill ${backendReady ? "ready" : "error"}`;
    pill.innerHTML = `<span class="pulse"></span>${edgeReady ? "Backend ed Edge online" : "Backend online · Edge in attesa"}`;
  } catch (_) {
    setSystemNode("#side-dashboard", true);
    setSystemNode("#side-backend", false);
    setSystemNode("#side-edge", false);
    pill.className = "pill error";
    pill.innerHTML = '<span class="pulse"></span>Backend non raggiungibile';
  }
}

function greenhouseBreadcrumbs(route, zone) {
  const items = [route.name === "dashboard"
    ? '<strong aria-current="page">Dashboard</strong>'
    : '<button type="button" data-greenhouse-route="serra">Serra</button>'];
  if (route.name === "reparto" || zone) {
    const number = route.departmentNumber || zone.department_number;
    items.push('<span aria-hidden="true">›</span>');
    items.push(zone
      ? `<button type="button" data-greenhouse-route="reparto/${number}">Reparto ${number}</button>`
      : `<strong aria-current="page">Reparto ${number}</strong>`);
  }
  if (zone) {
    items.push('<span aria-hidden="true">›</span>');
    items.push(`<strong aria-current="page">Settore ${zone.sector_number}</strong>`);
  }
  return items.join("");
}

function greenhouseSummaryMarkup() {
  const species = new Set(state.zones.filter((zone) => zone.department_number < 5 && zone.plant_species).map((zone) => zone.plant_species));
  const online = state.zones.filter((zone) => zone.status === "online").length;
  return `
    <div class="greenhouse-summary" aria-label="Riepilogo serra">
      <div><span>Reparti</span><strong>5</strong></div>
      <div><span>Settori configurati</span><strong>${state.zones.length} <small>/ 10</small></strong></div>
      <div><span>Settori online</span><strong>${online}</strong></div>
      <div><span>Specie coltivate</span><strong>${species.size}</strong></div>
      <div class="quarantine-summary"><span>Piante in quarantena</span><strong>${quarantinedPlants().length}</strong></div>
    </div>`;
}

function mapSectorMarkup(departmentNumber, sectorNumber) {
  const zone = zoneAt(departmentNumber, sectorNumber);
  if (!zone) {
    return `<button class="map-sector empty" type="button" data-configure-sector="${departmentNumber}-${sectorNumber}" aria-label="Configura reparto ${departmentNumber}, settore ${sectorNumber}"><span>Settore ${sectorNumber}</span><strong>Non configurato</strong><small>+ Clicca per configurare</small></button>`;
  }
  const quarantine = departmentNumber === 5;
  const plants = plantsInZone(zone.id);
  const speciesCount = new Set(plants.map((plant) => plant.species)).size;
  return `
    <button class="map-sector ${zone.status}" type="button" data-zone-id="${escapeHtml(zone.id)}">
      <span>Settore ${sectorNumber} · ${stateLabel(zone.status)}</span>
      <strong>${quarantine ? `${plants.length} ${plants.length === 1 ? "pianta" : "piante"}` : escapeHtml(zone.plant_species)}</strong>
      <small>${quarantine ? `${speciesCount} ${speciesCount === 1 ? "specie" : "specie"} presenti` : escapeHtml(latestPhase(zone) || "Fase non disponibile")}</small>
    </button>`;
}

function departmentCardMarkup(department) {
  const configured = state.zones.filter((zone) => zone.department_number === department.number).length;
  return `
    <article class="department-card ${department.kind}" style="grid-area:${department.area}">
      <button class="department-card-head" type="button" data-department="${department.number}" aria-label="Apri reparto ${department.number}, ${department.name}">
        <span><small>${department.kind === "quarantine" ? "Area isolata" : "Reparto"}</small><strong>${department.number}</strong></span>
        <span class="department-title">${escapeHtml(department.name)}</span>
        <span class="department-count">${configured}/2 settori</span>
      </button>
      <div class="department-sectors">${mapSectorMarkup(department.number, 1)}${mapSectorMarkup(department.number, 2)}</div>
    </article>`;
}

function renderGreenhouseMap() {
  return `
    ${greenhouseSummaryMarkup()}
    <div class="greenhouse-plan-shell">
      <div class="greenhouse-plan">
        ${departments.map(departmentCardMarkup).join("")}
        <div class="central-corridor" aria-label="Corridoio centrale"><span>Corridoio centrale</span><i></i></div>
        <div class="greenhouse-entry"><span>Ingresso / uscita</span><i aria-hidden="true">↑</i></div>
      </div>
    </div>`;
}

function sectorCardMarkup(departmentNumber, sectorNumber) {
  const zone = zoneAt(departmentNumber, sectorNumber);
  if (!zone) {
    return `<button class="sector-card empty" type="button" data-configure-sector="${departmentNumber}-${sectorNumber}" aria-label="Configura reparto ${departmentNumber}, settore ${sectorNumber}"><span class="sector-number">${sectorNumber}</span><span class="sector-card-body"><small>Settore ${sectorNumber}</small><strong>Configura il settore</strong><span>Clicca per scegliere la specie presente.</span></span><span class="sector-arrow" aria-hidden="true">+</span></button>`;
  }
  const quarantine = departmentNumber === 5;
  const plants = plantsInZone(zone.id);
  return `
    <button class="sector-card ${quarantine ? "quarantine" : ""}" type="button" data-zone-id="${escapeHtml(zone.id)}">
      <span class="sector-number">${sectorNumber}</span>
      <span class="sector-card-body">
        <span class="sector-card-top"><small>Settore ${sectorNumber}</small>${badge(zone.status)}</span>
        <strong>${quarantine ? `${plants.length} ${plants.length === 1 ? "pianta ospitata" : "piante ospitate"}` : escapeHtml(zone.plant_species)}</strong>
        <span>${quarantine ? `${new Set(plants.map((plant) => plant.species)).size} specie · area mista` : escapeHtml(latestPhase(zone) || "Fase non disponibile")}</span>
        <em>Ultimo contatto: ${escapeHtml(formatDateTime(zone.last_edge_contact))}</em>
      </span><span class="sector-arrow" aria-hidden="true">→</span>
    </button>`;
}

function renderDepartment(departmentNumber) {
  const department = departmentDefinition(departmentNumber);
  const configured = state.zones.filter((zone) => zone.department_number === departmentNumber).length;
  const quarantine = departmentNumber === 5;
  return `
    <div class="department-overview">
      <article class="department-hero ${quarantine ? "quarantine" : ""}">
        <div><p class="eyebrow light">${quarantine ? "Area sanitaria isolata" : "Area di coltivazione"}</p><h3>${escapeHtml(department.name)}</h3><p>${configured ? `${configured} ${configured === 1 ? "settore configurato" : "settori configurati"}` : "Nessun settore configurato"} su 2 disponibili.</p>
        ${quarantine ? `<button class="secondary light compact" type="button" data-open-quarantine>+ Metti una pianta in quarantena</button>` : ""}</div>
        <span class="department-hero-number">${departmentNumber}</span>
      </article>
      <div class="sector-list">${sectorCardMarkup(departmentNumber, 1)}${sectorCardMarkup(departmentNumber, 2)}</div>
    </div>`;
}

function telemetryMarkup(telemetry) {
  if (!telemetry) return '<div class="data-empty panel"><strong>Nessuna telemetria disponibile</strong><span>Il settore non ha ancora inviato campioni.</span></div>';
  const values = [
    ["Temperatura", "T", telemetry.temperature_c, "°C", "Sensore aria"],
    ["Umidità aria", "RH", telemetry.air_humidity_percent, "%", "Sensore aria"],
    ["Umidità terreno", "H₂O", telemetry.soil_moisture_percent, "%", "Sonda capacitiva"],
    ["EC apparente", "EC", telemetry.soil_bulk_ec_ms_cm, "mS/cm", "Sonda terreno"],
    ["EC nei pori", "EC*", telemetry.soil_ec_ms_cm, "mS/cm", "Stima corretta"],
    ["Fertilizzante", "Σ", telemetry.fertilizer_concentration_mg_per_liter, "mg/L", "Modello Edge"],
    ["Azoto", "N", telemetry.nitrogen_estimate_mg_per_liter, "mg/L", "Stima Edge"],
    ["Fosforo", "P", telemetry.phosphorus_estimate_mg_per_liter, "mg/L", "Stima Edge"],
    ["Potassio", "K", telemetry.potassium_estimate_mg_per_liter, "mg/L", "Stima Edge"],
    ["pH", "pH", telemetry.ph, "", "Elettrodo"],
    ["Luce", "PAR", telemetry.light_ppfd_umol_m2_s, "µmol/m²s", "Sensore PAR"],
  ];
  return `<div class="metric-grid">${values.map(([label, symbol, value, unit, source]) => `<article class="metric-card"><header><span>${label}</span><span class="metric-symbol">${symbol}</span></header><div class="metric-value">${formatNumber(value)} <small>${unit}</small></div><footer>${source}${value === null ? " · dropout" : ""}</footer></article>`).join("")}</div>`;
}

function strategiesMarkup(zone) {
  const strategies = zone.current_strategies || {};
  const setpoints = zone.current_setpoints || {};
  return `<div class="strategy-grid">${Object.keys(variableMeta).map((variable) => `<div><span>${variableMeta[variable].label}</span><strong>${escapeHtml(strategies[variable] || "—")}</strong><small>Setpoint ${formatNumber(setpoints[variable])} ${variableMeta[variable].unit}</small></div>`).join("")}</div>`;
}

function actuatorMarkup(snapshot) {
  if (!snapshot) return '<div class="data-empty"><strong>Nessuno snapshot disponibile</strong><span>Gli attuatori non hanno ancora comunicato.</span></div>';
  const command = snapshot.command || {};
  const output = snapshot.output || {};
  const valves = output.fertilizer_valves_open || {};
  const nutrientOpen = ["nitrogen", "phosphorus", "potassium"].some((key) => Boolean(valves[key]));
  const phOpen = Boolean(valves["ph-up"] ?? valves.ph_up) || Boolean(valves["ph-down"] ?? valves.ph_down);
  return `
    <div class="actuator-row"><span class="actuator-icon">P</span><div><strong>Pompa acqua</strong><small>${formatNumber(output.water_pump_flow_liters_per_hour)} L/h · ${formatNumber(output.irrigation_volume_liters_last_step, 2)} L ultimo passo</small></div><span class="state-value ${output.water_pump_on ? "on" : ""}">${output.water_pump_on ? "ON" : "OFF"}</span></div>
    <div class="actuator-row"><span class="actuator-icon">LED</span><div><strong>Illuminazione</strong><small>${formatNumber(output.lighting_power_watts)} W fisici</small></div><span class="state-value ${Number(command.lighting_percent) > 0 ? "on" : ""}">${formatNumber(command.lighting_percent, 0)}%</span></div>
    <div class="actuator-row"><span class="actuator-icon">NPK</span><div><strong>Valvole nutrienti</strong><small>Azoto, fosforo e potassio</small></div><span class="state-value ${nutrientOpen ? "on" : ""}">${nutrientOpen ? "APERTE" : "CHIUSE"}</span></div>
    <div class="actuator-row"><span class="actuator-icon">pH</span><div><strong>Correttori pH</strong><small>Valvole pH+ e pH−</small></div><span class="state-value ${phOpen ? "on" : ""}">${phOpen ? "ATTIVI" : "FERMI"}</span></div>`;
}

function eventDescription(event) {
  const payload = event.payload || {};
  const descriptions = {
    RecipePhaseChanged: `${payload.previous_phase || "Inizio"} → ${payload.current_phase || "fase sconosciuta"}`,
    StateChanged: `${stateLabel(payload.previous_state)} → ${stateLabel(payload.current_state)}${payload.reason ? ` · ${payload.reason}` : ""}`,
    ZoneLifecycleChanged: `${stateLabel(payload.previous_state)} → ${stateLabel(payload.current_state)}${payload.reason ? ` · ${payload.reason}` : ""}`,
    FaultDetected: `${payload.component || payload.fault_type || "Guasto"}${payload.diagnostic ? ` · ${payload.diagnostic}` : ""}`,
    EmergencyTriggered: payload.reason || "Arresto di emergenza",
    StrategyChanged: `${payload.variable || "Variabile"} · ${payload.previous_strategy || "—"} → ${payload.current_strategy || "—"}`,
    CommandFailed: payload.diagnostic || payload.message || "Comando rifiutato",
    CommandExecuted: payload.message || payload.command_type || "Comando eseguito",
    SimulationSpeedChanged: `Velocità ${formatNumber(payload.current_time_scale || payload.time_scale, 0)}×`,
  };
  return descriptions[event.event_type] || payload.diagnostic || payload.reason || payload.message || "Evento registrato dall’Edge";
}

function eventsMarkup(events = [], limit = 10) {
  const visible = [...events].reverse().slice(0, limit);
  if (!visible.length) return '<div class="data-empty"><strong>Nessun evento recente</strong><span>La cronologia comparirà dopo gli aggiornamenti Edge.</span></div>';
  return `<ol class="event-timeline">${visible.map((event) => {
    const critical = ["EmergencyTriggered", "FaultDetected", "CommandFailed"].includes(event.event_type);
    return `<li class="${critical ? "critical" : ""}"><span class="event-dot"></span><div><span>${escapeHtml(event.event_type)}<time>${escapeHtml(formatDateTime(event.recorded_at))}</time></span><strong>${escapeHtml(eventDescription(event))}</strong></div></li>`;
  }).join("")}</ol>`;
}

function plantsMarkup(zone) {
  const plants = plantsInZone(zone.id);
  const quarantine = zone.department_number === 5;
  if (!plants.length) return `<div class="data-empty"><strong>${quarantine ? "Nessuna pianta in quarantena" : "Nessuna pianta registrata"}</strong><span>${quarantine ? "Gli esemplari spostati qui compariranno automaticamente." : "L’anagrafica del settore è vuota."}</span></div>`;
  return `<div class="plant-list">${plants.map((plant) => {
    const homeZone = zoneById(plant.home_zone_id);
    return `<article class="plant-row"><span class="plant-avatar">${escapeHtml(plant.species.charAt(0).toUpperCase())}</span><div><strong>${escapeHtml(plant.id)}</strong><span>${escapeHtml(plant.species)}${quarantine ? ` · origine ${escapeHtml(homeZone?.name || plant.home_zone_id)}` : ""}</span>${quarantine ? `<small>${escapeHtml(plant.quarantine_reason || "Motivo non disponibile")} · ${escapeHtml(formatDateTime(plant.quarantined_at))}</small>` : ""}</div><div class="plant-actions">${quarantine ? `<button class="text-button" type="button" data-history-plant="${escapeHtml(plant.id)}">Storico</button><button class="text-button danger" type="button" data-release-plant="${escapeHtml(plant.id)}">Dimetti</button>` : `<button class="text-button danger" type="button" data-quarantine-plant="${escapeHtml(plant.id)}">Quarantena</button>`}</div></article>`;
  }).join("")}</div>`;
}

function recipeAssignmentMarkup(zone) {
  const recipes = compatibleRecipes(zone);
  const options = recipes.length
    ? recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}" ${recipe.id === zone.active_recipe_id ? "selected" : ""}>${escapeHtml(recipe.id)} · v${recipe.version}</option>`).join("")
    : '<option value="">Nessuna ricetta compatibile disponibile</option>';
  return `
    <article class="panel recipe-assignment" data-recipe-assignment>
      <div><p class="eyebrow">Configurazione colturale</p><h2>Ricetta del settore</h2><p class="muted">La ricetta viene salvata nel backend e potrà essere caricata dall’Edge quando il controller sarà disponibile.</p></div>
      <label>Ricetta compatibile con ${escapeHtml(zone.plant_species)}<select data-zone-recipe-select>${options}</select></label>
      <div class="command-actions"><button class="primary" type="button" data-assign-zone-recipe="${escapeHtml(zone.id)}" ${recipes.length ? "" : "disabled"}>${zone.active_recipe_id ? "Cambia ricetta" : "Assegna ricetta"}</button><button class="secondary" type="button" data-open-recipes>Gestisci ricette</button></div>
    </article>`;
}

function activeActuatorCount() {
  return activeCultivations().reduce((total, cultivation) => {
    const snapshot = state.zoneDetails[cultivation.zone_id]?.actuators;
    return total + actuatorSeries.filter((series) => snapshot && series.active(snapshot)).length;
  }, 0);
}

function latestTargetAssessment(cultivation) {
  const zone = zoneById(cultivation.zone_id);
  const sample = state.zoneDetails[cultivation.zone_id]?.telemetry;
  const recipe = recipeById(cultivation.recipe_id);
  if (!sample || !recipe) return { total: 0, inRange: 0, warnings: [] };
  const definitions = telemetryCharts.filter((definition) => definition.targetVariable);
  const warnings = [];
  let inRange = 0;
  definitions.forEach((definition) => {
    const target = targetForSample(recipe, sample, definition.targetVariable);
    const value = chartValue(sample, definition.series[0]);
    if (!target || value === null) return;
    const good = value >= Number(target.allowed_range.minimum) && value <= Number(target.allowed_range.maximum);
    if (good) inRange += 1;
    else warnings.push({ cultivation, zone, definition, value, target, recordedAt: sample.recorded_at, safety: value < Number(target.safety_range.minimum) || value > Number(target.safety_range.maximum) });
  });
  return { total: definitions.length, inRange, warnings };
}

function cultivationCardMarkup(cultivation, compact = false) {
  const zone = zoneById(cultivation.zone_id);
  const nextAction = cultivation.state === "paused" ? "Riprendere quando opportuno" : cultivation.state === "activating" ? "Attendere la conferma Edge" : cultivation.recipe_completed ? "Valutare la chiusura del ciclo" : `Monitorare ${zone?.current_phase || "la fase iniziale"}`;
  return `<article class="panel cultivation-card ${compact ? "compact" : ""}"><header><div class="cultivation-identity"><span>${escapeHtml(cultivation.plant_species.charAt(0))}</span><div><p class="eyebrow">${escapeHtml(zone ? `Reparto ${zone.department_number} · Settore ${zone.sector_number}` : "Settore")}</p><h3>${escapeHtml(cultivation.plant_species)}</h3></div></div>${badge(cultivation.state)}</header><dl><div><dt>Ricetta</dt><dd>${escapeHtml(cultivation.plant_species)} · versione ${cultivation.recipe_version}</dd></div><div><dt>Fase</dt><dd>${escapeHtml(zone?.current_phase || "In attesa")}</dd></div><div><dt>Edge</dt><dd>${zone?.status === "online" ? "Online" : "Avvio in attesa dell’Edge"}</dd></div><div><dt>Prossima azione</dt><dd>${escapeHtml(nextAction)}</dd></div></dl><footer><button class="secondary compact" type="button" data-open-cultivation="${escapeHtml(cultivation.id)}">Apri coltivazione</button></footer></article>`;
}

function renderAgronomicDashboard() {
  const active = activeCultivations();
  const assessments = active.map(latestTargetAssessment);
  const measured = assessments.reduce((sum, item) => sum + item.total, 0);
  const inRange = assessments.reduce((sum, item) => sum + item.inRange, 0);
  const warnings = assessments.flatMap((item) => item.warnings).sort((left, right) => Number(right.safety) - Number(left.safety) || new Date(right.recordedAt || 0) - new Date(left.recordedAt || 0) || Number(left.zone?.department_number || 0) - Number(right.zone?.department_number || 0) || Number(left.zone?.sector_number || 0) - Number(right.zone?.sector_number || 0));
  const attentionZones = new Set([...warnings.map((warning) => warning.cultivation.zone_id), ...active.filter((cultivation) => ["error", "stopping"].includes(cultivation.state)).map((cultivation) => cultivation.zone_id)]);
  const priorities = warnings.slice(0, 6).map((warning) => `<li class="${warning.safety ? "critical" : ""}"><span class="priority-icon" aria-hidden="true">${warning.safety ? "!" : "↗"}</span><div><strong>${escapeHtml(warning.definition.title)} ${warning.safety ? "oltre la soglia di sicurezza" : "fuori obiettivo"}</strong><span>${escapeHtml(warning.cultivation.plant_species)} · ${escapeHtml(warning.zone?.name || "Settore")} · ${formatNumber(warning.value)} ${escapeHtml(warning.definition.unit)}</span><small>Fascia ammessa ${formatNumber(warning.target.allowed_range.minimum)}–${formatNumber(warning.target.allowed_range.maximum)} ${escapeHtml(warning.definition.unit)}</small></div></li>`).join("");
  return `<div class="dashboard-kpis"><article><span>Coltivazioni attive</span><strong>${active.length}</strong><small>${active.filter((item) => item.state === "running").length} in esecuzione</small></article><article class="${attentionZones.size ? "attention" : ""}"><span>Settori in attenzione</span><strong>${attentionZones.size}</strong><small>${attentionZones.size ? "Richiedono verifica" : "Nessuna priorità"}</small></article><article><span>Parametri entro target</span><strong>${measured ? `${Math.round(inRange / measured * 100)}%` : "—"}</strong><small>${inRange} di ${measured} misure confrontabili</small></article><article><span>Attuatori attivi</span><strong>${activeActuatorCount()}</strong><small>Stato fisico più recente</small></article></div><div class="dashboard-main-grid"><article class="panel priorities-panel"><div class="section-heading"><div><p class="eyebrow">Da controllare ora</p><h2>Priorità agronomiche</h2></div><span class="badge ${warnings.length ? "limited" : "ready"}">${warnings.length ? `${warnings.length} anomalie` : "Tutto regolare"}</span></div>${priorities ? `<ol class="priority-list">${priorities}</ol>` : '<div class="data-empty"><strong>Nessun parametro fuori obiettivo</strong><span>I dati disponibili rispettano le fasce della fase corrente.</span></div>'}</article><article class="panel quick-actions"><p class="eyebrow">Azioni rapide</p><h2>Gestione quotidiana</h2><button class="primary full" type="button" data-add-cultivation>+ Aggiungi coltivazione</button><button class="secondary full" type="button" data-go-to="serra">Apri piantina della serra</button><button class="secondary full" type="button" data-open-time-mode="runtime">Regola tempo runtime</button><button class="secondary full" type="button" data-open-time-mode="batch">Simula una ricetta</button></article></div><div class="section-heading spaced"><div><p class="eyebrow">Cicli in corso</p><h2>Coltivazioni attive</h2></div><button class="text-button" type="button" data-go-to="cultivations">Vedi tutte →</button></div>${active.length ? `<div class="cultivation-grid">${active.map((item) => cultivationCardMarkup(item, true)).join("")}</div>` : '<div class="data-empty panel"><strong>Nessuna coltivazione attiva</strong><span>Usa “Aggiungi coltivazione” per scegliere settore e ricetta e avviare l’Edge.</span></div>'}`;
}

function renderSectorDetail(zone, { agronomic = false } = {}) {
  const details = state.zoneDetails[zone.id] || {};
  const cultivation = cultivationForZone(zone.id);
  const recipe = recipeById(cultivation?.recipe_id || zone.active_recipe_id);
  const quarantine = zone.department_number === 5;
  const identity = quarantine ? "Q" : zone.plant_species?.charAt(0).toUpperCase() || "S";
  return `
    <article class="sector-hero panel ${quarantine ? "quarantine" : ""}">
      <div class="sector-identity"><span class="plant-mark">${escapeHtml(identity)}</span><div><p class="eyebrow">${quarantine ? "Settore sanitario misto" : "Coltivazione"}</p><h2>${escapeHtml(quarantine ? zone.name : zone.plant_species)}</h2><p>${escapeHtml(zone.department_name)} · Settore ${zone.sector_number}</p></div></div>${badge(zone.status)}
      <dl class="sector-facts">
        <div><dt>Lifecycle</dt><dd>${badge(zone.lifecycle_state)}</dd></div>
        <div><dt>Stato operativo</dt><dd>${badge(zone.operational_state)}</dd></div>
        <div><dt>${quarantine ? "Piante ospitate" : "Ricetta attiva"}</dt><dd>${escapeHtml(quarantine ? plantsInZone(zone.id).length : recipe ? `${recipe.plant_type} · versione ${recipe.version}` : "Non assegnata")}</dd></div>
        <div><dt>Ultimo contatto Edge</dt><dd>${escapeHtml(formatDateTime(zone.last_edge_contact))}</dd></div>
        <div><dt>Stato amministrativo</dt><dd>${badge(zone.administrative_status)}</dd></div>
        <div><dt>${quarantine ? "Specie presenti" : "Fase corrente"}</dt><dd>${escapeHtml(quarantine ? new Set(plantsInZone(zone.id).map((plant) => plant.species)).size : latestPhase(zone) || "Non disponibile")}</dd></div>
        <div><dt>${quarantine ? "Gestione" : "Controller Edge"}</dt><dd>${escapeHtml(quarantine ? "Gestione sanitaria" : zone.assigned_edge_id ? "Assegnato automaticamente" : "Assegnazione in corso")}</dd></div>
        <div><dt>Velocità</dt><dd>${formatNumber(zone.time_scale, 0)}×</dd></div>
      </dl>
    </article>

    ${quarantine || agronomic ? "" : recipeAssignmentMarkup(zone)}

    <div class="section-heading spaced"><div><p class="eyebrow">Anagrafica</p><h2>${quarantine ? "Piante in quarantena" : "Piante nel settore"}</h2></div>${quarantine ? '<button class="primary compact" type="button" data-open-quarantine>+ Metti in quarantena</button>' : ""}</div>
    <article class="panel">${plantsMarkup(zone)}</article>

    <div class="section-heading spaced"><div><p class="eyebrow">Ultimo campione</p><h2>Condizioni del settore</h2></div><span class="muted">${details.telemetry ? escapeHtml(formatDateTime(details.telemetry.recorded_at)) : "In attesa di dati"}</span></div>
    ${telemetryMarkup(details.telemetry)}

    <div class="section-heading spaced chart-toolbar"><div><p class="eyebrow">Serie temporali</p><h2>Andamento dei sensori</h2></div><div class="time-filter" aria-label="Intervallo grafici"><button type="button" data-chart-window="6h" class="${state.chartWindow === "6h" ? "active" : ""}">6 ore</button><button type="button" data-chart-window="24h" class="${state.chartWindow === "24h" ? "active" : ""}">24 ore</button><button type="button" data-chart-window="7d" class="${state.chartWindow === "7d" ? "active" : ""}">7 giorni</button><button type="button" data-chart-window="cycle" class="${state.chartWindow === "cycle" ? "active" : ""}">Intero ciclo</button></div></div>
    ${telemetryHistoryMarkup(details.telemetryHistory || [], recipe, details.actuatorHistory || [])}

    ${quarantine ? "" : `<div class="section-heading spaced"><div><p class="eyebrow">Controllo corrente</p><h2>Strategy e setpoint Edge</h2></div><span class="badge outline">Ricetta v${escapeHtml(zone.active_recipe_version || "—")}</span></div><article class="panel">${strategiesMarkup(zone)}</article>`}

    <div class="detail-grid spaced">
      <article class="panel"><div class="section-heading"><div><p class="eyebrow">Output fisico</p><h2>Attuatori</h2></div><span class="badge ${details.actuators ? "ready" : "neutral"}">${details.actuators ? "Snapshot Edge" : "In attesa"}</span></div><div class="actuator-list">${actuatorMarkup(details.actuators)}</div></article>
      <article class="panel"><div class="section-heading"><div><p class="eyebrow">Registro operativo</p><h2>Eventi recenti</h2></div><span class="badge outline">${details.events?.length || 0}</span></div>${eventsMarkup(details.events || [])}</article>
    </div>

    <div class="section-heading spaced"><div><p class="eyebrow">Storico operativo</p><h2>Attività degli attuatori</h2></div><span class="badge outline">${currentBootHistory(details.actuatorHistory || []).length} snapshot</span></div>
    ${actuatorHistoryMarkup(details.actuatorHistory || [])}`;
}

function renderGreenhouse() {
  const route = state.route;
  const content = document.querySelector("#greenhouse-content");
  const breadcrumbs = document.querySelector("#greenhouse-breadcrumbs");
  const kicker = document.querySelector("#greenhouse-kicker");
  const heading = document.querySelector("#greenhouse-heading");
  const description = document.querySelector("#greenhouse-description");
  const zone = route.name === "settore" ? zoneById(route.zoneId) : null;
  breadcrumbs.innerHTML = greenhouseBreadcrumbs(route, zone);

  if (route.name === "reparto") {
    const department = departmentDefinition(route.departmentNumber);
    kicker.textContent = route.departmentNumber === 5 ? "Area sanitaria" : "Dettaglio reparto";
    heading.textContent = department.name;
    description.textContent = route.departmentNumber === 5 ? "I settori possono ospitare contemporaneamente piante di specie diverse." : "Seleziona un settore per aprire coltura, telemetria, attuatori ed eventi.";
    content.innerHTML = renderDepartment(route.departmentNumber);
    return;
  }
  if (route.name === "settore") {
    kicker.textContent = "Dettaglio settore";
    if (!zone) {
      heading.textContent = "Settore non disponibile";
      description.textContent = "Il settore richiesto non è registrato oppure è stato rimosso.";
      content.innerHTML = '<div class="route-empty panel"><span aria-hidden="true">⌁</span><h2>Impossibile aprire il settore</h2><p>Ritorna alla piantina e scegli un settore configurato.</p><button class="secondary" type="button" data-greenhouse-route="serra">Torna alla serra</button></div>';
      return;
    }
    heading.textContent = zone.name;
    description.textContent = zone.department_number === 5 ? "Monitoraggio ambientale e gestione degli esemplari isolati." : `Monitoraggio della coltivazione di ${zone.plant_species}.`;
    content.innerHTML = renderSectorDetail(zone);
    return;
  }

  if (route.name === "serra") {
    kicker.textContent = "Mappa operativa";
    heading.textContent = "Piantina della serra";
    description.textContent = "Quattro reparti produttivi e un’area separata per la quarantena. Seleziona un settore per aprirne il dettaglio.";
    content.innerHTML = renderGreenhouseMap();
    return;
  }

  kicker.textContent = "Quadro operativo";
  heading.textContent = "Cosa richiede attenzione oggi";
  description.textContent = "Stato sintetico dei cicli, scostamenti dai target e attuatori intervenuti.";
  content.innerHTML = renderAgronomicDashboard();
}

async function loadZoneDetails(zone, { includeHistory = true, cultivationId = null } = {}) {
  const zonePath = `/zones/${encodeURIComponent(zone.id)}`;
  const historyFilter = cultivationId ? `&cultivation_id=${encodeURIComponent(cultivationId)}` : "";
  const entries = [
    ["telemetry", `${zonePath}/telemetry/latest`],
    ["actuators", `${zonePath}/actuators/latest`],
    ["events", `${zonePath}/events?limit=40`],
  ];
  if (includeHistory) entries.splice(1, 0, ["telemetryHistory", `${zonePath}/telemetry?limit=1000${historyFilter}`], ["actuatorHistory", `${zonePath}/actuators?limit=1000${historyFilter}`]);
  const keys = entries.map(([key]) => key);
  const paths = entries.map(([, path]) => path);
  const results = await Promise.allSettled(paths.map(requestOptional));
  const details = { ...(state.zoneDetails[zone.id] || {}) };
  const errors = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") details[keys[index]] = result.value;
    else errors.push(`${keys[index]}: ${result.reason.message}`);
  });
  state.zoneDetails[zone.id] = details;
  return errors;
}

async function refreshSharedData() {
  const [zones, plants, cultivations] = await Promise.all([request("/zones"), request("/plants?limit=1000"), request("/cultivations")]);
  state.zones = zones;
  state.plants = plants;
  state.cultivations = cultivations;
  refreshEdgeZoneOptions();
  syncTimeControl(state.route);
  await refreshSystemStatus();
}

function syncTimeControl(route = state.route) {
  const trigger = document.querySelector("#time-control-trigger");
  if (!trigger) return;
  trigger.hidden = route?.name === "recipe";
  const routeCultivation = route?.name === "cultivation" ? cultivationById(route.cultivationId) : null;
  const cultivation = routeCultivation || activeCultivations()[0];
  const zone = zoneById(cultivation?.zone_id);
  trigger.textContent = cultivation ? `Tempo: ${formatNumber(zone?.time_scale || 1, 0)}× · Continuo` : "Tempo: configura";
  const runtimeSelect = document.querySelector("#runtime-cultivation");
  if (runtimeSelect) {
    runtimeSelect.innerHTML = activeCultivations().length ? activeCultivations().map((item) => {
      const itemZone = zoneById(item.zone_id);
      return `<option value="${escapeHtml(item.id)}">${escapeHtml(item.plant_species)} · R${itemZone?.department_number || "—"} S${itemZone?.sector_number || "—"}</option>`;
    }).join("") : '<option value="">Nessuna coltivazione attiva</option>';
    if (cultivation) runtimeSelect.value = cultivation.id;
  }
  const batchSelect = document.querySelector("#batch-recipe");
  if (batchSelect) batchSelect.innerHTML = state.recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}">${escapeHtml(recipe.plant_type)} · ${escapeHtml(recipe.id)} · v${recipe.version}</option>`).join("");
}

function setTimeMode(mode) {
  document.querySelectorAll('input[name="time-mode"]').forEach((input) => { input.checked = input.value === mode; });
  document.querySelector("#runtime-time-panel").hidden = mode !== "runtime";
  document.querySelector("#batch-time-panel").hidden = mode !== "batch";
}

function openTimeDialog(mode = "runtime") {
  syncTimeControl();
  setTimeMode(mode);
  state.dialogReturnFocus = document.activeElement;
  document.querySelector("#time-dialog").showModal();
}

function closeDialog(dialog) {
  if (!dialog?.open) return;
  dialog.close();
  if (state.dialogReturnFocus instanceof HTMLElement) state.dialogReturnFocus.focus();
  state.dialogReturnFocus = null;
}

function durationSeconds(value, unit) {
  const factors = { hours: 3600, days: 86400, weeks: 604800, months: 2592000 };
  return Math.round(Number(value) * factors[unit]);
}

async function applyRuntimeTime() {
  const cultivation = cultivationById(document.querySelector("#runtime-cultivation").value);
  const zone = zoneById(cultivation?.zone_id);
  if (!cultivation || !zone) return toast("Seleziona una coltivazione attiva.", true);
  const speed = Number(document.querySelector("#runtime-speed").value);
  const limited = document.querySelector("#runtime-duration-mode").value === "limited";
  const duration = limited ? durationSeconds(document.querySelector("#runtime-duration").value, document.querySelector("#runtime-duration-unit").value) : null;
  if (limited && (!Number.isFinite(duration) || duration <= 0)) return toast("Inserisci una durata valida.", true);
  const button = document.querySelector("#apply-runtime-time");
  button.disabled = true;
  button.textContent = "Invio in corso…";
  try {
    await enqueueCommand(zone, "SetSimulationSpeed", { time_scale: speed }, { refresh: false });
    await enqueueCommand(zone, "SetSimulationDuration", { duration_seconds: duration }, { refresh: false });
    document.querySelector("#time-dialog").close();
    toast(`${cultivation.plant_species}: tempo impostato a ${speed}×${limited ? ` per ${formatSimulatedTime(duration)}` : " in continuo"}.`);
    await refreshSharedData();
    if (state.route.name === "dashboard") await refreshGreenhouse(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Applica al runtime";
  }
}

function updateCultivationDialog() {
  const zone = zoneById(document.querySelector("#cultivation-zone").value);
  const recipes = zone ? compatibleRecipes(zone) : [];
  const select = document.querySelector("#cultivation-recipe");
  const previous = select.value;
  select.innerHTML = recipes.length ? recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}">${escapeHtml(recipe.plant_type)} · ${escapeHtml(recipe.id)} · v${recipe.version}</option>`).join("") : '<option value="">Nessuna ricetta compatibile</option>';
  if (recipes.some((recipe) => recipe.id === previous)) select.value = previous;
  const recipe = recipeById(select.value);
  const staleOfflineState = zone && zone.status === "offline" && !["Idle", "Error"].includes(zone.lifecycle_state);
  document.querySelector("#cultivation-summary").innerHTML = zone && recipe ? `<strong>Pronta per l’avvio</strong><span>${escapeHtml(recipe.plant_type)} nel ${escapeHtml(zone.name)}</span><small>${recipe.phases.length} fasi · ricetta ${escapeHtml(recipe.id)} v${recipe.version}</small><p>${staleOfflineState ? "Lo stato precedente del settore verrà riallineato. L’avvio resterà in attesa finché l’Edge tornerà online." : "L’Edge verrà avviato automaticamente con la conferma."}</p>` : '<strong>Configurazione non disponibile</strong><span>Scegli un settore con almeno una ricetta compatibile.</span>';
  document.querySelector("#create-cultivation").disabled = !zone || !recipe;
}

function openCultivationDialog() {
  const zonesWithoutCultivation = state.zones.filter((zone) => zone.department_number < 5 && zone.administrative_status === "active" && !cultivationForZone(zone.id));
  const zones = zonesWithoutCultivation.filter((zone) => ["Idle", "Error"].includes(zone.lifecycle_state) || zone.status === "offline");
  if (!zones.length) {
    const message = zonesWithoutCultivation.length
      ? "I settori liberi risultano ancora in esecuzione sull’Edge. Arresta il ciclo corrente o attendi la sincronizzazione."
      : "Non ci sono settori produttivi liberi. Termina prima una coltivazione attiva.";
    return toast(message, true);
  }
  document.querySelector("#cultivation-zone").innerHTML = zones.map((zone) => `<option value="${escapeHtml(zone.id)}">Reparto ${zone.department_number} · Settore ${zone.sector_number} · ${escapeHtml(zone.plant_species)}</option>`).join("");
  document.querySelector("#cultivation-form-status").hidden = true;
  updateCultivationDialog();
  state.dialogReturnFocus = document.activeElement;
  document.querySelector("#cultivation-dialog").showModal();
}

async function submitCultivation(event) {
  event.preventDefault();
  const button = document.querySelector("#create-cultivation");
  const status = document.querySelector("#cultivation-form-status");
  button.disabled = true;
  button.textContent = "Creazione e avvio…";
  try {
    const action = await request("/cultivations", { method: "POST", body: JSON.stringify({ zone_id: document.querySelector("#cultivation-zone").value, recipe_id: document.querySelector("#cultivation-recipe").value }) });
    document.querySelector("#cultivation-dialog").close();
    await refreshSharedData();
    const zone = zoneById(action.cultivation.zone_id);
    toast(zone?.status === "online" ? "Coltivazione creata: avvio accodato all’Edge." : "Coltivazione creata. Avvio in attesa dell’Edge.");
    navigate(`cultivation/${encodeURIComponent(action.cultivation.id)}`);
  } catch (error) {
    status.hidden = false;
    status.className = "inline-status error";
    status.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Crea e avvia";
  }
}

async function actOnCultivation(cultivationId, action) {
  const labels = { pause: "Mettere in pausa questa coltivazione?", resume: null, terminate: "Terminare la coltivazione? Il settore resterà occupato finché l’Edge non conferma; dati e storico saranno conservati." };
  if (labels[action] && !window.confirm(labels[action])) return;
  try {
    await request(`/cultivations/${encodeURIComponent(cultivationId)}/${action}`, { method: "POST" });
    toast(`${action === "terminate" ? "Termine" : action === "pause" ? "Pausa" : "Ripresa"} accodato all’Edge.`);
    await refreshCultivations(false);
  } catch (error) {
    toast(error.message, true);
  }
}

function cultivationDetailMarkup(cultivation) {
  const zone = zoneById(cultivation.zone_id);
  if (!zone) return '<div class="inline-status error">Il settore associato non è disponibile.</div>';
  const actions = cultivation.state === "running" ? `<button class="secondary" type="button" data-cultivation-action="pause" data-cultivation-id="${escapeHtml(cultivation.id)}">Metti in pausa</button>` : cultivation.state === "paused" ? `<button class="primary" type="button" data-cultivation-action="resume" data-cultivation-id="${escapeHtml(cultivation.id)}">Riprendi</button>` : "";
  return `<div class="cultivation-detail-actions"><button class="text-button" type="button" data-go-to="cultivations">← Tutte le coltivazioni</button><div>${actions}<button class="secondary danger-button" type="button" data-cultivation-action="terminate" data-cultivation-id="${escapeHtml(cultivation.id)}" ${cultivation.state === "stopping" ? "disabled" : ""}>Termina coltivazione</button><button class="danger-button" type="button" data-cultivation-emergency="${escapeHtml(cultivation.id)}">Arresto di emergenza</button></div></div>${zone.status !== "online" && cultivation.state === "activating" ? '<div class="inline-status">Avvio in attesa dell’Edge. Non devi eseguire altre operazioni.</div>' : ""}${renderSectorDetail(zone, { agronomic: true })}`;
}

function renderCultivations() {
  const content = document.querySelector("#cultivations-content");
  if (state.route.name === "cultivation") {
    const cultivation = cultivationById(state.route.cultivationId);
    content.innerHTML = cultivation ? cultivationDetailMarkup(cultivation) : '<div class="route-empty panel"><h2>Coltivazione non disponibile</h2><p>Il ciclo richiesto non esiste o non è più disponibile.</p></div>';
    return;
  }
  const active = activeCultivations();
  const archived = state.cultivations.filter((item) => item.state === "archived");
  content.innerHTML = active.length ? `<div class="cultivation-grid">${active.map((item) => cultivationCardMarkup(item)).join("")}</div>` : '<div class="data-empty panel"><strong>Nessuna coltivazione attiva</strong><span>Crea il primo ciclo scegliendo un settore libero e una ricetta compatibile.</span></div>';
  if (archived.length) content.insertAdjacentHTML("beforeend", `<details class="archive-section"><summary>Coltivazioni archiviate (${archived.length})</summary><div class="cultivation-grid">${archived.map((item) => cultivationCardMarkup(item, true)).join("")}</div></details>`);
}

async function refreshCultivations(silent = false) {
  try {
    await refreshSharedData();
    if (state.route.name === "cultivation") {
      const cultivation = cultivationById(state.route.cultivationId);
      const zone = zoneById(cultivation?.zone_id);
      if (zone) await loadZoneDetails(zone, { cultivationId: cultivation.id });
    }
    renderCultivations();
  } catch (error) {
    if (!silent) toast(error.message, true);
  }
}

function simulationSamples(preview) {
  return (preview?.series || []).map((point) => ({
    timestamp_seconds: point.start_seconds,
    current_phase: point.phase_name,
    ...point.average,
    soil_bulk_ec_ms_cm: point.average.ec_ms_cm,
    soil_ec_ms_cm: point.average.ec_ms_cm,
    nitrogen_estimate_mg_per_liter: point.average.nitrogen_mg_per_liter,
    phosphorus_estimate_mg_per_liter: point.average.phosphorus_mg_per_liter,
    potassium_estimate_mg_per_liter: point.average.potassium_mg_per_liter,
  }));
}

function simulationTimelineMarkup(preview) {
  if (!preview?.actuator_intervals?.length) return '<div class="data-empty"><strong>Nessuna attivazione prevista</strong></div>';
  const labels = { water_pump: "Pompa acqua", lighting: "Illuminazione", valve_nitrogen: "Valvola azoto", valve_phosphorus: "Valvola fosforo", valve_potassium: "Valvola potassio", "valve_ph-up": "Correttore pH+", "valve_ph-down": "Correttore pH−" };
  const windowSeconds = { "6h": 21600, "24h": 86400, "7d": 604800 }[state.chartWindow];
  const windowStart = windowSeconds ? Math.max(0, preview.duration_seconds - windowSeconds) : 0;
  const intervals = preview.actuator_intervals.filter((item) => item.end_seconds >= windowStart);
  if (!intervals.length) return '<div class="data-empty"><strong>Nessuna attivazione nell’intervallo selezionato</strong></div>';
  const actuators = [...new Set(intervals.map((item) => item.actuator))];
  const width = 760, left = 145, right = 18, row = 31, height = 45 + actuators.length * row;
  const visibleDuration = Math.max(preview.duration_seconds - windowStart, 1);
  const x = (seconds) => left + (Math.max(Number(seconds), windowStart) - windowStart) / visibleDuration * (width - left - right);
  const rows = actuators.map((actuator, index) => `<text x="${left - 10}" y="${22 + index * row}" text-anchor="end" class="timeline-label">${escapeHtml(labels[actuator] || actuator)}</text><rect x="${left}" y="${10 + index * row}" width="${width-left-right}" height="15" rx="4" class="timeline-track"/>${intervals.filter((item) => item.actuator === actuator).map((item) => { const label = `${labels[actuator] || actuator}: ${formatSimulatedTime(item.start_seconds)}–${formatSimulatedTime(item.end_seconds)}`; return `<rect x="${x(item.start_seconds)}" y="${10 + index * row}" width="${Math.max(x(item.end_seconds)-x(item.start_seconds), 1)}" height="15" rx="4" class="timeline-active" tabindex="0" role="img" aria-label="${escapeHtml(label)}"><title>${escapeHtml(label)}</title></rect>`; }).join("")}`).join("");
  return `<div class="chart-scroll"><svg class="actuator-timeline" viewBox="0 0 ${width} ${height}" role="img" aria-label="Intervalli previsti di attivazione degli attuatori">${rows}</svg></div>`;
}

function renderSimulations() {
  const content = document.querySelector("#simulations-content");
  if (!content) return;
  const job = state.simulationJob;
  const preview = state.simulationPreview;
  if (!job) {
    content.innerHTML = '<div class="simulation-empty panel"><span aria-hidden="true">▷</span><h2>Prova una ricetta prima di applicarla</h2><p>Scegli una durata da una settimana a 12 mesi. Un mese equivale sempre a 30 giorni.</p><button class="primary" type="button" data-open-time-mode="batch">Configura scenario</button></div>';
    return;
  }
  if (!preview) {
    content.innerHTML = `<article class="panel simulation-progress"><div class="section-heading"><div><p class="eyebrow">Scenario non operativo</p><h2>${escapeHtml(recipeById(job.recipe_id)?.plant_type || job.recipe_id)}</h2></div>${badge(job.status)}</div><p>${formatSimulatedTime(job.duration_seconds)} · passi interni da 15 minuti</p><progress max="100" value="${job.progress_percent}" aria-label="Avanzamento simulazione">${job.progress_percent}%</progress><strong>${formatNumber(job.progress_percent, 1)}% completato</strong>${job.error ? `<div class="inline-status error">${escapeHtml(job.error)}</div>` : ""}<button class="secondary danger-button" type="button" data-cancel-simulation="${escapeHtml(job.id)}">${["queued","running"].includes(job.status) ? "Annulla simulazione" : "Scarta anteprima"}</button></article>`;
    return;
  }
  const samples = simulationSamples(preview);
  const summary = preview.summary || {};
  content.innerHTML = `<div class="scenario-banner"><strong>${escapeHtml(preview.source_label)}</strong><span>Anteprima temporanea; scade 30 minuti dopo il completamento.</span><button class="secondary compact" type="button" data-cancel-simulation="${escapeHtml(job.id)}">Scarta</button></div><div class="dashboard-kpis simulation-kpis"><article><span>Acqua prevista</span><strong>${formatNumber(summary.delivered_water_liters, 1)} L</strong></article><article><span>Cicli di controllo</span><strong>${summary.control_cycles || 0}</strong></article><article><span>Durata simulata</span><strong>${formatSimulatedTime(preview.duration_seconds)}</strong></article><article><span>Punti visualizzati</span><strong>${samples.length}</strong><small>Massimo 1.000</small></article></div><div class="section-heading spaced chart-toolbar"><div><p class="eyebrow">Previsione dei sensori</p><h2>Andamento atteso</h2></div><div class="time-filter" aria-label="Intervallo grafici"><button type="button" data-chart-window="6h" class="${state.chartWindow === "6h" ? "active" : ""}">6 ore</button><button type="button" data-chart-window="24h" class="${state.chartWindow === "24h" ? "active" : ""}">24 ore</button><button type="button" data-chart-window="7d" class="${state.chartWindow === "7d" ? "active" : ""}">7 giorni</button><button type="button" data-chart-window="cycle" class="${state.chartWindow === "cycle" ? "active" : ""}">Intero ciclo</button></div></div>${telemetryHistoryMarkup(samples, recipeById(job.recipe_id))}<div class="section-heading spaced"><div><p class="eyebrow">Previsione degli attuatori</p><h2>Quando interverranno</h2></div></div><article class="panel simulation-timeline">${simulationTimelineMarkup(preview)}</article>`;
}

async function startBatchSimulation() {
  const recipeId = document.querySelector("#batch-recipe").value;
  const seconds = durationSeconds(document.querySelector("#batch-duration").value, document.querySelector("#batch-duration-unit").value);
  if (!recipeId || seconds < 900 || seconds > 360 * 86400) return toast("La durata deve essere compresa tra 15 minuti e 360 giorni.", true);
  const button = document.querySelector("#start-batch");
  button.disabled = true;
  try {
    state.simulationPreview = null;
    state.simulationJob = await request("/simulations", { method: "POST", body: JSON.stringify({ recipe_id: recipeId, duration_seconds: Math.ceil(seconds / 900) * 900 }) });
    document.querySelector("#time-dialog").close();
    navigate("simulations");
    renderSimulations();
    pollSimulation();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function pollSimulation() {
  if (!state.simulationJob) return;
  if (state.simulationPollTimer) window.clearTimeout(state.simulationPollTimer);
  try {
    state.simulationJob = await request(`/simulations/${encodeURIComponent(state.simulationJob.id)}`);
    if (state.simulationJob.status === "succeeded") state.simulationPreview = await request(`/simulations/${encodeURIComponent(state.simulationJob.id)}/result`);
    renderSimulations();
    if (["queued", "running"].includes(state.simulationJob.status)) state.simulationPollTimer = window.setTimeout(pollSimulation, 700);
  } catch (error) {
    toast(error.message, true);
  }
}

async function cancelSimulation(id) {
  try {
    await request(`/simulations/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.simulationJob = null;
    state.simulationPreview = null;
    renderSimulations();
    toast("Scenario annullato o scartato.");
  } catch (error) {
    toast(error.message, true);
  }
}

function recipesForDepartment(departmentNumber) {
  return state.recipes.filter((recipe) => recipe.department_number === departmentNumber);
}

function updateSectorRecipeOptions() {
  const departmentNumber = Number(document.querySelector("#sector-department").value);
  const species = document.querySelector("#sector-species").value;
  const recipeSelect = document.querySelector("#sector-recipe");
  const recipes = recipesForDepartment(departmentNumber).filter((recipe) => recipe.plant_type === species);
  recipeSelect.innerHTML = `<option value="">Nessuna ricetta</option>${recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}">${escapeHtml(recipe.id)} · v${recipe.version}</option>`).join("")}`;
  if (recipes.length) recipeSelect.value = recipes[0].id;
}

async function openSectorDialog(departmentNumber, sectorNumber) {
  const department = departmentDefinition(departmentNumber);
  if (!department || ![1, 2].includes(sectorNumber)) return;
  if (zoneAt(departmentNumber, sectorNumber)) return toast("Questo settore è già configurato.", true);

  const dialog = document.querySelector("#sector-dialog");
  const cropFields = document.querySelector("#sector-crop-fields");
  const status = document.querySelector("#sector-form-status");
  const quarantine = departmentNumber === 5;
  document.querySelector("#sector-department").value = departmentNumber;
  document.querySelector("#sector-number").value = sectorNumber;
  document.querySelector("#sector-id").value = `r${departmentNumber}-s${sectorNumber}`;
  document.querySelector("#sector-name").value = `${department.name} - Settore ${sectorNumber}`;
  document.querySelector("#sector-dialog-title").textContent = `${department.name} · Settore ${sectorNumber}`;
  document.querySelector("#sector-dialog-copy").textContent = quarantine
    ? "Il settore di quarantena è misto: la specie resta associata alle singole piante."
    : "Scegli la specie coltivata. Verranno proposte soltanto le ricette compatibili con questo reparto.";
  cropFields.hidden = quarantine;
  document.querySelector("#sector-species").required = !quarantine;
  status.hidden = true;
  status.textContent = "";

  if (!quarantine) {
    if (!state.recipes.length) {
      try {
        await refreshRecipes();
      } catch (error) {
        return toast(error.message, true);
      }
    }
    const species = [...new Set(recipesForDepartment(departmentNumber).map((recipe) => recipe.plant_type))]
      .sort((left, right) => left.localeCompare(right, "it"));
    if (!species.length) return toast("Non ci sono specie configurabili per questo reparto.", true);
    document.querySelector("#sector-species").innerHTML = species.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
    updateSectorRecipeOptions();
  }
  dialog.showModal();
}

async function submitSector(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const submit = document.querySelector("#confirm-sector");
  const status = document.querySelector("#sector-form-status");
  const departmentNumber = Number(document.querySelector("#sector-department").value);
  const sectorNumber = Number(document.querySelector("#sector-number").value);
  const quarantine = departmentNumber === 5;
  const payload = {
    id: document.querySelector("#sector-id").value.trim(),
    name: document.querySelector("#sector-name").value.trim(),
    department_number: departmentNumber,
    sector_number: sectorNumber,
    plant_species: quarantine ? null : document.querySelector("#sector-species").value,
  };
  const recipeId = quarantine ? "" : document.querySelector("#sector-recipe").value;
  if (recipeId) payload.active_recipe_id = recipeId;

  submit.disabled = true;
  submit.textContent = "Configurazione…";
  status.hidden = true;
  try {
    const zone = await request("/zones", { method: "POST", body: JSON.stringify(payload) });
    await refreshSharedData();
    document.querySelector("#sector-dialog").close();
    toast(`Settore ${sectorNumber} configurato per ${quarantine ? "la quarantena" : zone.plant_species}.`);
    navigate(`settore/${encodeURIComponent(zone.id)}`);
  } catch (error) {
    status.hidden = false;
    status.className = "inline-status error";
    status.textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Configura settore";
  }
}

async function refreshGreenhouse(silent = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  const button = document.querySelector("#refresh-greenhouse");
  const alert = document.querySelector("#greenhouse-alert");
  if (button) { button.disabled = true; button.textContent = "Aggiornamento…"; }
  const errors = [];
  try {
    try {
      await refreshSharedData();
      const zone = state.route.name === "settore" ? zoneById(state.route.zoneId) : null;
      if (zone) errors.push(...await loadZoneDetails(zone));
      if (state.route.name === "dashboard") {
        const results = await Promise.all(activeCultivations().map((cultivation) => {
          const activeZone = zoneById(cultivation.zone_id);
          return activeZone ? loadZoneDetails(activeZone, { includeHistory: false, cultivationId: cultivation.id }) : [];
        }));
        errors.push(...results.flat());
      }
    } catch (error) {
      errors.push(error.message);
    }
    renderGreenhouse();
    document.querySelector("#greenhouse-updated").textContent = `Aggiornato alle ${new Date().toLocaleTimeString("it-IT")}`;
    alert.hidden = errors.length === 0;
    alert.className = `inline-status greenhouse-alert${errors.length ? " error" : ""}`;
    alert.textContent = errors.length ? `Aggiornamento parziale: ${errors.join(" · ")}` : "";
    if (errors.length && !silent) toast("Alcuni dati della serra non sono disponibili.", true);
  } finally {
    state.refreshing = false;
    if (button) { button.disabled = false; button.textContent = "Aggiorna"; }
  }
}

function openQuarantineDialog(plantId = "") {
  const candidates = productionPlants();
  const quarantineZones = state.zones.filter((zone) => zone.department_number === 5);
  if (!candidates.length) return toast("Non ci sono piante disponibili da mettere in quarantena.", true);
  if (!quarantineZones.length) return toast("Configura prima almeno un settore nel reparto Quarantena.", true);
  const plantSelect = document.querySelector("#quarantine-plant");
  plantSelect.innerHTML = candidates.map((plant) => `<option value="${escapeHtml(plant.id)}">${escapeHtml(plant.id)} · ${escapeHtml(plant.species)} · ${escapeHtml(zoneById(plant.current_zone_id)?.name || plant.current_zone_id)}</option>`).join("");
  if (plantId && candidates.some((plant) => plant.id === plantId)) plantSelect.value = plantId;
  document.querySelector("#quarantine-zone").innerHTML = quarantineZones.map((zone) => `<option value="${escapeHtml(zone.id)}">${escapeHtml(zone.name)}</option>`).join("");
  document.querySelector("#quarantine-reason").value = "";
  document.querySelector("#quarantine-dialog").showModal();
}

async function submitQuarantine(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const plantId = document.querySelector("#quarantine-plant").value;
  const button = document.querySelector("#confirm-quarantine");
  button.disabled = true;
  try {
    await request(`/plants/${encodeURIComponent(plantId)}/quarantine`, {
      method: "PATCH",
      body: JSON.stringify({
        is_quarantined: true,
        quarantine_zone_id: document.querySelector("#quarantine-zone").value,
        reason: document.querySelector("#quarantine-reason").value.trim(),
      }),
    });
    document.querySelector("#quarantine-dialog").close();
    toast(`${plantId} è stata spostata in quarantena.`);
    await refreshGreenhouse(false);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function openReleaseDialog(plantId) {
  const plant = state.plants.find((item) => item.id === plantId);
  if (!plant) return;
  document.querySelector("#release-plant-id").value = plantId;
  document.querySelector("#release-copy").textContent = `${plant.id} tornerà nel settore di origine ${zoneById(plant.home_zone_id)?.name || plant.home_zone_id}.`;
  document.querySelector("#release-reason").value = "";
  document.querySelector("#release-dialog").showModal();
}

async function submitRelease(event) {
  event.preventDefault();
  const plantId = document.querySelector("#release-plant-id").value;
  const button = document.querySelector("#confirm-release");
  button.disabled = true;
  try {
    const reason = document.querySelector("#release-reason").value.trim();
    await request(`/plants/${encodeURIComponent(plantId)}/quarantine`, {
      method: "PATCH",
      body: JSON.stringify({ is_quarantined: false, reason: reason || null }),
    });
    document.querySelector("#release-dialog").close();
    toast(`${plantId} è tornata nel settore di origine.`);
    await refreshGreenhouse(false);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function openPlantHistory(plantId) {
  const dialog = document.querySelector("#history-dialog");
  document.querySelector("#history-title").textContent = `Storico · ${plantId}`;
  document.querySelector("#history-content").innerHTML = '<div class="data-empty"><strong>Caricamento…</strong></div>';
  dialog.showModal();
  try {
    const movements = await request(`/plants/${encodeURIComponent(plantId)}/movements?limit=100`);
    document.querySelector("#history-content").innerHTML = movements.length
      ? `<ol class="movement-list">${movements.map((movement) => `<li><span class="event-dot"></span><div><strong>${movement.is_quarantined ? "Ingresso in quarantena" : "Rientro nel reparto"}</strong><span>${escapeHtml(zoneById(movement.from_zone_id)?.name || movement.from_zone_id)} → ${escapeHtml(zoneById(movement.to_zone_id)?.name || movement.to_zone_id)}</span><small>${escapeHtml(movement.reason || "Nessuna nota")} · ${escapeHtml(formatDateTime(movement.moved_at))}</small></div></li>`).join("")}</ol>`
      : '<div class="data-empty"><strong>Nessuno spostamento registrato</strong></div>';
  } catch (error) {
    document.querySelector("#history-content").innerHTML = `<div class="inline-status error">${escapeHtml(error.message)}</div>`;
  }
}

function filteredRecipes() {
  const search = document.querySelector("#recipe-search")?.value.trim().toLowerCase() || "";
  const department = document.querySelector("#recipe-department")?.value || "";
  return state.recipes.filter((recipe) => {
    const matchesText = !search || `${recipe.id} ${recipe.plant_type} ${recipe.department_name || ""}`.toLowerCase().includes(search);
    const matchesDepartment = !department || String(recipe.department_number) === department;
    return matchesText && matchesDepartment;
  });
}

function renderRecipeCatalog() {
  const list = document.querySelector("#recipe-list");
  if (!list) return;
  const recipes = filteredRecipes();
  document.querySelector("#recipe-count").textContent = recipes.length;
  list.innerHTML = recipes.length ? recipes.map((recipe) => `
    <button class="recipe-list-item ${state.recipe?.id === recipe.id && state.persistedVersion !== null ? "active" : ""}" type="button" data-recipe-id="${escapeHtml(recipe.id)}" role="option">
      <span class="recipe-department">R${escapeHtml(recipe.department_number || "—")}</span><span><strong>${escapeHtml(recipe.plant_type)}</strong><small>${escapeHtml(recipe.id)} · v${recipe.version} · ${recipe.phases.length} fasi</small></span>
    </button>`).join("") : '<div class="data-empty"><strong>Nessuna ricetta trovata</strong><span>Modifica i filtri del catalogo.</span></div>';
}

function setPath(object, path, value) {
  const parts = path.split(".");
  const final = parts.pop();
  let cursor = object;
  parts.forEach((part) => { cursor = /^\d+$/.test(part) ? cursor[Number(part)] : cursor[part]; });
  cursor[final] = value;
}

function readInputValue(input) {
  if (input.dataset.valueType === "boolean") return input.value === "true";
  if (input.type === "number") return Number(input.value);
  return input.value;
}

function targetTable(phase, phaseIndex) {
  return `<div class="table-scroll"><table class="targets-table"><thead><tr><th>Variabile</th><th>Setpoint</th><th>Operativo min</th><th>Operativo max</th><th>Sicurezza min</th><th>Sicurezza max</th><th>Dose fase</th></tr></thead><tbody>${phase.targets.map((target, targetIndex) => {
    const meta = variableMeta[target.variable];
    const base = `phases.${phaseIndex}.targets.${targetIndex}`;
    return `<tr><td class="target-variable">${meta.label}<span class="target-unit">${meta.unit}</span></td><td><input type="number" step="any" data-path="${base}.setpoint" value="${target.setpoint}" required></td><td><input type="number" step="any" data-path="${base}.allowed_range.minimum" value="${target.allowed_range.minimum}" required></td><td><input type="number" step="any" data-path="${base}.allowed_range.maximum" value="${target.allowed_range.maximum}" required></td><td><input type="number" step="any" data-path="${base}.safety_range.minimum" value="${target.safety_range.minimum}" required></td><td><input type="number" step="any" data-path="${base}.safety_range.maximum" value="${target.safety_range.maximum}" required></td><td><input type="number" min="0" step="any" data-path="${base}.suggested_phase_dose_milliliters" value="${target.suggested_phase_dose_milliliters}" required></td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function phaseMarkup(phase, index) {
  return `<article class="panel phase-card"><div class="phase-header"><div class="phase-title"><span class="phase-index">${index + 1}</span><div><h3>${escapeHtml(phase.name)}</h3><span class="muted">${formatNumber(phase.duration_hours, 0)} ore</span></div></div><div><button class="text-button" type="button" data-duplicate-phase="${index}">Duplica</button><button class="text-button danger" type="button" data-remove-phase="${index}" ${state.recipe.phases.length === 1 ? "disabled" : ""}>Rimuovi</button></div></div><div class="form-grid four"><label>Nome fase<input data-path="phases.${index}.name" value="${escapeHtml(phase.name)}" required></label><label>Durata (ore)<input type="number" min="0.1" step="any" data-path="phases.${index}.duration_hours" value="${phase.duration_hours}" required></label><label>Inizio luce<input type="number" min="0" max="23.99" step="any" data-path="phases.${index}.photoperiod.start_hour" value="${phase.photoperiod.start_hour}" required></label><label>Ore di luce<input type="number" min="0.1" max="24" step="any" data-path="phases.${index}.photoperiod.duration_hours" value="${phase.photoperiod.duration_hours}" required></label></div>${targetTable(phase, index)}</article>`;
}

function defaultParameters(strategy, variable) {
  const target = state.recipe.phases[0].targets.find((item) => item.variable === variable);
  const setpoint = target?.setpoint ?? 0;
  if (strategy === "PID") return { setpoint, proportional_gain: 1, integral_gain: 0, derivative_gain: 0, command_minimum: 0, command_maximum: 1, direction: "increases" };
  if (strategy === "Predictive") return { setpoint, prediction_horizon_steps: 4, response_gain: 0.03, neutral_command: 0, command_minimum: 0, command_maximum: 4, direction: "increases", water_dilution_gain: 2, cumulative_dose_gain: 0.04, substrate_gain: 4 };
  return { lower_threshold: target?.allowed_range.minimum ?? 0, upper_threshold: target?.allowed_range.maximum ?? 1, direction: "increases", active_command: variable === "light" ? 100 : 0.5, inactive_command: 0, bidirectional: false };
}

function parameterInput(controllerIndex, field, value) {
  const path = `controllers.${controllerIndex}.parameters.${field}`;
  if (field === "direction") return `<label>${fieldLabels[field]}<select data-path="${path}"><option value="increases" ${value === "increases" ? "selected" : ""}>Aumenta il processo</option><option value="decreases" ${value === "decreases" ? "selected" : ""}>Riduce il processo</option></select></label>`;
  if (field === "bidirectional") return `<label>${fieldLabels[field]}<select data-path="${path}" data-value-type="boolean"><option value="false" ${!value ? "selected" : ""}>No</option><option value="true" ${value ? "selected" : ""}>Sì</option></select></label>`;
  return `<label>${fieldLabels[field] || field}<input type="number" step="any" data-path="${path}" value="${value ?? 0}" required></label>`;
}

function controllerMarkup(controller, index) {
  const meta = variableMeta[controller.variable];
  const nutrient = ["nitrogen", "phosphorus", "potassium"].includes(controller.variable);
  const allowed = nutrient ? ["Predictive"] : ["Threshold", "PID", "Predictive"];
  const parameters = strategyFields[controller.selected_strategy].map((field) => parameterInput(index, field, controller.parameters[field])).join("");
  const limits = Object.entries(controller.output_limits).map(([field, value]) => `<label>${fieldLabels[field] || field}<input type="number" min="0" step="any" data-path="controllers.${index}.output_limits.${field}" value="${value}" required></label>`).join("");
  return `<details class="controller-card"><summary><div class="controller-name"><span>${meta.short}</span><div><strong>${meta.label}</strong><small>${escapeHtml(controller.input_source)} → ${escapeHtml(controller.actuator)}</small></div></div><div class="controller-strategy"><strong>${controller.selected_strategy}</strong><small>${controller.confirmation_state} · config v${controller.version}</small></div><span aria-hidden="true">⌄</span></summary><div class="controller-body"><div class="form-grid three"><label>Strategy<select data-controller-strategy="${index}" ${nutrient ? "disabled" : ""}>${allowed.map((strategy) => `<option ${strategy === controller.selected_strategy ? "selected" : ""}>${strategy}</option>`).join("")}</select></label><label>Sorgente<input value="${escapeHtml(controller.input_source)}" readonly></label><label>Unità<input value="${escapeHtml(controller.unit)}" readonly></label></div><h3>Parametri della Strategy</h3><div class="form-grid parameter-grid">${parameters}</div><h3>Limiti prioritari</h3><div class="form-grid parameter-grid">${limits}</div></div></details>`;
}

function renderRecipeEditor() {
  const editor = document.querySelector("#recipe-editor");
  if (!state.recipe) return;
  const recipe = state.recipe;
  const care = recipe.care_profile;
  document.querySelector("#recipe-heading").textContent = recipe.plant_type;
  document.querySelector("#recipe-subheading").textContent = `${recipe.department_name || departmentDefinition(recipe.department_number)?.name || "Ricetta personalizzata"} · ${recipe.phases.length} fasi · versione ${recipe.version}`;
  document.querySelector("#duplicate-recipe").disabled = false;
  document.querySelector("#save-recipe").disabled = false;
  editor.className = "recipe-editor";
  editor.innerHTML = `
    <article class="panel"><div class="section-heading"><div><p class="eyebrow">Identità</p><h2>Metadati della ricetta</h2></div><span class="badge outline">v${recipe.version}</span></div><div class="form-grid four"><label>Identificativo<input data-path="id" value="${escapeHtml(recipe.id)}" ${state.persistedVersion !== null ? "readonly" : ""} required pattern="[A-Za-z0-9._-]+"></label><label>Specie<input value="${escapeHtml(recipe.plant_type)}" readonly></label><label>Reparto<input value="${escapeHtml(recipe.department_name || "Personalizzata")}" readonly></label><label>Substrato<select data-path="substrate"><option value="aerated-universal" ${recipe.substrate === "aerated-universal" ? "selected" : ""}>Universale aerato</option><option value="draining" ${recipe.substrate === "draining" ? "selected" : ""}>Drenante</option><option value="organic-retentive" ${recipe.substrate === "organic-retentive" ? "selected" : ""}>Organico ritentivo</option></select></label></div></article>
    ${care ? `<div class="care-grid"><article><span>Luce</span><strong>${escapeHtml(care.light)}</strong></article><article><span>Irrigazione</span><strong>${escapeHtml(care.watering)}</strong></article><article><span>Temperatura</span><strong>${escapeHtml(care.temperature)}</strong></article><article><span>Concimazione</span><strong>${escapeHtml(care.fertilization)}</strong></article></div>` : ""}
    <div class="section-heading spaced"><div><p class="eyebrow">Programma colturale</p><h2>Fasi e target</h2></div><button class="secondary compact" type="button" data-add-phase>+ Aggiungi fase</button></div>
    <div id="phase-editor">${recipe.phases.map(phaseMarkup).join("")}</div>
    <div class="section-heading spaced"><div><p class="eyebrow">Configurazione tecnica</p><h2>Strategy e limiti</h2></div><span class="muted">Sei variabili controllate</span></div>
    <div class="controller-stack">${recipe.controllers.map(controllerMarkup).join("")}</div>`;
  renderRecipeCatalog();
}

function selectRecipe(recipeId) {
  const recipe = state.recipes.find((item) => item.id === recipeId);
  if (!recipe) return;
  state.recipe = clone(recipe);
  state.persistedVersion = recipe.version;
  state.duplicateRecipe = false;
  renderRecipeEditor();
}

function duplicateRecipe() {
  if (!state.recipe) return;
  state.recipe = clone(state.recipe);
  state.recipe.id = `${state.recipe.id}-custom`;
  state.recipe.version = 1;
  delete state.recipe.department_name;
  state.recipe.controllers.forEach((controller) => {
    controller.version = 1;
    controller.confirmation_state = "PENDING_CONFIRMATION";
    controller.confirmed_recipe_version = 0;
  });
  state.persistedVersion = null;
  state.duplicateRecipe = true;
  renderRecipeEditor();
  document.querySelector('[data-path="id"]').focus();
  document.querySelector('[data-path="id"]').select();
  toast("Copia creata: assegna un nuovo identificativo e salvala.");
}

async function saveRecipe() {
  if (!state.recipe || !document.querySelector("#recipe-form").reportValidity()) return;
  const payload = clone(state.recipe);
  delete payload.department_name;
  payload.version = state.persistedVersion === null ? 1 : state.persistedVersion + 1;
  payload.controllers.forEach((controller) => {
    controller.confirmation_state = "PENDING_CONFIRMATION";
    controller.confirmed_recipe_version = 0;
    controller.version = state.persistedVersion === null ? 1 : controller.version + 1;
  });
  const button = document.querySelector("#save-recipe");
  button.disabled = true;
  button.textContent = "Salvataggio…";
  try {
    const saved = await request("/recipes", { method: "POST", body: JSON.stringify(payload) });
    await refreshRecipes();
    state.recipe = clone(saved);
    state.persistedVersion = saved.version;
    state.duplicateRecipe = false;
    renderRecipeEditor();
    toast(`Ricetta ${saved.id} salvata come versione ${saved.version}.`);
  } catch (error) {
    toast(error.status === 409 ? "La ricetta è stata aggiornata altrove. Ricaricala e riprova." : error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Salva nuova versione";
  }
}

async function refreshRecipes() {
  state.recipes = await request("/recipes");
  renderRecipeCatalog();
  refreshEdgeZoneOptions();
  syncTimeControl(state.route);
}

function compatibleRecipes(zone) {
  return state.recipes.filter((recipe) => recipe.plant_type === zone.plant_species && (recipe.department_number === null || recipe.department_number === zone.department_number));
}

async function persistZoneRecipe(zone, recipeId) {
  if (!recipeId) throw new Error("Seleziona una ricetta compatibile.");
  const updated = await request(`/zones/${encodeURIComponent(zone.id)}`, {
    method: "PATCH",
    body: JSON.stringify({ active_recipe_id: recipeId }),
  });
  state.zones = state.zones.map((item) => item.id === updated.id ? updated : item);
  toast(`Ricetta ${recipeId} assegnata a ${updated.name}.`);
  if (document.querySelector("#view-edge").classList.contains("active")) await refreshEdge(true);
  else renderGreenhouse();
  return updated;
}

async function assignRecipeFromSector(button) {
  const zone = zoneById(button.dataset.assignZoneRecipe);
  const select = button.closest("[data-recipe-assignment]")?.querySelector("[data-zone-recipe-select]");
  if (!zone || !select) return;
  button.disabled = true;
  try {
    await persistZoneRecipe(zone, select.value);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function refreshEdgeZoneOptions() {
  const select = document.querySelector("#edge-zone");
  if (!select) return;
  const productionZones = state.zones.filter((zone) => zone.department_number < 5);
  const current = state.edgeZoneId || select.value;
  select.innerHTML = `<option value="">Seleziona un settore</option>${productionZones.map((zone) => `<option value="${escapeHtml(zone.id)}">R${zone.department_number} · S${zone.sector_number} · ${escapeHtml(zone.plant_species)}</option>`).join("")}`;
  if (productionZones.some((zone) => zone.id === current)) select.value = current;
}

function edgeLifecycleActions(zone, disabled) {
  const controls = [];
  if (zone.lifecycle_state === "Idle") controls.push('<button class="primary" type="button" data-edge-command="activate">Attiva coltivazione</button>');
  if (zone.lifecycle_state === "Running") controls.push('<button class="secondary" type="button" data-edge-command="pause">Pausa</button>');
  if (zone.lifecycle_state === "Paused") controls.push('<button class="primary" type="button" data-edge-command="resume">Riprendi</button>');
  if (["Running", "Paused", "Error"].includes(zone.lifecycle_state)) controls.push('<button class="secondary danger-button" type="button" data-edge-command="stop">Arresta</button>');
  return controls.map((markup) => disabled ? markup.replace("type=\"button\"", 'type="button" disabled') : markup).join("");
}

function renderEdgeConsole(zone) {
  const details = state.zoneDetails[zone.id] || {};
  const pending = details.commands || [];
  const recipes = compatibleRecipes(zone);
  const unavailable = !zone.assigned_edge_id || zone.administrative_status !== "active";
  const active = ["Running", "Paused"].includes(zone.lifecycle_state);
  document.querySelector("#edge-zone-summary").className = `inline-status${unavailable ? " error" : ""}`;
  document.querySelector("#edge-zone-summary").textContent = !zone.assigned_edge_id ? "Assegnazione Edge in corso nel backend." : `Gestito dal backend · ${zone.assigned_edge_id} · ${stateLabel(zone.status)} · ${stateLabel(zone.lifecycle_state)}`;
  document.querySelector("#edge-content").innerHTML = `
    <article class="panel edge-hero"><div><p class="eyebrow">${escapeHtml(zone.department_name)}</p><h2>${escapeHtml(zone.name)}</h2><p>${escapeHtml(zone.plant_species)} · ${escapeHtml(zone.assigned_edge_id || "Assegnazione backend in corso")}</p></div><div class="edge-badges">${badge(zone.status)}${badge(zone.lifecycle_state)}${badge(zone.operational_state)}</div><dl class="edge-facts"><div><dt>Ricetta</dt><dd>${escapeHtml(zone.active_recipe_id || "Non assegnata")}</dd></div><div><dt>Fase</dt><dd>${escapeHtml(zone.current_phase || "Non disponibile")}</dd></div><div><dt>Velocità</dt><dd>${formatNumber(zone.time_scale, 0)}×</dd></div><div><dt>Ultimo contatto</dt><dd>${escapeHtml(formatDateTime(zone.last_edge_contact))}</dd></div></dl></article>
    ${unavailable ? '<div class="inline-status error">Il backend non ha ancora completato il provisioning Edge oppure il settore non è attivo. La ricetta può comunque essere salvata.</div>' : ""}
    <div class="edge-command-grid">
      <article class="panel command-card"><div class="section-heading"><div><p class="eyebrow">Configurazione</p><h2>Ricetta runtime</h2></div></div><p class="muted">Conferma le sei configurazioni dopo il caricamento oppure forza il passaggio alla fase successiva.</p><div class="command-actions"><button class="secondary" type="button" data-edge-command="confirm-all" ${!active || unavailable ? "disabled" : ""}>Conferma tutte</button><button class="secondary" type="button" data-edge-command="advance" ${zone.lifecycle_state !== "Running" || unavailable ? "disabled" : ""}>Avanza fase</button></div></article>
      <article class="panel command-card fault-card"><div class="section-heading"><div><p class="eyebrow">Diagnostica</p><h2>Iniezione fault</h2></div></div><div class="form-grid two"><label>ID fault<input id="fault-id" value="fault-${Date.now()}" ${!active || unavailable ? "disabled" : ""}></label><label>Componente<select id="fault-target-type" ${!active || unavailable ? "disabled" : ""}><option value="sensor">Sensore</option><option value="actuator">Attuatore</option></select></label><label>Target<select id="fault-target" ${!active || unavailable ? "disabled" : ""}></select></label><label>Modalità<select id="fault-mode" ${!active || unavailable ? "disabled" : ""}></select></label><label>Valore (se richiesto)<input id="fault-value" type="number" step="any" ${!active || unavailable ? "disabled" : ""}></label><label>Durata simulata (s)<input id="fault-duration" type="number" min="1" step="1" ${!active || unavailable ? "disabled" : ""}></label></div><div class="command-actions"><button class="secondary" type="button" data-edge-command="inject-fault" ${!active || unavailable ? "disabled" : ""}>Inietta fault</button><button class="text-button danger" type="button" data-edge-command="reset-fault" ${!active || unavailable ? "disabled" : ""}>Reset ID indicato</button></div></article>
      <article class="panel command-card emergency-card"><div class="section-heading"><div><p class="eyebrow">Sicurezza</p><h2>Emergenza</h2></div>${badge(zone.operational_state)}</div><label>Motivazione<input id="emergency-reason" value="arresto richiesto dalla control room" ${!active || unavailable ? "disabled" : ""}></label><div class="command-actions"><button class="danger-button" type="button" data-edge-command="emergency" ${!active || unavailable ? "disabled" : ""}>Arresto di emergenza</button><button class="secondary" type="button" data-edge-command="reset-emergency" ${zone.operational_state !== "EmergencyLockdown" || unavailable ? "disabled" : ""}>Richiedi recovery</button></div></article>
      <article class="panel command-card"><div class="section-heading"><div><p class="eyebrow">Coda backend</p><h2>Comandi pendenti</h2></div><span class="badge outline">${pending.length}</span></div>${pending.length ? `<div class="pending-list">${pending.map((command) => `<div><strong>${escapeHtml(command.command_type)}</strong><span>${escapeHtml(command.command_id)}</span>${badge(command.status)}</div>`).join("")}</div>` : '<div class="data-empty"><strong>Nessun comando pendente</strong><span>L’Edge ha elaborato tutta la coda.</span></div>'}</article>
    </div>
    <article class="panel"><div class="section-heading"><div><p class="eyebrow">Audit Edge</p><h2>Eventi recenti</h2></div><span class="badge outline">${details.events?.length || 0}</span></div>${eventsMarkup(details.events || [], 14)}</article>`;
  updateFaultFields();
}

function updateFaultFields() {
  const type = document.querySelector("#fault-target-type")?.value;
  const target = document.querySelector("#fault-target");
  const mode = document.querySelector("#fault-mode");
  if (!target || !mode) return;
  const targets = type === "actuator" ? actuatorTargets : sensorTargets;
  const modes = type === "actuator" ? actuatorFaultModes : sensorFaultModes;
  target.innerHTML = targets.map((value) => `<option value="${value}">${value}</option>`).join("");
  mode.innerHTML = modes.map((value) => `<option value="${value}">${value}</option>`).join("");
}

async function refreshEdge(silent = false) {
  try {
    await refreshSharedData();
    const zone = zoneById(state.edgeZoneId || document.querySelector("#edge-zone").value);
    if (!zone) {
      document.querySelector("#edge-content").innerHTML = '<div class="route-empty panel"><span aria-hidden="true">◉</span><h2>Nessun settore selezionato</h2><p>Seleziona un settore produttivo. Il provisioning Edge è gestito dal backend.</p></div>';
      return;
    }
    state.edgeZoneId = zone.id;
    document.querySelector("#edge-zone").value = zone.id;
    const errors = await loadZoneDetails(zone, { includeHistory: false });
    try {
      state.zoneDetails[zone.id].commands = await request(`/zones/${encodeURIComponent(zone.id)}/commands?limit=100`);
    } catch (error) {
      errors.push(`commands: ${error.message}`);
    }
    renderEdgeConsole(zone);
    if (errors.length && !silent) toast(`Aggiornamento Edge parziale: ${errors.join(" · ")}`, true);
  } catch (error) {
    if (!silent) toast(error.message, true);
  }
}

function commandId(type) {
  const suffix = globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${type.toLowerCase()}-${suffix}`;
}

async function enqueueCommand(zone, commandType, payload = {}, options = {}) {
  const command = await request(`/zones/${encodeURIComponent(zone.id)}/commands`, {
    method: "POST",
    body: JSON.stringify({ command_id: commandId(commandType), command_type: commandType, payload }),
  });
  toast(`${commandType} accodato per ${zone.name}.`);
  if (options.refresh !== false) {
    await refreshEdge(true);
    window.setTimeout(() => refreshEdge(true), 1400);
  }
  return command;
}

async function handleEdgeCommand(action) {
  const zone = zoneById(state.edgeZoneId);
  if (!zone) return;
  const recipeId = document.querySelector("#edge-recipe")?.value;
  try {
    if (action === "assign-recipe") {
      await persistZoneRecipe(zone, recipeId);
    } else if (action === "activate") {
      if (!recipeId) throw new Error("Seleziona una ricetta compatibile.");
      await enqueueCommand(zone, "ActivateCultivation", { cultivation_id: `${zone.id}-${Date.now()}`, recipe_id: recipeId });
    } else if (action === "pause") await enqueueCommand(zone, "PauseCultivation");
    else if (action === "resume") await enqueueCommand(zone, "ResumeCultivation");
    else if (action === "stop") {
      if (window.confirm("Arrestare la coltivazione? Il runtime e gli attuatori verranno fermati.")) await enqueueCommand(zone, "StopCultivation");
    } else if (action === "load-recipe") {
      if (!recipeId) throw new Error("Seleziona una ricetta compatibile.");
      await enqueueCommand(zone, "LoadRecipe", { recipe_id: recipeId });
    } else if (action === "speed") {
      const timeScale = Number(document.querySelector("#edge-speed").value);
      if (timeScale < 1 || timeScale > 60) throw new Error("La velocità deve essere compresa fra 1× e 60×.");
      await enqueueCommand(zone, "SetSimulationSpeed", { time_scale: timeScale });
    } else if (action === "duration") {
      const duration = Number(document.querySelector("#edge-duration").value);
      if (!(duration > 0)) throw new Error("La durata deve essere positiva.");
      await enqueueCommand(zone, "SetSimulationDuration", { duration_seconds: duration });
    } else if (action === "continuous") await enqueueCommand(zone, "SetSimulationDuration", { duration_seconds: null });
    else if (action === "advance") await enqueueCommand(zone, "AdvanceRecipePhase");
    else if (action === "confirm-all") {
      for (const variable of Object.keys(variableMeta)) await enqueueCommand(zone, "ConfirmConfiguration", { variable });
    } else if (action === "inject-fault") {
      const faultId = document.querySelector("#fault-id").value.trim();
      if (!faultId) throw new Error("Inserisci un identificativo del fault.");
      const mode = document.querySelector("#fault-mode").value;
      const valueText = document.querySelector("#fault-value").value;
      const durationText = document.querySelector("#fault-duration").value;
      const requiresValue = ["sensor_offset", "actuator_slow_response"].includes(mode);
      if (requiresValue && valueText === "") throw new Error("La modalità selezionata richiede un valore.");
      const payload = { fault_id: faultId, target_type: document.querySelector("#fault-target-type").value, target: document.querySelector("#fault-target").value, mode };
      if (valueText !== "") payload.value = Number(valueText);
      if (durationText !== "") payload.duration_seconds = Number(durationText);
      await enqueueCommand(zone, "InjectFault", payload);
    } else if (action === "reset-fault") {
      const faultId = document.querySelector("#fault-id").value.trim();
      if (!faultId) throw new Error("Inserisci l’ID del fault da rimuovere.");
      await enqueueCommand(zone, "ResetFault", { fault_id: faultId });
    } else if (action === "emergency") {
      if (window.confirm("Inviare un arresto di emergenza? Gli attuatori verranno bloccati immediatamente.")) await enqueueCommand(zone, "EmergencyStop", { reason: document.querySelector("#emergency-reason").value.trim() || "arresto richiesto dalla control room" });
    } else if (action === "reset-emergency") await enqueueCommand(zone, "ResetEmergency");
  } catch (error) {
    toast(error.message, true);
  }
}

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  document.querySelectorAll("[data-go-to]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.goTo)));
  document.querySelector("#mobile-menu").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
  document.querySelector("#refresh-greenhouse").addEventListener("click", () => refreshGreenhouse(false));
  document.querySelector("#edge-refresh").addEventListener("click", () => refreshEdge(false));
  document.querySelector("#edge-zone").addEventListener("change", (event) => { state.edgeZoneId = event.target.value; refreshEdge(false); });
  document.querySelector("#time-control-trigger").addEventListener("click", () => openTimeDialog("runtime"));
  document.querySelectorAll('input[name="time-mode"]').forEach((input) => input.addEventListener("change", () => setTimeMode(input.value)));
  document.querySelector("#runtime-duration-mode").addEventListener("change", (event) => { document.querySelector("#runtime-duration-fields").hidden = event.target.value !== "limited"; });
  document.querySelector("#apply-runtime-time").addEventListener("click", applyRuntimeTime);
  document.querySelector("#start-batch").addEventListener("click", startBatchSimulation);
  document.querySelector("#cultivation-form").addEventListener("submit", submitCultivation);
  document.querySelector("#cultivation-zone").addEventListener("change", updateCultivationDialog);
  document.querySelector("#cultivation-recipe").addEventListener("change", updateCultivationDialog);
  document.querySelector("#recipe-search").addEventListener("input", renderRecipeCatalog);
  document.querySelector("#recipe-department").addEventListener("change", renderRecipeCatalog);
  document.querySelector("#duplicate-recipe").addEventListener("click", duplicateRecipe);
  document.querySelector("#save-recipe").addEventListener("click", saveRecipe);
  document.querySelector("#sector-form").addEventListener("submit", submitSector);
  document.querySelector("#sector-species").addEventListener("change", updateSectorRecipeOptions);
  document.querySelector("#quarantine-form").addEventListener("submit", submitQuarantine);
  document.querySelector("#release-form").addEventListener("submit", submitRelease);
  window.addEventListener("hashchange", applyRoute);

  document.querySelector("#recipe-form").addEventListener("input", (event) => {
    if (event.target.dataset.path && state.recipe) setPath(state.recipe, event.target.dataset.path, readInputValue(event.target));
  });
  document.querySelector("#recipe-form").addEventListener("change", (event) => {
    if (event.target.dataset.path && state.recipe) setPath(state.recipe, event.target.dataset.path, readInputValue(event.target));
    if (event.target.dataset.controllerStrategy !== undefined) {
      const index = Number(event.target.dataset.controllerStrategy);
      state.recipe.controllers[index].selected_strategy = event.target.value;
      state.recipe.controllers[index].parameters = defaultParameters(event.target.value, state.recipe.controllers[index].variable);
      renderRecipeEditor();
    }
  });

  document.addEventListener("click", (event) => {
    const goTo = event.target.closest("[data-go-to]");
    if (goTo) return navigate(goTo.dataset.goTo);
    const routeButton = event.target.closest("[data-greenhouse-route]");
    if (routeButton) return navigate(routeButton.dataset.greenhouseRoute);
    const departmentButton = event.target.closest("[data-department]");
    if (departmentButton) return navigate(`reparto/${departmentButton.dataset.department}`);
    const configureSector = event.target.closest("[data-configure-sector]");
    if (configureSector) {
      const [departmentNumber, sectorNumber] = configureSector.dataset.configureSector.split("-").map(Number);
      return openSectorDialog(departmentNumber, sectorNumber);
    }
    const assignZoneRecipe = event.target.closest("[data-assign-zone-recipe]");
    if (assignZoneRecipe) return assignRecipeFromSector(assignZoneRecipe);
    const openRecipes = event.target.closest("[data-open-recipes]");
    if (openRecipes) return navigate("recipe");
    const addCultivation = event.target.closest("[data-add-cultivation]");
    if (addCultivation) return openCultivationDialog();
    const openCultivation = event.target.closest("[data-open-cultivation]");
    if (openCultivation) return navigate(`cultivation/${encodeURIComponent(openCultivation.dataset.openCultivation)}`);
    const cultivationAction = event.target.closest("[data-cultivation-action]");
    if (cultivationAction) return actOnCultivation(cultivationAction.dataset.cultivationId, cultivationAction.dataset.cultivationAction);
    const cultivationEmergency = event.target.closest("[data-cultivation-emergency]");
    if (cultivationEmergency) {
      const cultivation = cultivationById(cultivationEmergency.dataset.cultivationEmergency);
      const zone = zoneById(cultivation?.zone_id);
      if (zone && window.confirm("Inviare un arresto di emergenza? Gli attuatori verranno bloccati immediatamente.")) return enqueueCommand(zone, "EmergencyStop", { reason: "arresto richiesto dal dettaglio agronomico" }, { refresh: false }).then(() => refreshCultivations(true)).catch((error) => toast(error.message, true));
    }
    const openTime = event.target.closest("[data-open-time-mode]");
    if (openTime) return openTimeDialog(openTime.dataset.openTimeMode);
    const preset = event.target.closest("[data-batch-days]");
    if (preset) { document.querySelector("#batch-duration").value = preset.dataset.batchDays; document.querySelector("#batch-duration-unit").value = "days"; return; }
    const cancelBatch = event.target.closest("[data-cancel-simulation]");
    if (cancelBatch) return cancelSimulation(cancelBatch.dataset.cancelSimulation);
    const chartWindow = event.target.closest("[data-chart-window]");
    if (chartWindow) { state.chartWindow = chartWindow.dataset.chartWindow; if (state.route.name === "cultivation") renderCultivations(); else if (state.route.name === "settore") renderGreenhouse(); else if (state.route.name === "simulations") renderSimulations(); return; }
    const zoneButton = event.target.closest("[data-zone-id]");
    if (zoneButton) return navigate(`settore/${encodeURIComponent(zoneButton.dataset.zoneId)}`);
    const quarantineButton = event.target.closest("[data-open-quarantine]");
    if (quarantineButton) return openQuarantineDialog();
    const plantQuarantine = event.target.closest("[data-quarantine-plant]");
    if (plantQuarantine) return openQuarantineDialog(plantQuarantine.dataset.quarantinePlant);
    const release = event.target.closest("[data-release-plant]");
    if (release) return openReleaseDialog(release.dataset.releasePlant);
    const history = event.target.closest("[data-history-plant]");
    if (history) return openPlantHistory(history.dataset.historyPlant);
    const closeDialog = event.target.closest("[data-close-dialog]");
    if (closeDialog) return closeDialogElement(closeDialog.dataset.closeDialog);
    const recipeButton = event.target.closest("[data-recipe-id]");
    if (recipeButton) return selectRecipe(recipeButton.dataset.recipeId);
    const addPhase = event.target.closest("[data-add-phase]");
    if (addPhase && state.recipe) {
      const phase = clone(state.recipe.phases.at(-1));
      phase.name = `Fase ${state.recipe.phases.length + 1}`;
      state.recipe.phases.push(phase);
      return renderRecipeEditor();
    }
    const duplicatePhase = event.target.closest("[data-duplicate-phase]");
    if (duplicatePhase && state.recipe) {
      const index = Number(duplicatePhase.dataset.duplicatePhase);
      const phase = clone(state.recipe.phases[index]);
      phase.name = `${phase.name} copia`;
      state.recipe.phases.splice(index + 1, 0, phase);
      return renderRecipeEditor();
    }
    const removePhase = event.target.closest("[data-remove-phase]");
    if (removePhase && state.recipe && state.recipe.phases.length > 1) {
      state.recipe.phases.splice(Number(removePhase.dataset.removePhase), 1);
      return renderRecipeEditor();
    }
    const edgeCommand = event.target.closest("[data-edge-command]");
    if (edgeCommand) return handleEdgeCommand(edgeCommand.dataset.edgeCommand);
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "fault-target-type") updateFaultFields();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openDialog = [...document.querySelectorAll("dialog")].find((dialog) => dialog.open);
    if (openDialog) {
      event.preventDefault();
      closeDialog(openDialog);
    }
  });
}

function closeDialogElement(dialogId) {
  closeDialog(document.querySelector(`#${dialogId}`));
}

async function initialize() {
  bindEvents();
  try {
    await Promise.all([refreshSharedData(), refreshRecipes()]);
    if (state.recipes.length) selectRecipe(state.recipes[0].id);
  } catch (error) {
    toast(`Inizializzazione non riuscita: ${error.message}`, true);
  }
  if (!window.location.hash || window.location.hash === "#overview") navigate("dashboard");
  else applyRoute();
}

initialize();
