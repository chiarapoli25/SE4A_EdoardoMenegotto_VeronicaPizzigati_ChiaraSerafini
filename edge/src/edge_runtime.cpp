#include "smarthydro/edge_runtime.hpp"

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
    std::uint32_t sensor_seed)
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
      fertilizer_valves_(*actuators_) {
    seconds_since_last_dose_.fill(
        std::numeric_limits<double>::max() / 4.0);
}

EdgeRuntime::EdgeRuntime(
    Recipe recipe,
    SensorAdapterArray sensors,
    std::unique_ptr<IActuator> actuators,
    std::unique_ptr<IEnvironment> environment)
    : control_system_(std::move(recipe)),
      actuators_(require_actuators(std::move(actuators))),
      environment_(require_environment(std::move(environment))),
      sensors_(std::move(sensors)),
      water_pump_(*actuators_),
      lighting_(*actuators_),
      fertilizer_valves_(*actuators_) {
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
    const double elapsed_hours = elapsed_seconds / kSecondsPerHour;

    ControlRequest request;
    request.controller_input.delta_time_seconds = delta_time_seconds;
    request.elapsed_recipe_hours = elapsed_hours;
    request.simulated_time_seconds = elapsed_seconds;
    request.hour_of_day = hour_of_day(elapsed_hours);
    return request;
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

    const auto phase = active_phase_index(
        elapsed_seconds / kSecondsPerHour);
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
    const auto phase_index = active_phase_index(
        result.start_time_seconds / kSecondsPerHour);
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
        result.events.push_back(
            {
                EdgeEventType::RECIPE_PHASE_CHANGED,
                result.start_time_seconds,
                "recipe phase changed to " + result.phase_name,
            });
    }
    reported_phase_index_ = phase_index;
    result.readings = read_sensors();

    if (operational_state_ == OperationalState::EMERGENCY_LOCKDOWN) {
        hold_emergency_lockdown(delta_time_seconds, result);
        result.actuator_command = actuators_->command();
        result.actuator_output = actuators_->output();
        result.environment_state = environment_->state();
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

    if (enter_lockdown_for_critical_decision(result)) {
        advance_physics(delta_time_seconds, result);
    } else {
        try {
            apply_decisions(delta_time_seconds, result);
        } catch (const std::exception& error) {
            enter_emergency_lockdown(
                "actuator command failed: " + std::string(error.what()),
                result);
            advance_physics(delta_time_seconds, result);
        }
    }
    update_dose_histories(delta_time_seconds, result);
    result.actuator_command = actuators_->command();
    result.actuator_output = actuators_->output();
    result.environment_state = environment_->state();
    return result;
}

bool EdgeRuntime::enter_lockdown_for_critical_decision(
    EdgeStepResult& result) {
    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        const auto& decision = result.decisions[index];
        if (decision.status != ControlDecisionStatus::BLOCKED ||
            !decision.safety_critical) {
            continue;
        }

        enter_emergency_lockdown(
            std::string(to_string(kControlledVariables[index])) +
                " control blocked: " + decision.message,
            result);
        return true;
    }
    return false;
}

void EdgeRuntime::enter_emergency_lockdown(
    const std::string& reason,
    EdgeStepResult& result) {
    actuators_->stop_all();
    operational_state_ = OperationalState::EMERGENCY_LOCKDOWN;
    result.operational_state = operational_state_;
    result.events.push_back(
        {
            EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED,
            result.start_time_seconds,
            reason,
        });
}

void EdgeRuntime::hold_emergency_lockdown(
    double delta_time_seconds,
    EdgeStepResult& result) {
    actuators_->stop_all();
    result.operational_state = operational_state_;
    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        auto& decision = result.decisions[index];
        decision.status = ControlDecisionStatus::BLOCKED;
        decision.safety_critical = true;
        decision.actuator =
            control_system_.recipe().controllers[index].actuator;
        decision.message = "runtime is in emergency lockdown";
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

}  // namespace smarthydro
