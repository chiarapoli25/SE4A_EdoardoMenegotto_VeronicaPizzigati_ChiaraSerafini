#include <smarthydro/runtime/edge_runtime.hpp>

#include <smarthydro/simulation/sensor_simulator.hpp>

#include <algorithm>
#include <array>
#include <functional>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

constexpr ControlledValues<ControlledVariable> kControlledVariables{{
    ControlledVariable::SOIL_MOISTURE,
    ControlledVariable::LIGHT,
    ControlledVariable::PH,
    ControlledVariable::NITROGEN,
    ControlledVariable::PHOSPHORUS,
    ControlledVariable::POTASSIUM,
}};

/**
 * @brief Applica il substrato della ricetta e avvicina lo stato iniziale
 * dell'ambiente al setpoint della prima fase, senza farlo coincidere.
 *
 * @details Il terriccio e preparato a mano prima dell'avvio (substrati
 * diversi, dosaggio manuale impreciso...): partire esattamente al setpoint
 * sarebbe irrealistico quanto ignorarlo del tutto. Si campiona quindi un
 * punto uniforme nell'allowed_range della prima fase per ciascuna variabile
 * "a riserva" (pH, N, P, K) — vicino al target ma non identico — con un
 * generatore seedato separatamente da quello dell'ambiente (stesso
 * environment_seed, offset diverso) cosi' il punto di partenza resta
 * riproducibile a parita di ricetta e seed, come il resto della
 * simulazione.
 *
 * Un valore gia' esplicitamente diverso dal default di EnvironmentConfig
 * (impostato a mano dal chiamante, tipico nei test) non viene toccato: solo
 * il fallback implicito usato dal runtime reale e dal simulatore batch
 * beneficia di questo aggancio alla ricetta.
 */
EnvironmentConfig environment_for_recipe(
    EnvironmentConfig config,
    const Recipe& recipe,
    std::uint32_t environment_seed) {
    if (!recipe.substrate.has_value()) {
        throw std::invalid_argument("runtime recipe has no substrate");
    }
    config.soil_type = *recipe.substrate;

    if (!recipe.phases.empty()) {
        const EnvironmentConfig defaults{};
        const auto& targets = recipe.phases.front().targets;
        // Mescola l'id ricetta nel seed, non solo environment_seed: senza
        // questo, uniform_real_distribution restituisce alla PRIMA
        // estrazione (sempre la stessa posizione nella sequenza, dato che
        // environment_seed e' fisso per ogni simulazione batch a settore
        // singolo — vedi simulation_main.cpp) sempre la STESSA frazione
        // relativa (~0.4-0.5) del suo intervallo, qualunque esso sia:
        // il valore campionato finiva quindi sempre vicino al CENTRO
        // dell'intervallo — vicino al setpoint anche allargando la
        // finestra di campionamento sotto (segnalato dall'utente: la
        // partenza sembrava "troppo corretta" per non lasciar apprezzare
        // il controllore all'opera). Ricette diverse ora estraggono numeri
        // diversi; la stessa ricetta resta comunque riproducibile a parita'
        // di seed/id, come tutto il resto di questa simulazione.
        std::mt19937 initial_state_rng(
            environment_seed ^ 0x494E4954U ^  // "INIT"
            static_cast<std::uint32_t>(std::hash<std::string>{}(recipe.id)));
        auto near_setpoint = [&](ControlledVariable variable,
                                  double current,
                                  double default_value) {
            if (current != default_value) {
                return current;  // override esplicito del chiamante: intatto.
            }
            const auto& range =
                targets[controlled_variable_index(variable)].allowed_range;
            if (!(range.minimum < range.maximum)) {
                return current;  // fase senza banda utile per questa variabile.
            }
            return std::uniform_real_distribution<double>(
                range.minimum, range.maximum)(initial_state_rng);
        };
        // Come pH/N/P/K sotto, ma con una finestra di campionamento PIU'
        // AMPIA della sola banda target: senza questo, l'umidita' iniziale
        // userebbe il fallback fisico per substrato di soil_dynamics()
        // ("terriccio appena innaffiato", tipicamente ben SOPRA la banda
        // della prima fase) invece di un punto vicino al setpoint — e
        // mentre il terriccio si asciuga verso il proprio equilibrio nei
        // primi giorni, la stessa massa iniziale di N/P/K si ritroverebbe
        // concentrata in sempre meno acqua, producendo un picco di
        // concentrazione che non ha nulla a che fare col dosaggio reale
        // (osservato empiricamente: umidita' 82%->54% e azoto 57->77 mg/L
        // in perfetta correlazione inversa, comando del dosatore quasi
        // sempre zero durante la salita).
        //
        // A differenza di near_setpoint() sotto (che campiona ESATTAMENTE
        // dentro [minimum, maximum], quindi parte SEMPRE gia' in banda),
        // qui la finestra e' allargata di meta' della sua ampiezza per
        // lato: il punto di partenza cade spesso ma non sempre fuori dalla
        // banda — un vero scostamento dal setpoint da correggere, non un
        // avvio gia' comodo che non lascia vedere il controllore all'opera
        // (segnalato dall'utente: "la partenza in condizioni iniziali gia'
        // cosi' tanto corrette... non e' realistico"). Ristretto alla sola
        // umidita': e' la variabile per cui e' stato segnalato, e le altre
        // quattro (pH/N/P/K) partono comunque vicine al loro setpoint
        // proprio in funzione di dove parte l'umidita' (vedi il commento
        // sopra sulla concentrazione), quindi ereditano gia' una loro
        // variabilita' realistica senza bisogno di allargare anche la loro
        // finestra.
        if (config.initial_soil_moisture_percent ==
            defaults.initial_soil_moisture_percent) {
            const auto& range = recipe.phases.front().targets[
                controlled_variable_index(ControlledVariable::SOIL_MOISTURE)]
                    .allowed_range;
            if (range.minimum < range.maximum) {
                const double half_width = (range.maximum - range.minimum) / 2.0;
                config.initial_soil_moisture_percent = std::clamp(
                    std::uniform_real_distribution<double>(
                        range.minimum - half_width,
                        range.maximum + half_width)(initial_state_rng),
                    0.0,
                    100.0);
            }
        }
        config.initial_ph = near_setpoint(
            ControlledVariable::PH, config.initial_ph, defaults.initial_ph);
        config.initial_nitrogen_mg_per_liter = near_setpoint(
            ControlledVariable::NITROGEN,
            config.initial_nitrogen_mg_per_liter,
            defaults.initial_nitrogen_mg_per_liter);
        config.initial_phosphorus_mg_per_liter = near_setpoint(
            ControlledVariable::PHOSPHORUS,
            config.initial_phosphorus_mg_per_liter,
            defaults.initial_phosphorus_mg_per_liter);
        config.initial_potassium_mg_per_liter = near_setpoint(
            ControlledVariable::POTASSIUM,
            config.initial_potassium_mg_per_liter,
            defaults.initial_potassium_mg_per_liter);

        // initial_ec_ms_cm resta un default fisso (1.8) tarato sul vecchio
        // totale N+P+K fisso di 400 mg/L (150+50+200): sostituendo quel
        // totale con lo starting point specifico della ricetta sopra, senza
        // aggiornare anche l'EC iniziale, la sonda resistiva della zona
        // partirebbe da un EC che non corrisponde alla composizione reale
        // dell'ambiente. update_soil_probe_estimates() (sensor_simulator.cpp)
        // inverte proprio questa stessa relazione per stimare N/P/K dalla EC
        // misurata: usare qui gli stessi coefficienti la mantiene coerente,
        // cosi' la stima iniziale del sensore parte vicina al vero valore
        // simulato invece che sistematicamente troppo alta o troppo bassa.
        if (config.initial_ec_ms_cm == defaults.initial_ec_ms_cm) {
            const SoilProbeModelConfig probe{};
            const double initial_total_fertilizer =
                config.initial_nitrogen_mg_per_liter +
                config.initial_phosphorus_mg_per_liter +
                config.initial_potassium_mg_per_liter;
            config.initial_ec_ms_cm = probe.background_ec_ms_cm +
                initial_total_fertilizer /
                    probe.fertilizer_mg_per_liter_per_ms_cm;
        }
    }
    return config;
}

std::unique_ptr<IActuator> require_actuators(
    std::unique_ptr<IActuator> actuators) {
    if (!actuators) {
        throw std::invalid_argument(
            "edge runtime requires an actuator adapter");
    }
    return actuators;
}

std::unique_ptr<IEnvironment> require_environment(
    std::unique_ptr<IEnvironment> environment) {
    if (!environment) {
        throw std::invalid_argument(
            "edge runtime requires an environment adapter");
    }
    return environment;
}

OperationalStatePolicy require_valid_state_policy(
    OperationalStatePolicy policy) {
    if (policy.recoverable_faults_before_lockdown == 0 ||
        policy.healthy_steps_before_nominal == 0) {
        throw std::invalid_argument(
            "operational state policy thresholds must be positive");
    }
    return policy;
}

}  // namespace

const char* to_string(OperationalState state) noexcept {
    switch (state) {
        case OperationalState::NOMINAL:
            return "Nominal";
        case OperationalState::DEGRADED:
            return "Degraded";
        case OperationalState::EMERGENCY_LOCKDOWN:
            return "EmergencyLockdown";
    }
    return "Unknown";
}

const char* to_string(FaultTargetKind kind) noexcept {
    switch (kind) {
        case FaultTargetKind::SENSOR:
            return "sensor";
        case FaultTargetKind::ACTUATOR:
            return "actuator";
    }
    return "unknown";
}

const char* to_string(FaultMode mode) noexcept {
    switch (mode) {
        case FaultMode::SENSOR_DROPOUT:
            return "sensor_dropout";
        case FaultMode::SENSOR_STUCK:
            return "sensor_stuck";
        case FaultMode::SENSOR_OFFSET:
            return "sensor_offset";
        case FaultMode::ACTUATOR_STUCK_OFF:
            return "actuator_stuck_off";
        case FaultMode::ACTUATOR_STUCK_ON:
            return "actuator_stuck_on";
        case FaultMode::ACTUATOR_SLOW_RESPONSE:
            return "actuator_slow_response";
    }
    return "unknown";
}

const char* to_string(EdgeEventType type) noexcept {
    switch (type) {
        case EdgeEventType::RUNTIME_STARTED:
            return "RuntimeStarted";
        case EdgeEventType::RECIPE_PHASE_CHANGED:
            return "RecipePhaseChanged";
        case EdgeEventType::RECIPE_COMPLETED:
            return "RecipeCompleted";
        case EdgeEventType::OPERATIONAL_STATE_CHANGED:
            return "OperationalStateChanged";
        case EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED:
            return "EmergencyLockdownEntered";
    }
    return "Unknown";
}

EdgeRuntime::EdgeRuntime(
    Recipe recipe,
    ActuatorConfig actuator_config,
    EnvironmentConfig environment_config,
    SensorConfig sensor_config,
    std::uint32_t environment_seed,
    std::uint32_t sensor_seed,
    OperationalStatePolicy state_policy,
    FaultDetectorConfig detector_config)
    : control_system_(std::move(recipe)),
      actuators_(std::make_unique<ActuatorSimulatorAdapter>(
          std::move(actuator_config))),
      environment_(std::make_unique<EnvironmentSimulatorAdapter>(
          environment_for_recipe(
              std::move(environment_config),
              control_system_.recipe(),
              environment_seed),
          environment_seed)),
      soil_probe_model_(sensor_config.soil_probe_model),
      sensors_(make_simulated_sensor_adapters(
          std::move(sensor_config),
          sensor_seed)),
      water_pump_(*actuators_),
      lighting_(*actuators_),
      fertilizer_valves_(*actuators_),
      state_policy_(require_valid_state_policy(state_policy)),
      fault_detector_(std::move(detector_config)) {
    recipe_start_time_seconds_ =
        environment_->state().simulation_time_seconds;
    active_substrate_ = *control_system_.recipe().substrate;
    seconds_since_last_dose_.fill(
        std::numeric_limits<double>::max() / 4.0);
}

EdgeRuntime::EdgeRuntime(
    Recipe recipe,
    SensorAdapterArray sensors,
    std::unique_ptr<IActuator> actuators,
    std::unique_ptr<IEnvironment> environment,
    OperationalStatePolicy state_policy,
    SoilProbeModelConfig soil_probe_model,
    FaultDetectorConfig detector_config)
    : control_system_(std::move(recipe)),
      actuators_(require_actuators(std::move(actuators))),
      environment_(require_environment(std::move(environment))),
      soil_probe_model_(std::move(soil_probe_model)),
      sensors_(std::move(sensors)),
      water_pump_(*actuators_),
      lighting_(*actuators_),
      fertilizer_valves_(*actuators_),
      state_policy_(require_valid_state_policy(state_policy)),
      fault_detector_(std::move(detector_config)) {
    for (std::size_t index = 0;
         index < kSensorChannelCount;
         ++index) {
        if (!sensors_[index]) {
            throw std::invalid_argument(
                "edge runtime requires every sensor adapter");
        }
        if (sensor_channel_index(sensors_[index]->channel()) != index) {
            throw std::invalid_argument(
                "sensor adapter is stored in the wrong channel");
        }
    }
    recipe_start_time_seconds_ =
        environment_->state().simulation_time_seconds;
    active_substrate_ = *control_system_.recipe().substrate;
    seconds_since_last_dose_.fill(
        std::numeric_limits<double>::max() / 4.0);
}

void EdgeRuntime::confirm_all_configurations() {
    for (const auto variable : kControlledVariables) {
        const auto confirmation =
            control_system_.confirm_configuration(variable);
        if (!confirmation.success) {
            throw std::runtime_error(
                "cannot confirm " + std::string(to_string(variable)) +
                ": " + confirmation.error);
        }
    }
    if (!control_system_.all_configurations_confirmed()) {
        throw std::runtime_error(
            "recipe configurations are not all confirmed");
    }
}

const RecipeControlSystem& EdgeRuntime::control_system() const noexcept {
    return control_system_;
}

const EnvironmentState& EdgeRuntime::environment_state() const noexcept {
    return environment_->state();
}

const ActuatorOutput& EdgeRuntime::actuator_output() const noexcept {
    return effective_actuator_output_;
}

OperationalState EdgeRuntime::operational_state() const noexcept {
    return operational_state_;
}

double EdgeRuntime::cumulative_phase_dose_milliliters(
    ControlledVariable variable) const {
    return cumulative_phase_dose_milliliters_[
        controlled_variable_index(variable)];
}

double EdgeRuntime::daily_dose_milliliters(
    ControlledVariable variable) const {
    return daily_dose_milliliters_[controlled_variable_index(variable)];
}

}  // namespace smarthydro
