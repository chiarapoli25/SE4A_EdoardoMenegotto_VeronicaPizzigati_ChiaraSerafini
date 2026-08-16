#include <smarthydro/runtime/edge_runtime.hpp>

#include <array>
#include <limits>
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
              control_system_.recipe()),
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
