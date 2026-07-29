const API = "";

const variableMeta = {
  soil_moisture: { label: "Umidità terreno", short: "H₂O", unit: "%", source: "Sensore" },
  light: { label: "Luce", short: "PAR", unit: "µmol/m²s", source: "Sensore" },
  ph: { label: "pH", short: "pH", unit: "pH", source: "Sensore" },
  nitrogen: { label: "Azoto", short: "N", unit: "mg/L", source: "Modello Edge" },
  phosphorus: { label: "Fosforo", short: "P", unit: "mg/L", source: "Modello Edge" },
  potassium: { label: "Potassio", short: "K", unit: "mg/L", source: "Modello Edge" },
};

const strategyFields = {
  Threshold: [
    "lower_threshold", "upper_threshold", "active_command", "inactive_command", "direction", "bidirectional",
  ],
  PID: [
    "setpoint", "proportional_gain", "integral_gain", "derivative_gain",
    "command_minimum", "command_maximum", "direction",
  ],
  Predictive: [
    "setpoint", "prediction_horizon_steps", "response_gain", "neutral_command",
    "command_minimum", "command_maximum", "direction", "water_dilution_gain",
    "cumulative_dose_gain", "substrate_gain",
  ],
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

const state = {
  recipes: [],
  recipe: null,
  template: null,
  persistedVersion: null,
  simulation: [],
  simulationIndex: -1,
  timer: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `Errore HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (Array.isArray(body.detail)) {
        detail = body.detail.map((item) => item.msg).join(" · ");
      } else if (body.detail) {
        detail = body.detail;
      }
    } catch (_) {
      // Keep the HTTP fallback.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function toast(message, isError = false) {
  const region = document.querySelector("#toast-region");
  const element = document.createElement("div");
  element.className = `toast${isError ? " error" : ""}`;
  element.textContent = message;
  region.append(element);
  window.setTimeout(() => element.remove(), 4200);
}

function setView(view) {
  const titles = {
    overview: "Panoramica",
    recipe: "Gestione ricetta",
    simulation: "Simulazione Edge",
  };
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${view}`));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.querySelector("#page-title").textContent = titles[view];
  document.querySelector(".sidebar").classList.remove("open");
  window.location.hash = view;
}

function setSystemNode(id, ready) {
  const node = document.querySelector(id);
  node.className = `dot ${ready ? "ready" : "error"}`;
}

async function refreshSystemStatus() {
  try {
    const status = await request("/system/status");
    const ready = status.status === "ready";
    setSystemNode("#side-dashboard", status.dashboard === "ready");
    setSystemNode("#side-backend", status.backend === "ready");
    setSystemNode("#side-edge", status.edge === "ready");
    document.querySelector("#flow-dashboard").textContent = status.dashboard === "ready" ? "Pronta" : "Non disponibile";
    document.querySelector("#flow-backend").textContent = status.backend === "ready" ? "Pronto" : "Non disponibile";
    document.querySelector("#flow-edge").textContent = status.edge === "ready" ? "Pronto" : "Da compilare";
    const pill = document.querySelector("#connection-pill");
    pill.className = `pill ${ready ? "ready" : "error"}`;
    pill.innerHTML = `<span class="pulse"></span>${ready ? "Sistema pronto" : "Sistema parzialmente disponibile"}`;
    const badge = document.querySelector("#overview-system-badge");
    badge.className = `badge ${ready ? "ready" : "limited"}`;
    badge.textContent = ready ? "Operativo" : "Parziale";
  } catch (error) {
    ["#side-dashboard", "#side-backend", "#side-edge"].forEach((id) => setSystemNode(id, false));
    const pill = document.querySelector("#connection-pill");
    pill.className = "pill error";
    pill.innerHTML = '<span class="pulse"></span>Backend non raggiungibile';
  }
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("it-IT", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function metricCards(cycle = null) {
  const sensors = cycle?.sensors || {};
  const models = cycle?.models || {};
  const values = [
    ["Temperatura", "T", sensors.temperature_c, "°C", "Sensore aria"],
    ["Umidità aria", "RH", sensors.air_humidity_percent, "%", "Sensore aria"],
    ["Umidità terreno", "H₂O", sensors.soil_moisture_percent, "%", "Sensore terreno"],
    ["Luce", "PAR", sensors.light_ppfd_umol_m2_s, "µmol/m²s", "Sensore PAR"],
    ["pH", "pH", sensors.ph, "", "Elettrodo"],
    ["Azoto", "N", models.nitrogen_mg_per_liter, "mg/L", "Modello Edge"],
    ["Fosforo", "P", models.phosphorus_mg_per_liter, "mg/L", "Modello Edge"],
    ["Potassio", "K", models.potassium_mg_per_liter, "mg/L", "Modello Edge"],
  ];
  return values.map(([label, symbol, value, unit, source]) => `
    <article class="metric-card">
      <header><span>${label}</span><span class="metric-symbol">${symbol}</span></header>
      <div class="metric-value">${formatNumber(value)} <small>${unit}</small></div>
      <footer>${source}${value === null ? " · dropout" : ""}</footer>
    </article>`).join("");
}

function actuatorMarkup(cycle = null) {
  if (!cycle) {
    return [
      ["P", "Pompa acqua", "In attesa", "—"],
      ["LED", "Illuminazione", "In attesa", "—"],
      ["NPK", "Valvole nutrienti", "In attesa", "—"],
      ["pH", "Correttori pH", "In attesa", "—"],
    ].map(([icon, title, detail, value]) => `
      <div class="actuator-row"><span class="actuator-icon">${icon}</span>
      <div><strong>${title}</strong><small>${detail}</small></div><span class="state-value">${value}</span></div>`).join("");
  }
  const command = cycle.actuators.command;
  const output = cycle.actuators.output;
  const delivered = cycle.delivered.fertilizer_milliliters;
  const nutrients = ["nitrogen", "phosphorus", "potassium"].map((key) => `${key[0].toUpperCase()}: ${formatNumber(delivered[key], 2)} mL`).join(" · ");
  const phDose = `pH+: ${formatNumber(delivered["ph-up"], 2)} · pH−: ${formatNumber(delivered["ph-down"], 2)} mL`;
  return `
    <div class="actuator-row"><span class="actuator-icon">P</span><div><strong>Pompa acqua</strong>
      <small>${formatNumber(output.water_pump_flow_liters_per_hour)} L/h · erogati ${formatNumber(cycle.delivered.water_liters, 2)} L</small></div>
      <span class="state-value ${output.water_pump_on ? "on" : ""}">${output.water_pump_on ? "ON" : "OFF"}</span></div>
    <div class="actuator-row"><span class="actuator-icon">LED</span><div><strong>Illuminazione</strong>
      <small>${formatNumber(output.lighting_power_watts)} W fisici</small></div>
      <span class="state-value ${command.lighting_percent > 0 ? "on" : ""}">${formatNumber(command.lighting_percent, 0)}%</span></div>
    <div class="actuator-row"><span class="actuator-icon">NPK</span><div><strong>Valvole nutrienti</strong>
      <small>${nutrients}</small></div><span class="state-value">${Object.values(command.fertilizer_valves_open).some(Boolean) ? "APERTE" : "CHIUSE"}</span></div>
    <div class="actuator-row"><span class="actuator-icon">pH</span><div><strong>Correttori pH</strong>
      <small>${phDose}</small></div><span class="state-value">${command.fertilizer_valves_open["ph-up"] || command.fertilizer_valves_open["ph-down"] ? "ATTIVI" : "FERMI"}</span></div>`;
}

function renderEmptyMetrics() {
  const markup = metricCards();
  document.querySelector("#sensor-grid").innerHTML = markup;
  document.querySelector("#simulation-sensors").innerHTML = markup;
  document.querySelector("#overview-actuators").innerHTML = actuatorMarkup();
  document.querySelector("#simulation-actuators").innerHTML = actuatorMarkup();
}

function updateOverviewRecipe() {
  const recipe = state.recipe;
  document.querySelector("#overview-recipe-name").textContent = recipe ? recipe.id : "Nessuna ricetta salvata";
  document.querySelector("#overview-recipe-meta").textContent = recipe
    ? `Pomodoro · ${recipe.phases.length} fasi · versione ${recipe.version}`
    : "Crea la prima ricetta per iniziare la simulazione.";
  if (state.simulationIndex < 0) {
    document.querySelector("#overview-phase").textContent = recipe?.phases?.[0]?.name || "—";
  }
}

async function refreshRecipeList(preferredId = null) {
  state.recipes = await request("/recipes");
  const options = state.recipes.map((recipe) =>
    `<option value="${escapeHtml(recipe.id)}">${escapeHtml(recipe.id)} · v${recipe.version}</option>`
  ).join("");
  const selectors = [document.querySelector("#recipe-selector"), document.querySelector("#simulation-recipe")];
  selectors.forEach((select) => {
    const placeholder = select.id === "recipe-selector" ? "Nessuna ricetta" : "Seleziona una ricetta";
    select.innerHTML = `<option value="">${placeholder}</option>${options}`;
    if (preferredId && state.recipes.some((item) => item.id === preferredId)) select.value = preferredId;
  });
}

async function loadRecipe(id) {
  if (!id) return;
  try {
    state.recipe = await request(`/recipes/${encodeURIComponent(id)}`);
    state.persistedVersion = state.recipe.version;
    renderRecipeEditor();
    updateOverviewRecipe();
    document.querySelector("#recipe-selector").value = id;
    document.querySelector("#simulation-recipe").value = id;
  } catch (error) {
    toast(error.message, true);
  }
}

function setPath(object, path, value) {
  const parts = path.split(".");
  const final = parts.pop();
  let cursor = object;
  parts.forEach((part) => { cursor = cursor[Number.isInteger(Number(part)) && String(Number(part)) === part ? Number(part) : part]; });
  cursor[final] = value;
}

function targetTable(phase, phaseIndex) {
  return `
    <div class="table-scroll">
      <table class="targets-table">
        <thead><tr><th>Variabile</th><th>Setpoint</th><th>Operativo min</th><th>Operativo max</th><th>Sicurezza min</th><th>Sicurezza max</th><th>Dose fase</th></tr></thead>
        <tbody>${phase.targets.map((target, targetIndex) => {
          const meta = variableMeta[target.variable];
          const base = `phases.${phaseIndex}.targets.${targetIndex}`;
          return `<tr>
            <td class="target-variable">${meta.label}<span class="target-unit">${meta.unit}</span></td>
            <td><input type="number" step="any" data-path="${base}.setpoint" value="${target.setpoint}" required></td>
            <td><input type="number" step="any" data-path="${base}.allowed_range.minimum" value="${target.allowed_range.minimum}" required></td>
            <td><input type="number" step="any" data-path="${base}.allowed_range.maximum" value="${target.allowed_range.maximum}" required></td>
            <td><input type="number" step="any" data-path="${base}.safety_range.minimum" value="${target.safety_range.minimum}" required></td>
            <td><input type="number" step="any" data-path="${base}.safety_range.maximum" value="${target.safety_range.maximum}" required></td>
            <td><input type="number" min="0" step="any" data-path="${base}.suggested_phase_dose_milliliters" value="${target.suggested_phase_dose_milliliters}" required></td>
          </tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;
}

function renderPhases() {
  document.querySelector("#phase-editor").innerHTML = state.recipe.phases.map((phase, index) => `
    <article class="panel phase-card">
      <div class="phase-header">
        <div class="phase-title"><span class="phase-index">${index + 1}</span><div><h3>${escapeHtml(phase.name)}</h3><span class="muted">${formatNumber(phase.duration_hours, 0)} ore</span></div></div>
        <div><button class="text-button" type="button" data-duplicate-phase="${index}">Duplica</button>
        <button class="text-button danger" type="button" data-remove-phase="${index}" ${state.recipe.phases.length === 1 ? "disabled" : ""}>Rimuovi</button></div>
      </div>
      <div class="form-grid three">
        <label>Nome fase<input data-path="phases.${index}.name" value="${escapeHtml(phase.name)}" required></label>
        <label>Durata (ore)<input type="number" min="1" step="any" data-path="phases.${index}.duration_hours" value="${phase.duration_hours}" required></label>
        <label>Inizio luce (ora)<input type="number" min="0" max="23.99" step="any" data-path="phases.${index}.photoperiod.start_hour" value="${phase.photoperiod.start_hour}" required></label>
        <label>Durata luce (ore)<input type="number" min=".1" max="24" step="any" data-path="phases.${index}.photoperiod.duration_hours" value="${phase.photoperiod.duration_hours}" required></label>
      </div>
      ${targetTable(phase, index)}
    </article>`).join("");
}

function defaultParameters(strategy, variable) {
  const target = state.recipe.phases[0].targets.find((item) => item.variable === variable);
  const setpoint = target?.setpoint ?? 0;
  if (strategy === "PID") {
    return { setpoint, proportional_gain: 1, integral_gain: 0, derivative_gain: 0, command_minimum: 0, command_maximum: 1, direction: "increases" };
  }
  if (strategy === "Predictive") {
    return { setpoint, prediction_horizon_steps: 4, response_gain: 0.03, neutral_command: 0, command_minimum: 0, command_maximum: 4, direction: "increases", water_dilution_gain: 2, cumulative_dose_gain: 0.04, substrate_gain: 4 };
  }
  return { lower_threshold: target?.allowed_range.minimum ?? 0, upper_threshold: target?.allowed_range.maximum ?? 1, direction: "increases", active_command: variable === "light" ? 100 : 0.5, inactive_command: 0, bidirectional: false };
}

function parameterInput(controllerIndex, field, value) {
  const path = `controllers.${controllerIndex}.parameters.${field}`;
  if (field === "direction") {
    return `<label>${fieldLabels[field]}<select data-path="${path}"><option value="increases" ${value === "increases" ? "selected" : ""}>Aumenta il processo</option><option value="decreases" ${value === "decreases" ? "selected" : ""}>Riduce il processo</option></select></label>`;
  }
  if (field === "bidirectional") {
    return `<label>${fieldLabels[field]}<select data-path="${path}" data-value-type="boolean"><option value="false" ${!value ? "selected" : ""}>No</option><option value="true" ${value ? "selected" : ""}>Sì</option></select></label>`;
  }
  return `<label>${fieldLabels[field] || field}<input type="number" step="any" data-path="${path}" value="${value ?? 0}" required></label>`;
}

function renderControllers() {
  document.querySelector("#controller-editor").innerHTML = state.recipe.controllers.map((controller, index) => {
    const meta = variableMeta[controller.variable];
    const nutrient = ["nitrogen", "phosphorus", "potassium"].includes(controller.variable);
    const allowedStrategies = nutrient ? ["Predictive"] : ["Threshold", "PID", "Predictive"];
    const parameters = strategyFields[controller.selected_strategy].map((field) => parameterInput(index, field, controller.parameters[field])).join("");
    const limits = Object.entries(controller.output_limits).map(([field, value]) =>
      `<label>${fieldLabels[field] || field}<input type="number" min="0" step="any" data-path="controllers.${index}.output_limits.${field}" value="${value}" required></label>`
    ).join("");
    return `<details class="controller-card">
      <summary>
        <div class="controller-name"><span>${meta.short}</span><div><strong>${meta.label}</strong><small>${controller.sensor} → ${controller.actuator}</small></div></div>
        <div class="controller-strategy"><strong>${controller.selected_strategy}</strong><small>${nutrient ? "Obbligatoria per il modello N/P/K" : "Strategy selezionata"}</small></div>
        <span aria-hidden="true">⌄</span>
      </summary>
      <div class="controller-body">
        <div class="form-grid three">
          <label>Strategy<select data-controller-strategy="${index}" ${nutrient ? "disabled" : ""}>${allowedStrategies.map((strategy) => `<option ${strategy === controller.selected_strategy ? "selected" : ""}>${strategy}</option>`).join("")}</select></label>
          <label>Unità<input value="${escapeHtml(controller.unit)}" readonly></label>
          <label>Stato<input value="Da validare in simulazione" readonly></label>
        </div>
        <h3>Parametri della Strategy</h3><div class="form-grid parameter-grid">${parameters}</div>
        <h3>Limiti prioritari</h3><div class="form-grid parameter-grid">${limits}</div>
      </div>
    </details>`;
  }).join("");
}

function renderRecipeEditor() {
  if (!state.recipe) return;
  document.querySelector("#recipe-id").value = state.recipe.id;
  document.querySelector("#recipe-id").readOnly = state.persistedVersion !== null;
  document.querySelector("#substrate").value = state.recipe.substrate;
  document.querySelector("#recipe-version").textContent = `Versione ${state.recipe.version}`;
  renderPhases();
  renderControllers();
}

function newRecipe() {
  state.recipe = clone(state.template);
  state.recipe.id = "tomato_recipe";
  state.recipe.version = 1;
  state.recipe.controllers.forEach((controller) => {
    controller.confirmation_state = "PENDING_CONFIRMATION";
    controller.confirmed_recipe_version = 0;
  });
  state.persistedVersion = null;
  renderRecipeEditor();
  updateOverviewRecipe();
  document.querySelector("#recipe-selector").value = "";
  setView("recipe");
}

async function saveRecipe() {
  const form = document.querySelector("#recipe-form");
  if (!form.reportValidity()) return;
  const payload = clone(state.recipe);
  payload.plant_type = "Tomato";
  payload.version = state.persistedVersion === null ? 1 : state.persistedVersion + 1;
  payload.controllers.forEach((controller) => {
    controller.confirmation_state = "PENDING_CONFIRMATION";
    controller.confirmed_recipe_version = 0;
    if (state.persistedVersion !== null) controller.version += 1;
  });
  const button = document.querySelector("#save-recipe");
  button.disabled = true;
  button.textContent = "Salvataggio…";
  try {
    const saved = await request("/recipes", { method: "POST", body: JSON.stringify(payload) });
    state.recipe = saved;
    state.persistedVersion = saved.version;
    await refreshRecipeList(saved.id);
    renderRecipeEditor();
    updateOverviewRecipe();
    toast(`Ricetta ${saved.id} salvata come versione ${saved.version}.`);
  } catch (error) {
    toast(error.status === 409 ? "La ricetta è stata aggiornata altrove. Ricaricala e riprova." : error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Salva ricetta";
  }
}

function readInputValue(input) {
  if (input.dataset.valueType === "boolean") return input.value === "true";
  if (input.type === "number") return Number(input.value);
  return input.value;
}

function renderDecisionSummary(cycle) {
  return Object.entries(cycle.decisions).map(([variable, decision]) => `
    <div class="decision-mini"><span>${variableMeta[variable].label}</span>
    <strong>${decision.status} · ${formatNumber(decision.command, 2)}</strong></div>`).join("");
}

function renderCycle(index) {
  const cycle = state.simulation[index];
  if (!cycle) return;
  state.simulationIndex = index;
  const total = state.simulation.length;
  const timeHours = cycle.start_time_seconds / 3600;
  document.querySelector("#simulation-counter").textContent = `${index + 1} / ${total}`;
  const progress = document.querySelector("#simulation-progress");
  progress.max = total;
  progress.value = index + 1;
  document.querySelector("#simulation-phase").textContent = cycle.phase_name;
  document.querySelector("#simulation-time").textContent = `${formatNumber(timeHours, 2)} h`;
  document.querySelector("#simulation-sensors").innerHTML = metricCards(cycle);
  document.querySelector("#sensor-grid").innerHTML = metricCards(cycle);
  document.querySelector("#simulation-actuators").innerHTML = actuatorMarkup(cycle);
  document.querySelector("#overview-actuators").innerHTML = actuatorMarkup(cycle);
  document.querySelector("#overview-phase").textContent = cycle.phase_name;
  document.querySelector("#overview-cycle").textContent = `Ciclo ${cycle.cycle} · ${formatNumber(timeHours, 2)} h`;
  document.querySelector("#actuator-live-badge").className = "badge ready";
  document.querySelector("#actuator-live-badge").textContent = "Dati Edge";
  document.querySelector("#overview-decisions").className = "decision-summary";
  document.querySelector("#overview-decisions").innerHTML = renderDecisionSummary(cycle);
  document.querySelector("#decision-table").innerHTML = Object.entries(cycle.decisions).map(([variable, decision]) => `
    <tr><td><strong>${variableMeta[variable].label}</strong></td>
    <td><span class="badge ${decision.status.toLowerCase()}">${decision.status}</span></td>
    <td>${formatNumber(decision.command, 3)}</td><td>${escapeHtml(decision.message || "—")}</td></tr>`).join("");
  const first = Math.max(0, index - 9);
  document.querySelector("#history-table").innerHTML = state.simulation.slice(first, index + 1).reverse().map((item) => `
    <tr><td>${item.cycle}</td><td>${escapeHtml(item.phase_name)}</td>
    <td>${formatNumber(item.sensors.soil_moisture_percent)}%</td><td>${formatNumber(item.sensors.ph, 2)}</td>
    <td>${formatNumber(item.sensors.light_ppfd_umol_m2_s, 0)}</td><td>${formatNumber(item.delivered.water_liters, 2)} L</td></tr>`).join("");
  document.querySelector("#simulation-status").className = "inline-status";
  document.querySelector("#simulation-status").textContent = index === total - 1
    ? "Simulazione completata. Puoi riprodurla di nuovo o modificare la ricetta."
    : `Riproduzione del ciclo ${index + 1} di ${total}.`;
}

function pausePlayback() {
  if (state.timer) window.clearInterval(state.timer);
  state.timer = null;
}

function playSimulation() {
  if (!state.simulation.length) {
    toast("Esegui prima una simulazione.", true);
    return;
  }
  pausePlayback();
  if (state.simulationIndex >= state.simulation.length - 1) renderCycle(0);
  state.timer = window.setInterval(() => {
    const next = state.simulationIndex + 1;
    if (next >= state.simulation.length) {
      pausePlayback();
      return;
    }
    renderCycle(next);
  }, 650);
}

async function runSimulation() {
  const recipeId = document.querySelector("#simulation-recipe").value;
  if (!recipeId) {
    toast("Seleziona una ricetta salvata.", true);
    return;
  }
  pausePlayback();
  const button = document.querySelector("#run-simulation");
  const status = document.querySelector("#simulation-status");
  button.disabled = true;
  button.textContent = "Edge in esecuzione…";
  status.className = "inline-status";
  status.textContent = "Il backend sta eseguendo il simulatore C++.";
  try {
    const result = await request("/simulations", {
      method: "POST",
      body: JSON.stringify({
        recipe_id: recipeId,
        steps: Number(document.querySelector("#simulation-steps").value),
        step_seconds: Number(document.querySelector("#simulation-step-seconds").value),
      }),
    });
    state.simulation = result.steps;
    state.simulationIndex = -1;
    renderCycle(0);
    playSimulation();
    toast(`${state.simulation.length} cicli ricevuti dall’Edge.`);
  } catch (error) {
    status.className = "inline-status error";
    status.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Esegui simulazione";
  }
}

function resetSimulation() {
  pausePlayback();
  state.simulationIndex = -1;
  document.querySelector("#simulation-counter").textContent = `0 / ${state.simulation.length}`;
  document.querySelector("#simulation-progress").value = 0;
  document.querySelector("#simulation-phase").textContent = "Nessun dato";
  document.querySelector("#simulation-time").textContent = "—";
  document.querySelector("#decision-table").innerHTML = '<tr><td colspan="4" class="table-empty">Riproduzione azzerata</td></tr>';
  document.querySelector("#history-table").innerHTML = '<tr><td colspan="6" class="table-empty">Lo storico apparirà durante la riproduzione.</td></tr>';
}

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.querySelectorAll("[data-go-to]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.goTo)));
  document.querySelector("#mobile-menu").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
  document.querySelector("#new-recipe").addEventListener("click", newRecipe);
  document.querySelector("#save-recipe").addEventListener("click", saveRecipe);
  document.querySelector("#recipe-selector").addEventListener("change", (event) => loadRecipe(event.target.value));
  document.querySelector("#add-phase").addEventListener("click", () => {
    const phase = clone(state.recipe.phases.at(-1));
    phase.name = `Fase ${state.recipe.phases.length + 1}`;
    state.recipe.phases.push(phase);
    renderPhases();
  });
  document.querySelector("#recipe-form").addEventListener("input", (event) => {
    const path = event.target.dataset.path;
    if (!path || !state.recipe) return;
    setPath(state.recipe, path, readInputValue(event.target));
  });
  document.querySelector("#recipe-form").addEventListener("change", (event) => {
    const path = event.target.dataset.path;
    if (path && state.recipe) setPath(state.recipe, path, readInputValue(event.target));
    const strategyIndex = event.target.dataset.controllerStrategy;
    if (strategyIndex !== undefined) {
      const controller = state.recipe.controllers[Number(strategyIndex)];
      controller.selected_strategy = event.target.value;
      controller.parameters = defaultParameters(event.target.value, controller.variable);
      renderControllers();
    }
  });
  document.querySelector("#phase-editor").addEventListener("click", (event) => {
    const duplicate = event.target.dataset.duplicatePhase;
    const remove = event.target.dataset.removePhase;
    if (duplicate !== undefined) {
      const phase = clone(state.recipe.phases[Number(duplicate)]);
      phase.name = `${phase.name} copia`;
      state.recipe.phases.splice(Number(duplicate) + 1, 0, phase);
      renderPhases();
    }
    if (remove !== undefined && state.recipe.phases.length > 1) {
      state.recipe.phases.splice(Number(remove), 1);
      renderPhases();
    }
  });
  document.querySelector("#run-simulation").addEventListener("click", runSimulation);
  document.querySelector("#play-simulation").addEventListener("click", playSimulation);
  document.querySelector("#pause-simulation").addEventListener("click", pausePlayback);
  document.querySelector("#step-simulation").addEventListener("click", () => {
    pausePlayback();
    if (!state.simulation.length) return toast("Esegui prima una simulazione.", true);
    renderCycle(Math.min(state.simulationIndex + 1, state.simulation.length - 1));
  });
  document.querySelector("#reset-simulation").addEventListener("click", resetSimulation);
}

async function initialize() {
  bindEvents();
  renderEmptyMetrics();
  await refreshSystemStatus();
  try {
    state.template = await request("/recipes/template");
    await refreshRecipeList();
    if (state.recipes.length) {
      await loadRecipe(state.recipes[0].id);
    } else {
      newRecipe();
      setView("overview");
    }
  } catch (error) {
    toast(`Inizializzazione non riuscita: ${error.message}`, true);
  }
  const initialView = window.location.hash.replace("#", "");
  if (["overview", "recipe", "simulation"].includes(initialView)) setView(initialView);
}

initialize();
