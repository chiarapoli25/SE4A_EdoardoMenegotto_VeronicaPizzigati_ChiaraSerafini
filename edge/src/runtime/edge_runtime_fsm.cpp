#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <algorithm>
#include <cmath>
#include <optional>
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

bool is_sensor_mode(FaultMode mode) noexcept {
    return mode == FaultMode::SENSOR_DROPOUT ||
           mode == FaultMode::SENSOR_STUCK ||
           mode == FaultMode::SENSOR_OFFSET;
}

bool is_actuator_mode(FaultMode mode) noexcept {
    return mode == FaultMode::ACTUATOR_STUCK_OFF ||
           mode == FaultMode::ACTUATOR_STUCK_ON ||
           mode == FaultMode::ACTUATOR_SLOW_RESPONSE;
}

bool is_sensor_target(const std::string& target) noexcept {
    return target == "temperature" ||
           target == "air_humidity" ||
           target == "soil_moisture" ||
           target == "soil_conductivity" ||
           target == "ph" ||
           target == "light";
}

std::optional<FertilizerType> fertilizer_target(
    const std::string& target) noexcept {
    if (target == "nitrogen_valve") {
        return FertilizerType::NITROGEN;
    }
    if (target == "phosphorus_valve") {
        return FertilizerType::PHOSPHORUS;
    }
    if (target == "potassium_valve") {
        return FertilizerType::POTASSIUM;
    }
    if (target == "ph_up_valve") {
        return FertilizerType::PH_UP;
    }
    if (target == "ph_down_valve") {
        return FertilizerType::PH_DOWN;
    }
    return std::nullopt;
}

bool is_actuator_target(const std::string& target) noexcept {
    return target == "water_pump" ||
           target == "lighting" ||
           fertilizer_target(target).has_value();
}

std::optional<double>* sensor_value(
    SensorReadings& readings,
    const std::string& target) noexcept {
    if (target == "temperature") {
        return &readings.temperature_c;
    }
    if (target == "air_humidity") {
        return &readings.air_humidity_percent;
    }
    if (target == "soil_moisture") {
        return &readings.soil_moisture_percent;
    }
    if (target == "soil_conductivity") {
        return &readings.soil_bulk_ec_ms_cm;
    }
    if (target == "ph") {
        return &readings.ph;
    }
    if (target == "light") {
        return &readings.light_ppfd_umol_m2_s;
    }
    return nullptr;
}

void validate_fault_specification(const FaultSpecification& specification) {
    if (specification.fault_id.empty()) {
        throw std::invalid_argument(
            "fault specification requires a non-empty identifier");
    }
    if (specification.target.empty()) {
        throw std::invalid_argument(
            "fault specification requires a target");
    }
    if (specification.target_kind == FaultTargetKind::SENSOR) {
        if (!is_sensor_mode(specification.mode)) {
            throw std::invalid_argument(
                "sensor target requires a sensor fault mode");
        }
        if (!is_sensor_target(specification.target)) {
            throw std::invalid_argument(
                "unknown sensor fault target: " + specification.target);
        }
    } else {
        if (!is_actuator_mode(specification.mode)) {
            throw std::invalid_argument(
                "actuator target requires an actuator fault mode");
        }
        if (!is_actuator_target(specification.target)) {
            throw std::invalid_argument(
                "unknown actuator fault target: " + specification.target);
        }
    }

    if (specification.duration_seconds.has_value() &&
        (!std::isfinite(*specification.duration_seconds) ||
         *specification.duration_seconds <= 0.0)) {
        throw std::invalid_argument(
            "fault duration must be positive and finite");
    }
    if (specification.value.has_value() &&
        !std::isfinite(*specification.value)) {
        throw std::invalid_argument("fault value must be finite");
    }
    if (specification.mode == FaultMode::SENSOR_OFFSET &&
        !specification.value.has_value()) {
        throw std::invalid_argument(
            "sensor_offset requires an additive value");
    }
    if (specification.mode == FaultMode::ACTUATOR_SLOW_RESPONSE &&
        (!specification.value.has_value() ||
         *specification.value <= 0.0 ||
         *specification.value >= 1.0)) {
        throw std::invalid_argument(
            "actuator_slow_response requires a factor in (0, 1)");
    }
}

}  // namespace

void EdgeRuntime::inject_fault(FaultSpecification specification) {
    validate_fault_specification(specification);
    for (const auto& [identifier, active] : injected_faults_) {
        static_cast<void>(identifier);
        if (active.specification.target_kind ==
                specification.target_kind &&
            active.specification.target == specification.target) {
            throw std::invalid_argument(
                "target already has an active injected fault");
        }
    }
    const auto identifier = specification.fault_id;
    const double now = environment_->state().simulation_time_seconds;
    std::optional<double> expires_at;
    if (specification.duration_seconds.has_value()) {
        expires_at = now + *specification.duration_seconds;
    }
    const auto [iterator, inserted] = injected_faults_.emplace(
        identifier,
        InjectedFault{
            std::move(specification),
            now,
            expires_at,
        });
    if (!inserted) {
        throw std::invalid_argument(
            "injected fault identifier is already active");
    }
    static_cast<void>(iterator);
}

bool EdgeRuntime::reset_injected_fault(
    const std::string& fault_id) noexcept {
    return !fault_id.empty() && injected_faults_.erase(fault_id) != 0;
}

bool EdgeRuntime::has_injected_fault(
    const std::string& fault_id) const noexcept {
    return injected_faults_.find(fault_id) != injected_faults_.end();
}

void EdgeRuntime::expire_injected_faults(double timestamp_seconds) {
    for (auto iterator = injected_faults_.begin();
         iterator != injected_faults_.end();) {
        if (iterator->second.expires_at_seconds.has_value() &&
            timestamp_seconds >=
                *iterator->second.expires_at_seconds) {
            iterator = injected_faults_.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

void EdgeRuntime::mark_fault_detected(
    InjectedFault& fault,
    ControlFaultSeverity severity,
    std::string diagnostic) {
    if (fault.detected) {
        return;
    }
    fault.detected = true;
    fault.detected_severity = severity;
    fault.diagnostic = std::move(diagnostic);
    if (event_bus_) {
        event_bus_->publish(
            FaultDetected{
                zone_id_,
                environment_->state().simulation_time_seconds,
                to_string(fault.specification.mode),
                severity,
                "fault " + fault.specification.fault_id +
                    " on " + fault.specification.target + ": " +
                    fault.diagnostic,
            });
    }
}

void EdgeRuntime::apply_sensor_faults(SensorReadings& readings) {
    for (auto& [identifier, fault] : injected_faults_) {
        static_cast<void>(identifier);
        if (fault.specification.target_kind !=
            FaultTargetKind::SENSOR) {
            continue;
        }
        auto* value = sensor_value(
            readings, fault.specification.target);
        if (!value) {
            continue;
        }
        ++fault.observation_count;
        switch (fault.specification.mode) {
            case FaultMode::SENSOR_DROPOUT:
                value->reset();
                mark_fault_detected(
                    fault,
                    ControlFaultSeverity::RECOVERABLE,
                    "sensor reading is unavailable");
                break;
            case FaultMode::SENSOR_OFFSET:
                if (value->has_value()) {
                    **value += *fault.specification.value;
                    mark_fault_detected(
                        fault,
                        ControlFaultSeverity::RECOVERABLE,
                        "persistent measurement offset observed");
                }
                break;
            case FaultMode::SENSOR_STUCK:
                if (!fault.latched_sensor_value.has_value()) {
                    if (fault.specification.value.has_value()) {
                        fault.latched_sensor_value =
                            fault.specification.value;
                    } else if (value->has_value()) {
                        fault.latched_sensor_value = **value;
                    }
                }
                if (fault.latched_sensor_value.has_value()) {
                    *value = fault.latched_sensor_value;
                }
                if (fault.observation_count >= 2U) {
                    mark_fault_detected(
                        fault,
                        ControlFaultSeverity::RECOVERABLE,
                        "sensor value remained frozen across samples");
                }
                break;
            default:
                break;
        }
    }
}

ActuatorOutput EdgeRuntime::apply_actuator_faults(
    const ActuatorCommand& command,
    const ActuatorOutput& raw_output,
    double delta_time_seconds) {
    ActuatorOutput effective = raw_output;
    for (auto& [identifier, fault] : injected_faults_) {
        static_cast<void>(identifier);
        if (fault.specification.target_kind !=
            FaultTargetKind::ACTUATOR) {
            continue;
        }
        const auto& target = fault.specification.target;
        bool demanded = false;
        bool unexpectedly_active = false;
        const double factor =
            fault.specification.value.value_or(1.0);

        if (target == "water_pump") {
            demanded =
                command.requested_irrigation_volume_liters > 0.0 ||
                raw_output.water_pump_on;
            unexpectedly_active =
                command.requested_irrigation_volume_liters <= 0.0 &&
                !raw_output.water_pump_on;
            if (fault.specification.mode ==
                FaultMode::ACTUATOR_STUCK_OFF) {
                effective.water_pump_on = false;
                effective.water_pump_flow_liters_per_hour = 0.0;
                effective.irrigation_volume_liters_last_step = 0.0;
                effective.water_pump_on_time_seconds_last_step = 0.0;
                effective.fertilizer_valves_open.fill(false);
                effective.fertilizer_flow_milliliters_per_hour.fill(0.0);
                effective.fertilizer_volume_milliliters_last_step.fill(0.0);
            } else if (fault.specification.mode ==
                       FaultMode::ACTUATOR_STUCK_ON) {
                effective.water_pump_on = true;
                effective.water_pump_flow_liters_per_hour =
                    actuators_->config()
                        .water_pump_flow_liters_per_hour;
                effective.water_pump_on_time_seconds_last_step =
                    delta_time_seconds;
                effective.irrigation_volume_liters_last_step =
                    effective.water_pump_flow_liters_per_hour *
                    delta_time_seconds / 3600.0;
            } else {
                effective.water_pump_flow_liters_per_hour *= factor;
                effective.irrigation_volume_liters_last_step *= factor;
            }
        } else if (target == "lighting") {
            demanded = command.lighting_percent > 0.0;
            unexpectedly_active = command.lighting_percent <= 0.0;
            if (fault.specification.mode ==
                FaultMode::ACTUATOR_STUCK_OFF) {
                effective.lighting_power_watts = 0.0;
            } else if (fault.specification.mode ==
                       FaultMode::ACTUATOR_STUCK_ON) {
                effective.lighting_power_watts =
                    actuators_->config().maximum_lighting_power_watts;
            } else {
                effective.lighting_power_watts *= factor;
            }
        } else if (const auto fertilizer =
                       fertilizer_target(target)) {
            const auto index = fertilizer_index(*fertilizer);
            demanded = command.fertilizer_valves_open[index];
            unexpectedly_active =
                !command.fertilizer_valves_open[index];
            if (fault.specification.mode ==
                FaultMode::ACTUATOR_STUCK_OFF) {
                effective.fertilizer_valves_open[index] = false;
                effective.fertilizer_flow_milliliters_per_hour[index] =
                    0.0;
                effective.fertilizer_volume_milliliters_last_step[index] =
                    0.0;
            } else if (fault.specification.mode ==
                       FaultMode::ACTUATOR_STUCK_ON) {
                effective.fertilizer_valves_open[index] = true;
                if (effective.water_pump_on) {
                    effective.fertilizer_flow_milliliters_per_hour[index] =
                        actuators_->config()
                            .fertilizer_flow_milliliters_per_hour[index];
                    effective
                        .fertilizer_volume_milliliters_last_step[index] =
                        effective
                            .fertilizer_flow_milliliters_per_hour[index] *
                        delta_time_seconds / 3600.0;
                }
            } else {
                effective.fertilizer_flow_milliliters_per_hour[index] *=
                    factor;
                effective.fertilizer_volume_milliliters_last_step[index] *=
                    factor;
            }
        }

        if (fault.specification.mode ==
                FaultMode::ACTUATOR_STUCK_ON &&
            unexpectedly_active) {
            mark_fault_detected(
                fault,
                ControlFaultSeverity::CRITICAL,
                "actuator remains active without a command");
        } else if (
            demanded &&
            fault.specification.mode !=
                FaultMode::ACTUATOR_STUCK_ON) {
            mark_fault_detected(
                fault,
                ControlFaultSeverity::RECOVERABLE,
                fault.specification.mode ==
                        FaultMode::ACTUATOR_STUCK_OFF
                    ? "commanded actuator produced no output"
                    : "actuator output is below the requested response");
        }
    }
    return effective;
}

void EdgeRuntime::apply_post_actuation_fault_state(
    EdgeStepResult& result) {
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    std::string reason;
    for (const auto& [identifier, fault] : injected_faults_) {
        if (!fault.detected ||
            fault.specification.target_kind !=
                FaultTargetKind::ACTUATOR) {
            continue;
        }
        if (fault.detected_severity ==
                ControlFaultSeverity::CRITICAL ||
            severity == ControlFaultSeverity::NONE) {
            severity = fault.detected_severity;
            reason =
                "detected fault " + identifier + ": " +
                fault.diagnostic;
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
    } else if (severity == ControlFaultSeverity::RECOVERABLE &&
               operational_state_ == OperationalState::NOMINAL) {
        consecutive_recoverable_faults_ = 1;
        consecutive_healthy_steps_ = 0;
        if (state_policy_.recoverable_faults_before_lockdown <= 1) {
            transition_operational_state(
                OperationalState::EMERGENCY_LOCKDOWN,
                "recoverable fault persisted: " + reason,
                result);
        } else {
            transition_operational_state(
                OperationalState::DEGRADED,
                reason,
                result);
        }
    }
}

void EdgeRuntime::apply_degraded_isolation(EdgeStepResult& result) {
    ControlledValues<bool> isolated{};
    ControlledValues<std::string> reasons{};

    const auto isolate = [&isolated, &reasons](
                             ControlledVariable variable,
                             const std::string& reason) {
        const auto index = controlled_variable_index(variable);
        isolated[index] = true;
        if (reasons[index].empty()) {
            reasons[index] = reason;
        }
    };

    for (const auto& [identifier, fault] : injected_faults_) {
        if (!fault.detected ||
            fault.detected_severity !=
                ControlFaultSeverity::RECOVERABLE) {
            continue;
        }

        const auto& target = fault.specification.target;
        const std::string reason =
            "control isolated in Degraded mode: " + identifier +
            " (" + target + ")";
        if (fault.specification.target_kind ==
            FaultTargetKind::SENSOR) {
            if (target == "soil_moisture") {
                isolate(ControlledVariable::SOIL_MOISTURE, reason);
            } else if (target == "light") {
                isolate(ControlledVariable::LIGHT, reason);
            } else if (target == "ph") {
                isolate(ControlledVariable::PH, reason);
            }
            continue;
        }

        if (target == "lighting") {
            isolate(ControlledVariable::LIGHT, reason);
        } else if (target == "water_pump") {
            for (const auto variable : {
                     ControlledVariable::SOIL_MOISTURE,
                     ControlledVariable::PH,
                     ControlledVariable::NITROGEN,
                     ControlledVariable::PHOSPHORUS,
                     ControlledVariable::POTASSIUM}) {
                isolate(variable, reason);
            }
        } else if (target == "nitrogen_valve") {
            isolate(ControlledVariable::NITROGEN, reason);
        } else if (target == "phosphorus_valve") {
            isolate(ControlledVariable::PHOSPHORUS, reason);
        } else if (target == "potassium_valve") {
            isolate(ControlledVariable::POTASSIUM, reason);
        } else if (
            target == "ph_up_valve" ||
            target == "ph_down_valve") {
            isolate(ControlledVariable::PH, reason);
        }
    }

    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        if (!isolated[index]) {
            continue;
        }
        auto& decision = result.decisions[index];
        decision.status = ControlDecisionStatus::BLOCKED;
        decision.safety_critical = true;
        decision.fault_severity =
            ControlFaultSeverity::RECOVERABLE;
        decision.command = 0.0;
        decision.actuator =
            control_system_.recipe().controllers[index].actuator;
        decision.message = reasons[index];
        decision.predicted_value.reset();
    }
}

bool EdgeRuntime::trigger_emergency_stop(const std::string& reason) {
    if (reason.empty()) {
        throw std::invalid_argument(
            "emergency stop reason must not be empty");
    }
    stop_all_actuators();
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

bool EdgeRuntime::update_operational_state(
    EdgeStepResult& result) {
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    std::string reason;
    std::string runtime_fault_type;
    for (const auto& [fault_id, fault] : injected_faults_) {
        if (!fault.detected) {
            continue;
        }
        if (fault.detected_severity ==
                ControlFaultSeverity::CRITICAL ||
            severity == ControlFaultSeverity::NONE) {
            severity = fault.detected_severity;
            reason =
                "detected fault " + fault_id + ": " +
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
            runtime_fault_type = "temperature_sensor_unavailable";
        }
    } else if (!result.readings.air_humidity_percent.has_value() ||
               !std::isfinite(
                   *result.readings.air_humidity_percent)) {
        if (severity == ControlFaultSeverity::NONE) {
            severity = ControlFaultSeverity::RECOVERABLE;
            reason = "air humidity sensor input is invalid";
            runtime_fault_type = "air_humidity_sensor_unavailable";
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
            runtime_fault_type =
                std::string(to_string(kControlledVariables[index])) +
                "_control_fault";
        }
        if (severity == ControlFaultSeverity::CRITICAL) {
            break;
        }
    }

    if (!runtime_fault_type.empty() &&
        reported_runtime_faults_.insert(runtime_fault_type).second &&
        event_bus_) {
        event_bus_->publish(
            FaultDetected{
                zone_id_,
                result.start_time_seconds,
                runtime_fault_type,
                severity,
                reason,
            });
    }
    if (severity == ControlFaultSeverity::NONE) {
        reported_runtime_faults_.clear();
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
        return operational_state_ !=
               OperationalState::EMERGENCY_LOCKDOWN;
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
        return true;
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
    if (next_state == OperationalState::EMERGENCY_LOCKDOWN) {
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

}  // namespace smarthydro
