#include <smarthydro/control/control_system.hpp>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

void require_finite(double value, const char* name) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument(std::string(name) + " must be finite");
    }
}

bool parameter_type_matches(
    StrategyType strategy,
    const ControllerParameters& parameters) {
    switch (strategy) {
        case StrategyType::THRESHOLD:
            return std::holds_alternative<ThresholdConfig>(parameters);
        case StrategyType::PID:
            return std::holds_alternative<PidConfig>(parameters);
        case StrategyType::PREDICTIVE:
            return std::holds_alternative<PredictiveConfig>(parameters);
    }
    return false;
}

bool is_model_source(ControlInputSource input_source) noexcept {
    return input_source == ControlInputSource::NITROGEN_MODEL ||
           input_source == ControlInputSource::PHOSPHORUS_MODEL ||
           input_source == ControlInputSource::POTASSIUM_MODEL;
}

bool is_nutrient(ControlledVariable variable) noexcept {
    return variable == ControlledVariable::NITROGEN ||
           variable == ControlledVariable::PHOSPHORUS ||
           variable == ControlledVariable::POTASSIUM;
}

bool is_dose_variable(ControlledVariable variable) noexcept {
    return variable == ControlledVariable::PH || is_nutrient(variable);
}

StrategyType required_default_strategy(ControlledVariable variable) {
    // N/P/K leggono da una stima di modello (is_model_source), non da un
    // sensore diretto: Predictive resta il default perche' e' l'unica delle
    // tre Strategy pensata per proiettare un trend nel tempo, che qui serve
    // a dosare in vista della prossima occasione utile (l'irrigazione, non
    // ogni ciclo di controllo — le valvole di concentrato si aprono solo
    // mentre la pompa e' attiva) invece di reagire al solo valore
    // istantaneo. Threshold e PID restano comunque selezionabili (vedi
    // ChangeStrategy) per chi preferisce quel comportamento.
    if (is_nutrient(variable)) {
        return StrategyType::PREDICTIVE;
    }
    if (variable == ControlledVariable::PH) {
        return StrategyType::PID;
    }
    return StrategyType::THRESHOLD;
}

ControlInputSource required_input_source(ControlledVariable variable) {
    switch (variable) {
        case ControlledVariable::SOIL_MOISTURE:
            return ControlInputSource::SOIL_MOISTURE_SENSOR;
        case ControlledVariable::LIGHT:
            return ControlInputSource::LIGHT_SENSOR;
        case ControlledVariable::PH:
            return ControlInputSource::PH_SENSOR;
        case ControlledVariable::NITROGEN:
            return ControlInputSource::NITROGEN_MODEL;
        case ControlledVariable::PHOSPHORUS:
            return ControlInputSource::PHOSPHORUS_MODEL;
        case ControlledVariable::POTASSIUM:
            return ControlInputSource::POTASSIUM_MODEL;
        case ControlledVariable::COUNT:
            break;
    }
    throw std::invalid_argument("unknown controlled variable");
}

ActuatorType required_actuator(ControlledVariable variable) {
    switch (variable) {
        case ControlledVariable::SOIL_MOISTURE:
            return ActuatorType::WATER_PUMP;
        case ControlledVariable::LIGHT:
            return ActuatorType::LIGHTING;
        case ControlledVariable::PH:
            return ActuatorType::PH_CORRECTOR_VALVES;
        case ControlledVariable::NITROGEN:
            return ActuatorType::NITROGEN_VALVE;
        case ControlledVariable::PHOSPHORUS:
            return ActuatorType::PHOSPHORUS_VALVE;
        case ControlledVariable::POTASSIUM:
            return ActuatorType::POTASSIUM_VALVE;
        case ControlledVariable::COUNT:
            break;
    }
    throw std::invalid_argument("unknown controlled variable");
}

double substrate_factor(SoilType soil) {
    switch (soil) {
        case SoilType::AERATED_UNIVERSAL:
            return 1.0;
        case SoilType::DRAINING:
            return 1.15;
        case SoilType::ORGANIC_RETENTIVE:
            return 0.85;
    }
    throw std::invalid_argument("unknown substrate");
}

ControllerParameters parameters_for_phase(
    const ControllerConfiguration& configuration,
    const PhaseVariableTarget& target) {
    switch (configuration.selected_strategy) {
        case StrategyType::THRESHOLD: {
            auto parameters = std::get<ThresholdConfig>(
                configuration.parameters);
            parameters.lower_threshold = target.allowed_range.minimum;
            parameters.upper_threshold = target.allowed_range.maximum;
            parameters.bidirectional =
                target.variable == ControlledVariable::PH;
            return parameters;
        }
        case StrategyType::PID: {
            auto parameters = std::get<PidConfig>(configuration.parameters);
            parameters.setpoint = target.setpoint;
            return parameters;
        }
        case StrategyType::PREDICTIVE: {
            auto parameters = std::get<PredictiveConfig>(
                configuration.parameters);
            parameters.setpoint = target.setpoint;
            return parameters;
        }
    }
    throw std::invalid_argument("unknown strategy");
}

bool hour_in_photoperiod(double hour, const Photoperiod& photoperiod) {
    double relative = std::fmod(hour - photoperiod.start_hour + 24.0, 24.0);
    if (relative < 0.0) {
        relative += 24.0;
    }
    return relative < photoperiod.duration_hours;
}

std::optional<double> source_value(
    ControlInputSource input_source,
    const ControllerInput& input) {
    return is_model_source(input_source)
               ? input.model_estimate
               : input.measured_value;
}

}  // namespace

std::size_t controlled_variable_index(ControlledVariable variable) {
    const auto index = static_cast<std::size_t>(variable);
    if (index >= kControlledVariableCount) {
        throw std::invalid_argument("unknown controlled variable");
    }
    return index;
}

const char* to_string(ControlledVariable variable) noexcept {
    switch (variable) {
        case ControlledVariable::SOIL_MOISTURE:
            return "soil_moisture";
        case ControlledVariable::LIGHT:
            return "light";
        case ControlledVariable::PH:
            return "ph";
        case ControlledVariable::NITROGEN:
            return "nitrogen";
        case ControlledVariable::PHOSPHORUS:
            return "phosphorus";
        case ControlledVariable::POTASSIUM:
            return "potassium";
        case ControlledVariable::COUNT:
            break;
    }
    return "unknown";
}

const char* to_string(ControlInputSource input_source) noexcept {
    switch (input_source) {
        case ControlInputSource::SOIL_MOISTURE_SENSOR:
            return "soil_moisture_sensor";
        case ControlInputSource::LIGHT_SENSOR:
            return "light_sensor";
        case ControlInputSource::PH_SENSOR:
            return "ph_sensor";
        case ControlInputSource::NITROGEN_MODEL:
            return "nitrogen_model";
        case ControlInputSource::PHOSPHORUS_MODEL:
            return "phosphorus_model";
        case ControlInputSource::POTASSIUM_MODEL:
            return "potassium_model";
    }
    return "unknown";
}

const char* to_string(ActuatorType actuator) noexcept {
    switch (actuator) {
        case ActuatorType::WATER_PUMP:
            return "water_pump";
        case ActuatorType::LIGHTING:
            return "lighting";
        case ActuatorType::PH_CORRECTOR_VALVES:
            return "ph_corrector_valves";
        case ActuatorType::NITROGEN_VALVE:
            return "nitrogen_valve";
        case ActuatorType::PHOSPHORUS_VALVE:
            return "phosphorus_valve";
        case ActuatorType::POTASSIUM_VALVE:
            return "potassium_valve";
    }
    return "unknown";
}

const char* to_string(ConfirmationState state) noexcept {
    switch (state) {
        case ConfirmationState::PENDING_CONFIRMATION:
            return "PENDING_CONFIRMATION";
        case ConfirmationState::CONFIRMED:
            return "CONFIRMED";
        case ConfirmationState::REJECTED:
            return "REJECTED";
        case ConfirmationState::INVALID:
            return "INVALID";
    }
    return "INVALID";
}

RecipeControlSystem::RecipeControlSystem(Recipe recipe)
    : recipe_(std::move(recipe)) {
    validate_recipe(recipe_);
    confirm_all_from_recipe();
}

const Recipe& RecipeControlSystem::recipe() const noexcept {
    return recipe_;
}

std::size_t RecipeControlSystem::phase_index(
    double elapsed_recipe_hours) const {
    require_finite(elapsed_recipe_hours, "elapsed recipe hours");
    if (elapsed_recipe_hours < 0.0) {
        throw std::invalid_argument("elapsed recipe hours must not be negative");
    }
    double boundary = 0.0;
    for (std::size_t index = 0; index < recipe_.phases.size(); ++index) {
        boundary += recipe_.phases[index].duration_hours;
        if (elapsed_recipe_hours < boundary) {
            return index;
        }
    }
    return recipe_.phases.size() - 1;
}

const RecipePhase& RecipeControlSystem::active_phase(
    double elapsed_recipe_hours) const {
    return recipe_.phases[phase_index(elapsed_recipe_hours)];
}

ConfirmationResult RecipeControlSystem::confirm_configuration(
    ControlledVariable variable) {
    const auto index = controlled_variable_index(variable);
    auto& configuration = recipe_.controllers[index];
    if (!parameter_type_matches(
            configuration.selected_strategy, configuration.parameters)) {
        configuration.confirmation_state = ConfirmationState::INVALID;
        return {false, "parameters do not match selected strategy"};
    }
    // Threshold, PID e Predictive sono tutte ammesse anche per una sorgente
    // di modello (N/P/K): process_value()/source_value() risolvono il
    // valore da controllare in modo identico per qualunque Strategy, quindi
    // non c'e' nulla di specifico a Predictive che le altre due non possano
    // gestire.
    try {
        for (const auto& phase : recipe_.phases) {
            const auto parameters = parameters_for_phase(
                configuration, phase.targets[index]);
            static_cast<void>(ControllerFactory::create(
                configuration.selected_strategy, parameters));
        }
    } catch (const std::exception& error) {
        configuration.confirmation_state = ConfirmationState::INVALID;
        return {false, error.what()};
    }

    configuration.confirmation_state = ConfirmationState::CONFIRMED;
    configuration.confirmed_recipe_version = recipe_.version;
    return {true, {}};
}

void RecipeControlSystem::reject_configuration(ControlledVariable variable) {
    auto& configuration =
        recipe_.controllers[controlled_variable_index(variable)];
    configuration.confirmation_state = ConfirmationState::REJECTED;
    configuration.confirmed_recipe_version = 0;
}

void RecipeControlSystem::select_strategy(
    ControlledVariable variable,
    StrategyType strategy,
    ControllerParameters parameters) {
    auto& configuration =
        recipe_.controllers[controlled_variable_index(variable)];
    configuration.selected_strategy = strategy;
    configuration.parameters = std::move(parameters);
    ++configuration.version;
    ++recipe_.version;
    invalidate_all_confirmations();
}

void RecipeControlSystem::replace_recipe(Recipe recipe) {
    validate_recipe(recipe);
    if (recipe.version <= recipe_.version) {
        throw std::invalid_argument(
            "replacement recipe version must be greater than current version");
    }
    recipe_ = std::move(recipe);
    confirm_all_from_recipe();
}

bool RecipeControlSystem::all_configurations_confirmed() const noexcept {
    return std::all_of(
        recipe_.controllers.begin(),
        recipe_.controllers.end(),
        [this](const ControllerConfiguration& configuration) {
            return configuration.confirmation_state ==
                       ConfirmationState::CONFIRMED &&
                   configuration.confirmed_recipe_version == recipe_.version;
        });
}

void RecipeControlSystem::invalidate_all_confirmations() noexcept {
    for (auto& configuration : recipe_.controllers) {
        configuration.confirmation_state =
            ConfirmationState::PENDING_CONFIRMATION;
        configuration.confirmed_recipe_version = 0;
    }
    for (auto& controller : controllers_) {
        controller.reset();
    }
    controller_phase_index_ = kControlledVariableCount;
}

void RecipeControlSystem::confirm_all_from_recipe() {
    // Same cache reset invalidate_all_confirmations() performs, so
    // rebuild_controllers() compiles fresh controller instances from the
    // (re)confirmed configuration on the next execute().
    for (auto& controller : controllers_) {
        controller.reset();
    }
    controller_phase_index_ = kControlledVariableCount;

    for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
        const auto variable = static_cast<ControlledVariable>(index);
        const auto result = confirm_configuration(variable);
        if (!result.success) {
            throw std::runtime_error(
                "cannot confirm " + std::string(to_string(variable)) +
                " when adopting recipe " + recipe_.id + ": " +
                result.error);
        }
    }
}

void RecipeControlSystem::rebuild_controllers(std::size_t phase) {
    for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
        const auto& configuration = recipe_.controllers[index];
        try {
            controllers_[index] = ControllerFactory::create(
                configuration.selected_strategy,
                parameters_for_phase(
                    configuration, recipe_.phases[phase].targets[index]));
        } catch (const std::exception&) {
            controllers_[index].reset();
        }
    }
    controller_phase_index_ = phase;
}

ControlDecision RecipeControlSystem::execute(
    ControlledVariable variable,
    const ControlRequest& request) {
    const auto index = controlled_variable_index(variable);
    const auto& configuration = recipe_.controllers[index];
    ControlDecision decision;
    decision.actuator = configuration.actuator;

    if (configuration.confirmation_state != ConfirmationState::CONFIRMED ||
        configuration.confirmed_recipe_version != recipe_.version) {
        decision.message = "configuration is not confirmed";
        return decision;
    }
    if (!request.source_valid) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::RECOVERABLE;
        decision.message = "sensor or model input is invalid";
        return decision;
    }
    if (!std::isfinite(request.elapsed_recipe_hours) ||
        request.elapsed_recipe_hours < 0.0) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "recipe time is invalid";
        return decision;
    }
    if (variable == ControlledVariable::LIGHT &&
        (!std::isfinite(request.hour_of_day) ||
         request.hour_of_day < 0.0 ||
         request.hour_of_day >= 24.0)) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "hour of day is invalid";
        return decision;
    }
    if (is_dose_variable(variable) &&
        (!std::isfinite(request.daily_dose_milliliters) ||
         request.daily_dose_milliliters < 0.0 ||
         !std::isfinite(request.seconds_since_last_dose) ||
         request.seconds_since_last_dose < 0.0)) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "dose history is invalid";
        return decision;
    }
    if (variable == ControlledVariable::LIGHT &&
        (!std::isfinite(request.daily_light_mol_m2_so_far) ||
         request.daily_light_mol_m2_so_far < 0.0)) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "daily light history is invalid";
        return decision;
    }

    const auto phase = phase_index(request.elapsed_recipe_hours);
    if (controller_phase_index_ != phase) {
        rebuild_controllers(phase);
    }
    if (!controllers_[index]) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "controller could not be created";
        return decision;
    }

    const auto& active = recipe_.phases[phase];
    const auto& target = active.targets[index];
    const auto value = source_value(
        configuration.input_source, request.controller_input);
    if (!value.has_value() || !std::isfinite(*value)) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::RECOVERABLE;
        decision.message = "required sensor or model value is missing";
        return decision;
    }
    // Per la luce il target (setpoint/allowed_range/safety_range) e' ormai
    // un DLI giornaliero (mol/m^2/giorno), non piu' un livello PPFD
    // istantaneo: confrontarci una lettura del momento non avrebbe senso
    // (safety_range.maximum sarebbe quasi sempre superato di notte... no,
    // sotto — ma anche di giorno il PPFD istantaneo naturale puo'
    // benissimo superare un numero che rappresenta un totale giornaliero).
    // Il controllo di sicurezza sul valore istantaneo resta invariato per
    // tutte le altre variabili.
    if (variable != ControlledVariable::LIGHT &&
        (*value < target.safety_range.minimum ||
         *value > target.safety_range.maximum)) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = "process value is outside safety limits";
        return decision;
    }

    if (variable == ControlledVariable::LIGHT &&
        !hour_in_photoperiod(request.hour_of_day, active.photoperiod)) {
        decision.status = ControlDecisionStatus::APPLIED;
        decision.message = "outside photoperiod";
        return decision;
    }

    if (variable == ControlledVariable::LIGHT) {
        // Deficit DLI (Daily Light Integral): la lampada supplisce solo se
        // oggi, fra sole e lampada, non e' ancora arrivata alla pianta la
        // quantita' di luce richiesta dalla fase (target.setpoint, in
        // mol/m^2/giorno) — non reagisce alla lettura istantanea del
        // momento. Bypassa deliberatamente ControllerFactory/IController:
        // selected_strategy resta un campo valido e mostrato (coerenza col
        // resto del sistema — vedi control_strategy/ lato backend), ma per
        // la luce il comando e' sempre questo, qualunque Strategy sia
        // selezionata.
        const double remaining = std::max(
            0.0, target.setpoint - request.daily_light_mol_m2_so_far);
        // A RITMO E PROPORZIONALE, non piu' un binario "accesa finche' il
        // deficit non si chiude" scattato al primissimo minuto di
        // fotoperiodo (MVP originale): quella versione accendeva la
        // lampada a piena potenza dall'inizio del fotoperiodo ogni volta
        // che il residuo era > 0, anche con l'intera giornata ancora
        // davanti al sole per colmarlo da solo — osservato empiricamente
        // che il deficit si chiudeva spesso entro meta' mattina, e tutto
        // il sole ricevuto nelle ore RESTANTI del fotoperiodo si sommava
        // comunque sopra (nessun attuatore riduce il sole in eccesso),
        // portando il totale giornaliero anche al doppio del target.
        //
        // Un primo tentativo binario "a ritmo" (accesa al 100% solo se
        // indietro rispetto a un ritmo lineare, spenta appena si e' di
        // nuovo in pari) chiudeva il problema sopra ma ne apriva un altro:
        // con una lampada abbastanza potente da coprire da sola le specie
        // a fabbisogno alto (vedi maximum_lighting_power_watts), bastava
        // una nuvola che si apriva per un attimo a far scattare/rientrare
        // il 100% ad ogni ciclo di controllo — osservato empiricamente sul
        // Pomodorino: 40 accensioni/spegnimenti in un solo giorno, non
        // realistico per una lampada da centinaia di watt vera. Un secondo
        // tentativo con isteresi (resta accesa una finestra minima prima
        // di rivalutare) risolveva lo sfarfallio ma non c'era una singola
        // finestra buona per tutte le specie: abbastanza lunga da non far
        // scattare il pomodoro troppo spesso, chiudeva pero' l'INTERO
        // target giornaliero del Pothos in 1-2 ore, lasciando poi tutte le
        // ore restanti di fotoperiodo libere di sommare sole naturale
        // sopra un target gia' chiuso (media giornaliera Pothos salita da
        // 11.7 a 18.5 mol/m^2/giorno).
        //
        // Qui il comando e' PROPORZIONALE a quanto si e' indietro rispetto
        // al ritmo lineare (pace_threshold sotto), non piu' un interruttore
        // 100%/0%: per un piccolo scostamento la lampada spinge poco (una
        // specie a fabbisogno basso come il Pothos, quasi sempre appena
        // indietro, non riceve mai un getto pieno che chiude da solo tutta
        // la giornata), per un grande scostamento spinge fino al massimo
        // (una specie a fabbisogno alto come il pomodoro, quasi sempre
        // molto indietro perche' il sole da solo non basta, ottiene
        // comunque un comando vicino al 100% quasi tutto il giorno). Niente
        // isteresi a stato: la modulazione continua del comando smorza da
        // sola le oscillazioni cicliche senza bisogno di "ricordare" se
        // era gia' accesa.
        const double elapsed_in_photoperiod = std::max(
            0.0, request.hour_of_day - active.photoperiod.start_hour);
        const double photoperiod_progress =
            active.photoperiod.duration_hours > 0.0
                ? std::clamp(
                      elapsed_in_photoperiod /
                          active.photoperiod.duration_hours,
                      0.0,
                      1.0)
                : 1.0;
        // pace_threshold e' quanto resterebbe da colmare a questo punto
        // della giornata se il DLI si accumulasse a ritmo LINEARE costante
        // dall'inizio alla fine del fotoperiodo. La soglia tende a zero
        // verso la fine del fotoperiodo, quindi qualunque residuo ancora
        // aperto a quel punto spinge comunque il comando verso il massimo
        // in tempo — la garanzia del minimo entro fine giornata resta
        // intatta, cambia solo QUANTO spinge e QUANDO.
        const double pace_threshold =
            target.setpoint * (1.0 - photoperiod_progress);
        const double behind_by = std::max(0.0, remaining - pace_threshold);
        // Scala di riferimento: il comando raggiunge il 100% quando si e'
        // indietro di piu' di un quarto del target dell'intera giornata
        // rispetto al ritmo — una frazione del target invece di un valore
        // assoluto fisso, cosi' la stessa logica si adatta da sola a
        // target di ordini di grandezza diversi (8 vs 58 mol/m^2/giorno)
        // senza bisogno di conoscere la potenza reale della lampada, che
        // il controllo non conosce ne' deve conoscere (bypassa
        // deliberatamente ControllerFactory/IController, vedi sopra).
        const double scale = target.setpoint * 0.25;
        decision.command = scale > 0.0
            ? std::clamp(100.0 * behind_by / scale, 0.0, 100.0)
            : (behind_by > 0.0 ? 100.0 : 0.0);
        decision.status = ControlDecisionStatus::APPLIED;
        return decision;
    }

    auto controller_input = request.controller_input;
    if (is_model_source(configuration.input_source)) {
        controller_input.measured_value.reset();
    } else {
        controller_input.model_estimate.reset();
    }
    controller_input.phase_target_dose_milliliters =
        target.suggested_phase_dose_milliliters;
    controller_input.substrate_factor =
        substrate_factor(*recipe_.substrate);
    const auto raw = controllers_[index]->compute(controller_input);
    if (!raw.valid) {
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::CRITICAL;
        decision.message = raw.error;
        return decision;
    }
    decision.command = raw.command;
    decision.predicted_value = raw.predicted_value;
    decision.status = ControlDecisionStatus::APPLIED;

    const auto& limits = configuration.output_limits;
    if (variable == ControlledVariable::SOIL_MOISTURE) {
        const double duration_limited_volume =
            limits.water_pump_flow_liters_per_hour *
            limits.maximum_pump_duration_seconds / 3600.0;
        const double maximum_volume = std::min(
            limits.maximum_water_volume_liters,
            duration_limited_volume);
        const double limited = std::clamp(decision.command, 0.0, maximum_volume);
        if (limited != decision.command) {
            decision.command = limited;
            decision.status = ControlDecisionStatus::LIMITED;
            decision.message = "water command limited by volume or pump duration";
        }
    } else if (is_dose_variable(variable) &&
               std::abs(decision.command) > 0.0) {
        double required_interval = limits.minimum_seconds_between_doses;
        if (variable == ControlledVariable::PH) {
            required_interval = std::max(
                required_interval, limits.ph_settling_time_seconds);
        }
        if (request.seconds_since_last_dose < required_interval) {
            decision.command = 0.0;
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.message = variable == ControlledVariable::PH
                                   ? "pH settling time is still active"
                                   : "minimum interval between doses is active";
            return decision;
        }
        if (variable == ControlledVariable::PH &&
            ((decision.command > 0.0 && request.ph_down_active) ||
             (decision.command < 0.0 && request.ph_up_active))) {
            decision.command = 0.0;
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.message = "opposite pH valve is already active";
            return decision;
        }

        const double remaining_daily = std::max(
            0.0,
            limits.maximum_daily_dose_milliliters -
                request.daily_dose_milliliters);
        const double maximum_dose = std::min(
            limits.maximum_dose_per_command_milliliters,
            remaining_daily);
        if (maximum_dose <= 0.0) {
            decision.command = 0.0;
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.message = "daily dose limit reached";
            return decision;
        }
        const double limited_magnitude = std::min(
            std::abs(decision.command), maximum_dose);
        const double limited = std::copysign(
            limited_magnitude, decision.command);
        if (limited != decision.command) {
            decision.command = limited;
            decision.status = ControlDecisionStatus::LIMITED;
            decision.message = "dose limited by command or daily maximum";
        }
    }

    return decision;
}

void RecipeControlSystem::validate_recipe(const Recipe& recipe) {
    if (recipe.id.empty() || recipe.plant_type.empty()) {
        throw std::invalid_argument("recipe id and plant type must not be empty");
    }
    if (recipe.version == 0 || recipe.phases.empty()) {
        throw std::invalid_argument(
            "recipe version must be positive and phases must not be empty");
    }
    if (!recipe.substrate.has_value()) {
        throw std::invalid_argument(
            "recipe substrate must be explicitly specified");
    }
    static_cast<void>(substrate_factor(*recipe.substrate));

    for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
        const auto variable = static_cast<ControlledVariable>(index);
        const auto& configuration = recipe.controllers[index];
        if (configuration.variable != variable ||
            configuration.input_source != required_input_source(variable) ||
            configuration.actuator != required_actuator(variable)) {
            throw std::invalid_argument(
                "controller associations must match controlled variable");
        }
        if (configuration.default_strategy !=
            required_default_strategy(variable)) {
            throw std::invalid_argument(
                "recipe does not use the required default strategy");
        }
        if (!parameter_type_matches(
                configuration.selected_strategy, configuration.parameters)) {
            throw std::invalid_argument(
                "selected strategy and parameters do not match");
        }
        if (configuration.unit.empty() || configuration.version == 0) {
            throw std::invalid_argument(
                "controller unit and version must be defined");
        }
        const auto& limits = configuration.output_limits;
        for (const double value : {
                 limits.maximum_water_volume_liters,
                 limits.maximum_pump_duration_seconds,
                 limits.water_pump_flow_liters_per_hour,
                 limits.maximum_dose_per_command_milliliters,
                 limits.maximum_daily_dose_milliliters,
                 limits.minimum_seconds_between_doses,
                 limits.ph_settling_time_seconds}) {
            require_finite(value, "output safety limit");
            if (value < 0.0) {
                throw std::invalid_argument(
                    "output safety limits must not be negative");
            }
        }
    }

    for (const auto& phase : recipe.phases) {
        if (phase.name.empty()) {
            throw std::invalid_argument("phase name must not be empty");
        }
        require_finite(phase.duration_hours, "phase duration");
        require_finite(phase.photoperiod.start_hour, "photoperiod start");
        require_finite(phase.photoperiod.duration_hours, "photoperiod duration");
        if (phase.duration_hours <= 0.0 ||
            phase.photoperiod.start_hour < 0.0 ||
            phase.photoperiod.start_hour >= 24.0 ||
            phase.photoperiod.duration_hours <= 0.0 ||
            phase.photoperiod.duration_hours > 24.0) {
            throw std::invalid_argument(
                "phase duration or photoperiod is invalid");
        }
        for (std::size_t index = 0; index < kControlledVariableCount; ++index) {
            const auto variable = static_cast<ControlledVariable>(index);
            const auto& target = phase.targets[index];
            if (target.variable != variable) {
                throw std::invalid_argument(
                    "phase targets must match their variable index");
            }
            require_finite(target.setpoint, "target setpoint");
            require_finite(target.allowed_range.minimum, "allowed minimum");
            require_finite(target.allowed_range.maximum, "allowed maximum");
            require_finite(target.safety_range.minimum, "safety minimum");
            require_finite(target.safety_range.maximum, "safety maximum");
            require_finite(
                target.suggested_phase_dose_milliliters,
                "suggested phase dose");
            if (target.safety_range.minimum > target.allowed_range.minimum ||
                target.safety_range.minimum >=
                    target.safety_range.maximum ||
                target.allowed_range.minimum >=
                    target.allowed_range.maximum ||
                target.allowed_range.minimum > target.setpoint ||
                target.setpoint > target.allowed_range.maximum ||
                target.allowed_range.maximum > target.safety_range.maximum ||
                target.suggested_phase_dose_milliliters < 0.0) {
                throw std::invalid_argument(
                    "target ranges, setpoint or dose are inconsistent");
            }
        }
    }
}

}  // namespace smarthydro
