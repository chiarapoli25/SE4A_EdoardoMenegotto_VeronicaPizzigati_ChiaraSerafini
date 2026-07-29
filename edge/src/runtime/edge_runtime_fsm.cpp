#include "smarthydro/edge_runtime.hpp"
#include "smarthydro/event_bus.hpp"

#include <cmath>
#include <stdexcept>
#include <string>

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

}  // namespace

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

}  // namespace smarthydro
