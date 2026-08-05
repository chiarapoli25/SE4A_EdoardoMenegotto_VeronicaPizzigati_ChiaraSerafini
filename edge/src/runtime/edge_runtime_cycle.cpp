#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;
constexpr double kSecondsPerDay = 24.0 * kSecondsPerHour;

double hour_of_day(double elapsed_hours) {
    double hour = std::fmod(elapsed_hours, 24.0);
    if (hour < 0.0) {
        hour += 24.0;
    }
    return hour;
}

std::optional<double> nutrient_probe_estimate(
    ControlledVariable variable,
    const SensorReadings& readings) {
    switch (variable) {
        case ControlledVariable::NITROGEN:
            return readings.nitrogen_estimate_mg_per_liter;
        case ControlledVariable::PHOSPHORUS:
            return readings.phosphorus_estimate_mg_per_liter;
        case ControlledVariable::POTASSIUM:
            return readings.potassium_estimate_mg_per_liter;
        default:
            break;
    }
    throw std::invalid_argument("variable has no nutrient model");
}

}  // namespace

double EdgeRuntime::elapsed_recipe_hours() const noexcept {
    return elapsed_recipe_seconds() / kSecondsPerHour;
}

ControlRequest EdgeRuntime::base_request(double delta_time_seconds) const {
    const double elapsed_seconds =
        environment_->state().simulation_time_seconds;

    ControlRequest request;
    request.controller_input.delta_time_seconds = delta_time_seconds;
    request.elapsed_recipe_hours = elapsed_recipe_hours();
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

double EdgeRuntime::total_recipe_duration_hours() const noexcept {
    double total = 0.0;
    for (const auto& phase : control_system_.recipe().phases) {
        total += phase.duration_hours;
    }
    return total;
}

ControlledValues<ValueRange> EdgeRuntime::active_safety_ranges() const {
    ControlledValues<ValueRange> ranges{};
    const auto& phase = control_system_.active_phase(
        elapsed_recipe_hours());
    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        ranges[index] = phase.targets[index].safety_range;
    }
    return ranges;
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
    readings.soil_bulk_ec_ms_cm =
        sensors_[sensor_channel_index(SensorChannel::SOIL_CONDUCTIVITY)]
            ->read(state);
    readings.ph =
        sensors_[sensor_channel_index(SensorChannel::PH)]
            ->read(state);
    readings.light_ppfd_umol_m2_s =
        sensors_[sensor_channel_index(SensorChannel::LIGHT)]
            ->read(state);
    fault_injector_.alter_readings(
        readings, state.simulation_time_seconds);
    update_soil_probe_estimates(readings, state, soil_probe_model_);
    return readings;
}

EdgeStepResult EdgeRuntime::step(double delta_time_seconds) {
    if (!std::isfinite(delta_time_seconds) ||
        delta_time_seconds <= 0.0) {
        throw std::invalid_argument(
            "edge runtime step duration must be positive and finite");
    }

    reset_histories_if_needed();
    reset_actuator_observation();

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
    if (!recipe_completed_ &&
        elapsed_recipe_hours() >= total_recipe_duration_hours()) {
        recipe_completed_ = true;
        result.events.push_back(
            {
                EdgeEventType::RECIPE_COMPLETED,
                result.start_time_seconds,
                "recipe completed; final phase remains active: " +
                    result.phase_name,
            });
        if (event_bus_) {
            event_bus_->publish(
                RecipeCompleted{
                    zone_id_,
                    result.start_time_seconds,
                    control_system_.recipe().id,
                    result.phase_name,
                    total_recipe_duration_hours(),
                });
        }
    }
    result.recipe_completed = recipe_completed_;
    result.readings = read_sensors();
    auto detected_faults = fault_detector_.observe_readings(
        result.readings, active_safety_ranges());
    for (const auto& fault : previous_actuator_faults_) {
        if (fault.severity == ControlFaultSeverity::RECOVERABLE) {
            detected_faults.push_back(fault);
        }
    }
    if (!detected_faults.empty()) {
        publish_detected_faults(
            detected_faults, result.start_time_seconds);
    }

    if (operational_state_ == OperationalState::EMERGENCY_LOCKDOWN) {
        if (manual_reset_requested_) {
            apply_safe_fallback(delta_time_seconds, result, false);
            auto actuator_faults = fault_detector_.observe_actuators(
                detector_command_observation_,
                detector_output_observation_,
                actuators_->config(),
                delta_time_seconds);
            previous_actuator_faults_ = actuator_faults;
            detected_faults.insert(
                detected_faults.end(),
                actuator_faults.begin(),
                actuator_faults.end());
            publish_detected_faults(
                detected_faults, result.start_time_seconds);
            update_operational_state(result, detected_faults);
            result.actuator_command = actuators_->command();
            result.actuator_output = effective_actuator_output_;
            result.environment_state = environment_->state();
            publish_telemetry(result);
            return result;
        }
        publish_detected_faults(
            detected_faults, result.start_time_seconds);
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
            nutrient_probe_estimate(variable, result.readings);
        request.source_valid =
            request.controller_input.model_estimate.has_value();
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

    apply_degraded_isolation(result, detected_faults);

    bool command_executed = false;
    const bool state_evaluated_before_actuation =
        !detected_faults.empty();
    if (state_evaluated_before_actuation &&
        !update_operational_state(result, detected_faults)) {
        apply_safe_fallback(delta_time_seconds, result, false);
    } else {
        try {
            apply_decisions(delta_time_seconds, result);
            command_executed = true;
            auto actuator_faults = fault_detector_.observe_actuators(
                detector_command_observation_,
                detector_output_observation_,
                actuators_->config(),
                delta_time_seconds);
            previous_actuator_faults_ = actuator_faults;
            detected_faults.insert(
                detected_faults.end(),
                actuator_faults.begin(),
                actuator_faults.end());
            if (!actuator_faults.empty()) {
                publish_detected_faults(
                    detected_faults, result.start_time_seconds);
            }
            if (state_evaluated_before_actuation) {
                bool has_critical_actuator_fault = false;
                for (const auto& fault : actuator_faults) {
                    if (fault.severity ==
                        ControlFaultSeverity::CRITICAL) {
                        has_critical_actuator_fault = true;
                        break;
                    }
                }
                if (has_critical_actuator_fault) {
                    update_operational_state(
                        result, actuator_faults, false);
                }
            } else {
                update_operational_state(result, detected_faults);
            }
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
    if (command_executed) {
        update_dose_histories(delta_time_seconds, result);
    }
    publish_detected_faults(
        detected_faults, result.start_time_seconds);
    result.actuator_command = actuators_->command();
    result.actuator_output = effective_actuator_output_;
    result.environment_state = environment_->state();
    if (command_executed) {
        publish_command_executed(result);
    }
    publish_telemetry(result);
    return result;
}

}  // namespace smarthydro
