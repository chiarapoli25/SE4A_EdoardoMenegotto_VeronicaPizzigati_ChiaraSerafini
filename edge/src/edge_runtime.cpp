#include "smarthydro/edge_runtime.hpp"
#include "smarthydro/event_bus.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;
constexpr double kSecondsPerDay = 24.0 * kSecondsPerHour;
constexpr double kEventToleranceSeconds = 1.0e-9;
constexpr double kDeliveredTolerance = 1.0e-12;

constexpr ControlledValues<ControlledVariable> kControlledVariables{{
    ControlledVariable::SOIL_MOISTURE,
    ControlledVariable::LIGHT,
    ControlledVariable::PH,
    ControlledVariable::NITROGEN,
    ControlledVariable::PHOSPHORUS,
    ControlledVariable::POTASSIUM,
}};

EnvironmentConfig environment_for_recipe(
    EnvironmentConfig config,
    const Recipe& recipe) {
    if (!recipe.substrate.has_value()) {
        throw std::invalid_argument("runtime recipe has no substrate");
    }
    config.soil_type = *recipe.substrate;
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

double hour_of_day(double elapsed_hours) {
    double hour = std::fmod(elapsed_hours, 24.0);
    if (hour < 0.0) {
        hour += 24.0;
    }
    return hour;
}

double nutrient_model_value(
    ControlledVariable variable,
    const EnvironmentState& state) {
    switch (variable) {
        case ControlledVariable::NITROGEN:
            return state.nitrogen_mg_per_liter;
        case ControlledVariable::PHOSPHORUS:
            return state.phosphorus_mg_per_liter;
        case ControlledVariable::POTASSIUM:
            return state.potassium_mg_per_liter;
        default:
            break;
    }
    throw std::invalid_argument("variable has no nutrient model");
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

const char* to_string(EdgeEventType type) noexcept {
    switch (type) {
        case EdgeEventType::RUNTIME_STARTED:
            return "RuntimeStarted";
        case EdgeEventType::RECIPE_PHASE_CHANGED:
            return "RecipePhaseChanged";
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
    OperationalStatePolicy state_policy)
    : control_system_(std::move(recipe)),
      actuators_(std::make_unique<ActuatorSimulatorAdapter>(
          std::move(actuator_config))),
      environment_(std::make_unique<EnvironmentSimulatorAdapter>(
          environment_for_recipe(
              std::move(environment_config),
              control_system_.recipe()),
          environment_seed)),
      sensors_(make_simulated_sensor_adapters(
          std::move(sensor_config),
          sensor_seed)),
      water_pump_(*actuators_),
      lighting_(*actuators_),
      fertilizer_valves_(*actuators_),
      state_policy_(require_valid_state_policy(state_policy)) {
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
    OperationalStatePolicy state_policy)
    : control_system_(std::move(recipe)),
      actuators_(require_actuators(std::move(actuators))),
      environment_(require_environment(std::move(environment))),
      sensors_(std::move(sensors)),
      water_pump_(*actuators_),
      lighting_(*actuators_),
      fertilizer_valves_(*actuators_),
      state_policy_(require_valid_state_policy(state_policy)) {
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
    return actuators_->output();
}

OperationalState EdgeRuntime::operational_state() const noexcept {
    return operational_state_;
}

double EdgeRuntime::elapsed_recipe_hours() const noexcept {
    return elapsed_recipe_seconds() / kSecondsPerHour;
}

const std::string& EdgeRuntime::active_phase_name() const {
    return control_system_.recipe()
        .phases[active_phase_index(elapsed_recipe_hours())]
        .name;
}

void EdgeRuntime::change_strategy(
    ControlledVariable variable,
    StrategyType strategy,
    ControllerParameters parameters) {
    const auto index = controlled_variable_index(variable);
    const auto previous_strategy =
        control_system_.recipe().controllers[index].selected_strategy;
    control_system_.select_strategy(
        variable, strategy, std::move(parameters));
    if (event_bus_) {
        event_bus_->publish(
            StrategyChanged{
                zone_id_,
                environment_->state().simulation_time_seconds,
                variable,
                previous_strategy,
                strategy,
            });
    }
}

void EdgeRuntime::replace_recipe(Recipe recipe) {
    RecipeControlSystem::validate_recipe(recipe);
    if (*recipe.substrate != active_substrate_) {
        throw std::invalid_argument(
            "replacement recipe substrate differs from the physical zone");
    }
    actuators_->stop_all();
    control_system_.replace_recipe(std::move(recipe));
    recipe_start_time_seconds_ =
        environment_->state().simulation_time_seconds;
    recipe_time_offset_seconds_ = 0.0;
    reported_phase_index_.reset();
    history_phase_index_ = kControlledVariableCount;
    cumulative_phase_dose_milliliters_.fill(0.0);
    seconds_since_last_dose_.fill(
        std::numeric_limits<double>::max() / 4.0);
}

ConfirmationResult EdgeRuntime::confirm_configuration(
    ControlledVariable variable) {
    return control_system_.confirm_configuration(variable);
}

void EdgeRuntime::reject_configuration(ControlledVariable variable) {
    control_system_.reject_configuration(variable);
}

bool EdgeRuntime::advance_recipe_phase() {
    const auto current_index =
        active_phase_index(elapsed_recipe_hours());
    const auto& phases = control_system_.recipe().phases;
    if (current_index + 1 >= phases.size()) {
        return false;
    }

    double next_phase_start_hours = 0.0;
    for (std::size_t index = 0; index <= current_index; ++index) {
        next_phase_start_hours += phases[index].duration_hours;
    }
    const double unshifted_recipe_seconds =
        environment_->state().simulation_time_seconds -
        recipe_start_time_seconds_;
    recipe_time_offset_seconds_ =
        next_phase_start_hours * kSecondsPerHour -
        unshifted_recipe_seconds;

    const auto& previous_phase = phases[current_index].name;
    const auto& current_phase = phases[current_index + 1].name;
    history_phase_index_ = current_index + 1;
    cumulative_phase_dose_milliliters_.fill(0.0);
    reported_phase_index_ = current_index + 1;
    if (event_bus_) {
        event_bus_->publish(
            RecipePhaseChanged{
                zone_id_,
                environment_->state().simulation_time_seconds,
                previous_phase,
                current_phase,
            });
    }
    return true;
}

void EdgeRuntime::inject_fault(
    std::string fault_id,
    ControlFaultSeverity severity,
    std::string diagnostic) {
    if (fault_id.empty() || diagnostic.empty()) {
        throw std::invalid_argument(
            "injected fault requires identifier and diagnostic");
    }
    if (severity != ControlFaultSeverity::RECOVERABLE &&
        severity != ControlFaultSeverity::CRITICAL) {
        throw std::invalid_argument(
            "injected fault severity must be Recoverable or Critical");
    }
    const auto [iterator, inserted] = injected_faults_.emplace(
        fault_id,
        InjectedFault{severity, diagnostic});
    if (!inserted) {
        throw std::invalid_argument(
            "injected fault identifier is already active");
    }
    if (event_bus_) {
        event_bus_->publish(
            FaultDetected{
                zone_id_,
                environment_->state().simulation_time_seconds,
                iterator->first,
                iterator->second.severity,
                iterator->second.diagnostic,
            });
    }
}

bool EdgeRuntime::reset_injected_fault(
    const std::string& fault_id) noexcept {
    return !fault_id.empty() && injected_faults_.erase(fault_id) != 0;
}

bool EdgeRuntime::has_injected_fault(
    const std::string& fault_id) const noexcept {
    return injected_faults_.find(fault_id) != injected_faults_.end();
}

bool EdgeRuntime::trigger_emergency_stop(const std::string& reason) {
    if (reason.empty()) {
        throw std::invalid_argument(
            "emergency stop reason must not be empty");
    }
    actuators_->stop_all();
    if (operational_state_ == OperationalState::EMERGENCY_LOCKDOWN) {
        return false;
    }
    EdgeStepResult transition;
    transition.start_time_seconds =
        environment_->state().simulation_time_seconds;
    transition.operational_state = operational_state_;
    transition_operational_state(
        OperationalState::EMERGENCY_LOCKDOWN,
        reason,
        transition);
    return true;
}

bool EdgeRuntime::request_manual_reset() noexcept {
    if (operational_state_ != OperationalState::EMERGENCY_LOCKDOWN) {
        return false;
    }
    manual_reset_requested_ = true;
    return true;
}

void EdgeRuntime::attach_event_bus(
    std::shared_ptr<EventBus> event_bus,
    std::string zone_id) {
    if (!event_bus) {
        throw std::invalid_argument(
            "edge runtime requires a non-null event bus");
    }
    if (zone_id.empty()) {
        throw std::invalid_argument(
            "edge runtime zone identifier must not be empty");
    }
    event_bus_ = std::move(event_bus);
    zone_id_ = std::move(zone_id);
}

void EdgeRuntime::detach_event_bus() noexcept {
    event_bus_.reset();
}

const std::string& EdgeRuntime::zone_id() const noexcept {
    return zone_id_;
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

ControlRequest EdgeRuntime::base_request(double delta_time_seconds) const {
    const double elapsed_seconds =
        environment_->state().simulation_time_seconds;
    const double elapsed_hours = elapsed_recipe_hours();

    ControlRequest request;
    request.controller_input.delta_time_seconds = delta_time_seconds;
    request.elapsed_recipe_hours = elapsed_hours;
    request.simulated_time_seconds = elapsed_seconds;
    request.hour_of_day =
        hour_of_day(elapsed_seconds / kSecondsPerHour);
    return request;
}

double EdgeRuntime::elapsed_recipe_seconds() const noexcept {
    return std::max(
        0.0,
        environment_->state().simulation_time_seconds -
            recipe_start_time_seconds_ +
            recipe_time_offset_seconds_);
}

std::size_t EdgeRuntime::active_phase_index(
    double elapsed_recipe_hours) const {
    const auto& phases = control_system_.recipe().phases;
    double phase_end = 0.0;
    for (std::size_t index = 0; index < phases.size(); ++index) {
        phase_end += phases[index].duration_hours;
        if (elapsed_recipe_hours < phase_end) {
            return index;
        }
    }
    return phases.size() - 1;
}

void EdgeRuntime::reset_histories_if_needed() {
    const double elapsed_seconds =
        environment_->state().simulation_time_seconds;
    const auto current_day = static_cast<std::uint64_t>(
        std::floor(elapsed_seconds / kSecondsPerDay));
    if (current_day != history_day_index_) {
        daily_dose_milliliters_.fill(0.0);
        history_day_index_ = current_day;
    }

    const auto phase = active_phase_index(elapsed_recipe_hours());
    if (phase != history_phase_index_) {
        cumulative_phase_dose_milliliters_.fill(0.0);
        history_phase_index_ = phase;
    }
}

SensorReadings EdgeRuntime::read_sensors() {
    const auto& state = environment_->state();
    SensorReadings readings;
    readings.timestamp_seconds = state.simulation_time_seconds;
    readings.temperature_c =
        sensors_[sensor_channel_index(SensorChannel::TEMPERATURE)]
            ->read(state);
    readings.air_humidity_percent =
        sensors_[sensor_channel_index(SensorChannel::AIR_HUMIDITY)]
            ->read(state);
    readings.soil_moisture_percent =
        sensors_[sensor_channel_index(SensorChannel::SOIL_MOISTURE)]
            ->read(state);
    readings.ph =
        sensors_[sensor_channel_index(SensorChannel::PH)]
            ->read(state);
    readings.light_ppfd_umol_m2_s =
        sensors_[sensor_channel_index(SensorChannel::LIGHT)]
            ->read(state);
    return readings;
}

EdgeStepResult EdgeRuntime::step(double delta_time_seconds) {
    if (!std::isfinite(delta_time_seconds) ||
        delta_time_seconds <= 0.0) {
        throw std::invalid_argument(
            "edge runtime step duration must be positive and finite");
    }

    reset_histories_if_needed();

    EdgeStepResult result;
    result.sequence_number = next_sequence_number_++;
    result.start_time_seconds =
        environment_->state().simulation_time_seconds;
    result.duration_seconds = delta_time_seconds;
    result.operational_state = operational_state_;
    const auto phase_index = active_phase_index(elapsed_recipe_hours());
    result.phase_name =
        control_system_.recipe().phases[phase_index].name;
    if (!reported_phase_index_.has_value()) {
        result.events.push_back(
            {
                EdgeEventType::RUNTIME_STARTED,
                result.start_time_seconds,
                "runtime started in phase " + result.phase_name,
            });
    } else if (phase_index != *reported_phase_index_) {
        const auto previous_phase_name =
            control_system_.recipe()
                .phases[*reported_phase_index_]
                .name;
        result.events.push_back(
            {
                EdgeEventType::RECIPE_PHASE_CHANGED,
                result.start_time_seconds,
                "recipe phase changed to " + result.phase_name,
            });
        if (event_bus_) {
            event_bus_->publish(
                RecipePhaseChanged{
                    zone_id_,
                    result.start_time_seconds,
                    previous_phase_name,
                    result.phase_name,
                });
        }
    }
    reported_phase_index_ = phase_index;
    result.readings = read_sensors();

    if (operational_state_ == OperationalState::EMERGENCY_LOCKDOWN &&
        !manual_reset_requested_) {
        apply_safe_fallback(delta_time_seconds, result, true);
        result.actuator_command = actuators_->command();
        result.actuator_output = actuators_->output();
        result.environment_state = environment_->state();
        publish_telemetry(result);
        return result;
    }

    auto request = base_request(delta_time_seconds);
    request.controller_input.measured_value =
        result.readings.soil_moisture_percent;
    request.source_valid =
        result.readings.soil_moisture_percent.has_value();
    result.decisions[controlled_variable_index(
        ControlledVariable::SOIL_MOISTURE)] =
        control_system_.execute(
            ControlledVariable::SOIL_MOISTURE, request);

    request = base_request(delta_time_seconds);
    request.controller_input.measured_value =
        result.readings.light_ppfd_umol_m2_s;
    request.source_valid =
        result.readings.light_ppfd_umol_m2_s.has_value();
    result.decisions[controlled_variable_index(
        ControlledVariable::LIGHT)] =
        control_system_.execute(ControlledVariable::LIGHT, request);

    request = base_request(delta_time_seconds);
    request.controller_input.measured_value = result.readings.ph;
    request.source_valid = result.readings.ph.has_value();
    const auto ph_index =
        controlled_variable_index(ControlledVariable::PH);
    request.daily_dose_milliliters =
        daily_dose_milliliters_[ph_index];
    request.seconds_since_last_dose =
        seconds_since_last_dose_[ph_index];
    request.ph_up_active =
        fertilizer_valves_.open(FertilizerType::PH_UP);
    request.ph_down_active =
        fertilizer_valves_.open(FertilizerType::PH_DOWN);
    result.decisions[ph_index] =
        control_system_.execute(ControlledVariable::PH, request);

    const auto water_index =
        controlled_variable_index(ControlledVariable::SOIL_MOISTURE);
    const double requested_water = std::max(
        0.0, result.decisions[water_index].command);
    for (const auto variable : {
             ControlledVariable::NITROGEN,
             ControlledVariable::PHOSPHORUS,
             ControlledVariable::POTASSIUM}) {
        const auto index = controlled_variable_index(variable);
        request = base_request(delta_time_seconds);
        request.controller_input.model_estimate =
            nutrient_model_value(variable, environment_->state());
        request.controller_input.water_delivered_liters =
            requested_water;
        request.controller_input.cumulative_dose_milliliters =
            cumulative_phase_dose_milliliters_[index];
        request.daily_dose_milliliters =
            daily_dose_milliliters_[index];
        request.seconds_since_last_dose =
            seconds_since_last_dose_[index];
        result.decisions[index] =
            control_system_.execute(variable, request);
    }

    bool command_executed = false;
    if (!update_operational_state(result)) {
        apply_safe_fallback(delta_time_seconds, result, false);
    } else {
        try {
            apply_decisions(delta_time_seconds, result);
            command_executed = true;
        } catch (const std::exception& error) {
            const std::string diagnostic =
                "actuator command failed: " +
                std::string(error.what());
            publish_command_failed(
                result.start_time_seconds,
                diagnostic);
            transition_operational_state(
                OperationalState::EMERGENCY_LOCKDOWN,
                diagnostic,
                result);
            apply_safe_fallback(delta_time_seconds, result, false);
        }
    }
    if (operational_state_ == OperationalState::NOMINAL) {
        update_dose_histories(delta_time_seconds, result);
    }
    result.actuator_command = actuators_->command();
    result.actuator_output = actuators_->output();
    result.environment_state = environment_->state();
    if (command_executed) {
        publish_command_executed(result);
    }
    publish_telemetry(result);
    return result;
}

bool EdgeRuntime::update_operational_state(
    EdgeStepResult& result) {
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    std::string reason;
    for (const auto& [fault_id, fault] : injected_faults_) {
        if (fault.severity == ControlFaultSeverity::CRITICAL ||
            severity == ControlFaultSeverity::NONE) {
            severity = fault.severity;
            reason =
                "injected fault " + fault_id + ": " +
                fault.diagnostic;
        }
        if (severity == ControlFaultSeverity::CRITICAL) {
            break;
        }
    }
    if (!result.readings.temperature_c.has_value() ||
        !std::isfinite(*result.readings.temperature_c)) {
        if (severity == ControlFaultSeverity::NONE) {
            severity = ControlFaultSeverity::RECOVERABLE;
            reason = "temperature sensor input is invalid";
        }
    } else if (!result.readings.air_humidity_percent.has_value() ||
               !std::isfinite(
                   *result.readings.air_humidity_percent)) {
        if (severity == ControlFaultSeverity::NONE) {
            severity = ControlFaultSeverity::RECOVERABLE;
            reason = "air humidity sensor input is invalid";
        }
    }
    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        const auto& decision = result.decisions[index];
        if (decision.status != ControlDecisionStatus::BLOCKED ||
            !decision.safety_critical ||
            decision.fault_severity == ControlFaultSeverity::NONE) {
            continue;
        }
        if (decision.fault_severity == ControlFaultSeverity::CRITICAL ||
            severity == ControlFaultSeverity::NONE) {
            severity = decision.fault_severity;
            reason =
                std::string(to_string(kControlledVariables[index])) +
                " control blocked: " + decision.message;
        }
        if (severity == ControlFaultSeverity::CRITICAL) {
            break;
        }
    }

    if (severity == ControlFaultSeverity::CRITICAL) {
        consecutive_recoverable_faults_ = 0;
        consecutive_healthy_steps_ = 0;
        transition_operational_state(
            OperationalState::EMERGENCY_LOCKDOWN,
            reason,
            result);
        return false;
    }

    if (operational_state_ == OperationalState::EMERGENCY_LOCKDOWN) {
        if (severity == ControlFaultSeverity::RECOVERABLE) {
            return false;
        }
        manual_reset_requested_ = false;
        consecutive_recoverable_faults_ = 0;
        consecutive_healthy_steps_ = 0;
        transition_operational_state(
            OperationalState::DEGRADED,
            "manual reset accepted; health verification started",
            result);
        return false;
    }

    if (severity == ControlFaultSeverity::RECOVERABLE) {
        consecutive_healthy_steps_ = 0;
        ++consecutive_recoverable_faults_;
        if (operational_state_ == OperationalState::NOMINAL) {
            transition_operational_state(
                OperationalState::DEGRADED,
                reason,
                result);
        } else if (
            consecutive_recoverable_faults_ >=
            state_policy_.recoverable_faults_before_lockdown) {
            transition_operational_state(
                OperationalState::EMERGENCY_LOCKDOWN,
                "recoverable fault persisted: " + reason,
                result);
        }
        return false;
    }

    consecutive_recoverable_faults_ = 0;
    if (operational_state_ == OperationalState::DEGRADED) {
        ++consecutive_healthy_steps_;
        if (consecutive_healthy_steps_ >=
            state_policy_.healthy_steps_before_nominal) {
            consecutive_healthy_steps_ = 0;
            transition_operational_state(
                OperationalState::NOMINAL,
                "automatic recovery after consecutive healthy cycles",
                result);
            return true;
        }
        return false;
    }

    consecutive_healthy_steps_ = 0;
    return true;
}

void EdgeRuntime::transition_operational_state(
    OperationalState next_state,
    const std::string& reason,
    EdgeStepResult& result) {
    if (next_state == operational_state_) {
        result.operational_state = operational_state_;
        return;
    }

    const auto previous_state = operational_state_;
    if (next_state != OperationalState::NOMINAL) {
        actuators_->stop_all();
    }
    operational_state_ = next_state;
    result.operational_state = operational_state_;
    result.events.push_back(
        {
            EdgeEventType::OPERATIONAL_STATE_CHANGED,
            result.start_time_seconds,
            std::string(to_string(previous_state)) + " -> " +
                to_string(next_state) + ": " + reason,
            previous_state,
            next_state,
        });
    if (event_bus_) {
        event_bus_->publish(
            StateChanged{
                zone_id_,
                result.start_time_seconds,
                previous_state,
                next_state,
                reason,
            });
    }
    if (next_state == OperationalState::EMERGENCY_LOCKDOWN) {
        manual_reset_requested_ = false;
        result.events.push_back(
            {
                EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED,
                result.start_time_seconds,
                reason,
                previous_state,
                next_state,
            });
        if (event_bus_) {
            event_bus_->publish(
                EmergencyTriggered{
                    zone_id_,
                    result.start_time_seconds,
                    reason,
                });
        }
    }
}

void EdgeRuntime::apply_safe_fallback(
    double delta_time_seconds,
    EdgeStepResult& result,
    bool replace_decisions) {
    actuators_->stop_all();
    result.operational_state = operational_state_;
    if (replace_decisions) {
        for (std::size_t index = 0;
             index < kControlledVariableCount;
             ++index) {
            auto& decision = result.decisions[index];
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.safety_critical = true;
            decision.fault_severity = ControlFaultSeverity::CRITICAL;
            decision.actuator =
                control_system_.recipe().controllers[index].actuator;
            decision.message =
                "runtime is in " +
                std::string(to_string(operational_state_));
        }
    }
    advance_physics(delta_time_seconds, result);
    update_dose_histories(delta_time_seconds, result);
}

void EdgeRuntime::apply_decisions(
    double delta_time_seconds,
    EdgeStepResult& result) {
    const auto water_index =
        controlled_variable_index(ControlledVariable::SOIL_MOISTURE);
    const auto light_index =
        controlled_variable_index(ControlledVariable::LIGHT);
    const double lighting_percent = std::clamp(
        result.decisions[light_index].command, 0.0, 100.0);
    lighting_.set_command_percent(lighting_percent);

    const double water_command =
        result.decisions[water_index].command;
    if (water_command > 0.0 && !water_pump_.active()) {
        water_pump_.request_volume_liters(water_command);
    }

    fertilizer_valves_.close_all();
    FertilizerValues<double> requested_doses{};
    requested_doses[fertilizer_index(FertilizerType::NITROGEN)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::NITROGEN)]
                .command);
    requested_doses[fertilizer_index(FertilizerType::PHOSPHORUS)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::PHOSPHORUS)]
                .command);
    requested_doses[fertilizer_index(FertilizerType::POTASSIUM)] =
        std::max(
            0.0,
            result.decisions[controlled_variable_index(
                ControlledVariable::POTASSIUM)]
                .command);

    const double ph_command =
        result.decisions[controlled_variable_index(
            ControlledVariable::PH)]
            .command;
    if (ph_command > 0.0) {
        requested_doses[fertilizer_index(FertilizerType::PH_UP)] =
            ph_command;
    } else if (ph_command < 0.0) {
        requested_doses[fertilizer_index(FertilizerType::PH_DOWN)] =
            std::abs(ph_command);
    }

    FertilizerValues<double> close_after_seconds{};
    std::vector<double> close_events;
    if (water_pump_.active()) {
        const double available_pump_seconds = std::min(
            delta_time_seconds,
            water_pump_.remaining_time_seconds());
        for (std::size_t index = 0;
             index < kFertilizerTypeCount;
             ++index) {
            if (requested_doses[index] <= 0.0) {
                continue;
            }
            const auto type = static_cast<FertilizerType>(index);
            const double flow =
                actuators_->config()
                    .fertilizer_flow_milliliters_per_hour[index];
            const double requested_seconds =
                requested_doses[index] / flow * kSecondsPerHour;
            close_after_seconds[index] = std::min(
                requested_seconds, available_pump_seconds);
            if (close_after_seconds[index] > kEventToleranceSeconds) {
                fertilizer_valves_.set_open(type, true);
                close_events.push_back(close_after_seconds[index]);
            }
        }
    }

    std::sort(close_events.begin(), close_events.end());
    close_events.erase(
        std::unique(
            close_events.begin(),
            close_events.end(),
            [](double left, double right) {
                return std::abs(left - right) <=
                       kEventToleranceSeconds;
            }),
        close_events.end());

    double elapsed = 0.0;
    for (const double event_time : close_events) {
        if (event_time > elapsed + kEventToleranceSeconds) {
            advance_physics(event_time - elapsed, result);
            elapsed = event_time;
        }
        for (std::size_t index = 0;
             index < kFertilizerTypeCount;
             ++index) {
            if (close_after_seconds[index] > 0.0 &&
                close_after_seconds[index] <=
                    event_time + kEventToleranceSeconds) {
                fertilizer_valves_.set_open(
                    static_cast<FertilizerType>(index), false);
                close_after_seconds[index] = 0.0;
            }
        }
    }

    if (elapsed < delta_time_seconds - kEventToleranceSeconds) {
        advance_physics(delta_time_seconds - elapsed, result);
    }
    fertilizer_valves_.close_all();
}

void EdgeRuntime::advance_physics(
    double delta_time_seconds,
    EdgeStepResult& result) {
    actuators_->step(delta_time_seconds);
    const auto& output = actuators_->output();
    result.delivered_water_liters +=
        output.irrigation_volume_liters_last_step;
    for (std::size_t index = 0;
         index < kFertilizerTypeCount;
         ++index) {
        result.delivered_fertilizer_milliliters[index] +=
            output.fertilizer_volume_milliliters_last_step[index];
    }
    environment_->step(delta_time_seconds, output);
}

void EdgeRuntime::update_dose_histories(
    double delta_time_seconds,
    const EdgeStepResult& result) {
    for (const auto variable : {
             ControlledVariable::PH,
             ControlledVariable::NITROGEN,
             ControlledVariable::PHOSPHORUS,
             ControlledVariable::POTASSIUM}) {
        seconds_since_last_dose_[
            controlled_variable_index(variable)] +=
            delta_time_seconds;
    }

    const std::array<
        std::pair<ControlledVariable, double>,
        4>
        delivered{{
            {
                ControlledVariable::PH,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::PH_UP)] +
                    result.delivered_fertilizer_milliliters[
                        fertilizer_index(FertilizerType::PH_DOWN)],
            },
            {
                ControlledVariable::NITROGEN,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::NITROGEN)],
            },
            {
                ControlledVariable::PHOSPHORUS,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::PHOSPHORUS)],
            },
            {
                ControlledVariable::POTASSIUM,
                result.delivered_fertilizer_milliliters[
                    fertilizer_index(FertilizerType::POTASSIUM)],
            },
        }};

    for (const auto& [variable, dose] : delivered) {
        if (dose <= kDeliveredTolerance) {
            continue;
        }
        const auto index = controlled_variable_index(variable);
        cumulative_phase_dose_milliliters_[index] += dose;
        daily_dose_milliliters_[index] += dose;
        seconds_since_last_dose_[index] = 0.0;
    }
}

void EdgeRuntime::publish_telemetry(
    const EdgeStepResult& result) noexcept {
    if (!event_bus_) {
        return;
    }
    event_bus_->publish(
        TelemetrySample{
            zone_id_,
            result.sequence_number,
            result.environment_state.simulation_time_seconds,
            result.operational_state,
            result.readings,
            result.actuator_command,
            result.actuator_output,
            result.environment_state,
        });
}

void EdgeRuntime::publish_command_executed(
    const EdgeStepResult& result) noexcept {
    if (!event_bus_) {
        return;
    }
    event_bus_->publish(
        CommandExecuted{
            zone_id_,
            result.start_time_seconds,
            result.actuator_command,
            result.actuator_output,
            result.delivered_water_liters,
            result.delivered_fertilizer_milliliters,
        });
}

void EdgeRuntime::publish_command_failed(
    double timestamp_seconds,
    const std::string& diagnostic) noexcept {
    if (!event_bus_) {
        return;
    }
    event_bus_->publish(
        CommandFailed{
            zone_id_,
            timestamp_seconds,
            "actuator-bank",
            diagnostic,
        });
}

}  // namespace smarthydro
