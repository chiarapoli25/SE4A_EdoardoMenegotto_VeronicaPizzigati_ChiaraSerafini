#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <algorithm>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>

namespace smarthydro {

void EdgeRuntime::inject_fault(FaultSpecification specification) {
    fault_injector_.inject(
        std::move(specification),
        environment_->state().simulation_time_seconds);
}

bool EdgeRuntime::reset_injected_fault(
    const std::string& fault_id) noexcept {
    return fault_injector_.reset(fault_id);
}

bool EdgeRuntime::has_injected_fault(
    const std::string& fault_id) const noexcept {
    return fault_injector_.contains(fault_id);
}

void EdgeRuntime::publish_detected_faults(
    const std::vector<DetectedFault>& faults,
    double timestamp_seconds) {
    std::unordered_set<std::string> active_keys;
    for (const auto& fault : faults) {
        active_keys.insert(fault.key());
        if (!reported_runtime_faults_.insert(fault.key()).second ||
            !event_bus_) {
            continue;
        }
        event_bus_->publish(
            FaultDetected{
                zone_id_,
                timestamp_seconds,
                fault.component,
                fault.rule,
                fault.severity,
                fault.diagnostic,
            });
    }
    for (auto iterator = reported_runtime_faults_.begin();
         iterator != reported_runtime_faults_.end();) {
        if (active_keys.find(*iterator) == active_keys.end()) {
            iterator = reported_runtime_faults_.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

void EdgeRuntime::apply_degraded_isolation(
    EdgeStepResult& result,
    const std::vector<DetectedFault>& faults) {
    ControlledValues<bool> isolated{};
    ControlledValues<std::string> reasons{};
    const auto isolate = [&isolated, &reasons](
                             ControlledVariable variable,
                             const DetectedFault& fault) {
        const auto index = controlled_variable_index(variable);
        isolated[index] = true;
        if (reasons[index].empty()) {
            reasons[index] = "control isolated by " + fault.component +
                " / " + fault.rule + ": " + fault.diagnostic;
        }
    };

    for (const auto& fault : faults) {
        if (fault.severity != ControlFaultSeverity::RECOVERABLE) continue;
        const auto& component = fault.component;
        if (component == "soil_moisture_sensor") {
            isolate(ControlledVariable::SOIL_MOISTURE, fault);
        } else if (component == "light_sensor" ||
                   component == "lighting") {
            isolate(ControlledVariable::LIGHT, fault);
        } else if (component == "ph_sensor" ||
                   component == "ph-up_valve" ||
                   component == "ph-down_valve") {
            isolate(ControlledVariable::PH, fault);
        } else if (component == "nitrogen_model" ||
                   component == "nitrogen_valve") {
            isolate(ControlledVariable::NITROGEN, fault);
        } else if (component == "phosphorus_model" ||
                   component == "phosphorus_valve") {
            isolate(ControlledVariable::PHOSPHORUS, fault);
        } else if (component == "potassium_model" ||
                   component == "potassium_valve") {
            isolate(ControlledVariable::POTASSIUM, fault);
        } else if (component == "water_pump") {
            for (const auto variable : {
                     ControlledVariable::SOIL_MOISTURE,
                     ControlledVariable::PH,
                     ControlledVariable::NITROGEN,
                     ControlledVariable::PHOSPHORUS,
                     ControlledVariable::POTASSIUM}) {
                isolate(variable, fault);
            }
        } else if (component == "soil_conductivity_sensor" ||
                   component == "soil_ec_model" ||
                   component == "fertilizer_concentration_model") {
            isolate(ControlledVariable::NITROGEN, fault);
            isolate(ControlledVariable::PHOSPHORUS, fault);
            isolate(ControlledVariable::POTASSIUM, fault);
        }
    }

    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        if (!isolated[index]) continue;
        auto& decision = result.decisions[index];
        decision.status = ControlDecisionStatus::BLOCKED;
        decision.safety_critical = true;
        decision.fault_severity = ControlFaultSeverity::RECOVERABLE;
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
    EdgeStepResult& result,
    const std::vector<DetectedFault>& faults,
    bool count_recoverable_cycle) {
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    std::string reason;
    for (const auto& fault : faults) {
        if (fault.severity == ControlFaultSeverity::CRITICAL ||
            severity == ControlFaultSeverity::NONE) {
            severity = fault.severity;
            reason = fault.component + " violated " + fault.rule +
                ": " + fault.diagnostic;
        }
        if (severity == ControlFaultSeverity::CRITICAL) break;
    }

    if (severity == ControlFaultSeverity::NONE) {
        for (std::size_t index = 0;
             index < kControlledVariableCount;
             ++index) {
            const auto& decision = result.decisions[index];
            if (decision.status == ControlDecisionStatus::BLOCKED &&
                decision.safety_critical &&
                decision.fault_severity == ControlFaultSeverity::CRITICAL) {
                severity = ControlFaultSeverity::CRITICAL;
                reason = std::string(to_string(
                    static_cast<ControlledVariable>(index))) +
                    " control interlock: " + decision.message;
                break;
            }
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
        if (severity != ControlFaultSeverity::NONE ||
            !manual_reset_requested_) {
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
        // Un valore N/P/K fuori dal range agronomico della ricetta e' una
        // condizione di processo, non il guasto persistente di un sensore o
        // attuatore. apply_degraded_isolation() ha gia' chiuso soltanto la
        // relativa valvola: promuoverla dopo tre cicli a EmergencyLockdown
        // spegneva anche la pompa, faceva seccare il substrato e concentrava
        // ulteriormente i sali, creando una retroazione opposta a quella
        // desiderata. Rimane visibile come stato Degraded finche' il valore
        // non rientra, ma non puo' arrestare gli altri anelli di controllo.
        const bool contains_only_process_excursions =
            !faults.empty() &&
            std::all_of(
                faults.begin(), faults.end(),
                [](const DetectedFault& fault) {
                    return fault.severity == ControlFaultSeverity::RECOVERABLE &&
                           fault.rule == "model_limit_incompatible";
                });
        if (count_recoverable_cycle && !contains_only_process_excursions) {
            ++consecutive_recoverable_faults_;
        } else if (contains_only_process_excursions) {
            consecutive_recoverable_faults_ = 0;
        }
        if (operational_state_ == OperationalState::NOMINAL) {
            transition_operational_state(
                OperationalState::DEGRADED,
                reason,
                result);
        }
        if (consecutive_recoverable_faults_ >=
            state_policy_.recoverable_faults_before_lockdown) {
            transition_operational_state(
                OperationalState::EMERGENCY_LOCKDOWN,
                "recoverable fault persisted: " + reason,
                result);
            return false;
        }
        return true;
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
    result.events.push_back({
        EdgeEventType::OPERATIONAL_STATE_CHANGED,
        result.start_time_seconds,
        std::string(to_string(previous_state)) + " -> " +
            to_string(next_state) + ": " + reason,
        previous_state,
        next_state,
    });
    if (event_bus_) {
        event_bus_->publish(StateChanged{
            zone_id_, result.start_time_seconds,
            previous_state, next_state, reason});
    }
    if (next_state == OperationalState::EMERGENCY_LOCKDOWN) {
        manual_reset_requested_ = false;
        result.events.push_back({
            EdgeEventType::EMERGENCY_LOCKDOWN_ENTERED,
            result.start_time_seconds,
            reason,
            previous_state,
            next_state,
        });
        if (event_bus_) {
            event_bus_->publish(EmergencyTriggered{
                zone_id_, result.start_time_seconds, reason});
        }
    }
}

}  // namespace smarthydro
