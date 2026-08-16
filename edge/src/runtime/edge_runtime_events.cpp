#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

#include <cmath>
#include <stdexcept>
#include <utility>

namespace smarthydro {

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

void EdgeRuntime::set_snapshot_context(
    std::string lifecycle_state,
    double time_scale) {
    if (lifecycle_state.empty()) {
        throw std::invalid_argument(
            "snapshot lifecycle state must not be empty");
    }
    if (!std::isfinite(time_scale) || time_scale <= 0.0) {
        throw std::invalid_argument(
            "snapshot time scale must be finite and positive");
    }
    snapshot_lifecycle_state_ = std::move(lifecycle_state);
    snapshot_time_scale_ = time_scale;
}

void EdgeRuntime::publish_telemetry(
    const EdgeStepResult& result) noexcept {
    if (!event_bus_) {
        return;
    }
    TelemetrySample sample;
    sample.zone_id = zone_id_;
    sample.sequence_number = result.sequence_number;
    sample.timestamp_seconds =
        result.environment_state.simulation_time_seconds;
    sample.operational_state = result.operational_state;
    sample.readings = result.readings;
    sample.actuator_command = result.actuator_command;
    sample.actuator_output = result.actuator_output;
    sample.environment_state = result.environment_state;
    const auto& recipe = control_system_.recipe();
    sample.active_recipe_id = recipe.id;
    sample.active_recipe_version = recipe.version;
    sample.current_phase = result.phase_name;
    sample.lifecycle_state = snapshot_lifecycle_state_;
    sample.time_scale = snapshot_time_scale_;

    const RecipePhase* phase = nullptr;
    for (const auto& candidate : recipe.phases) {
        if (candidate.name == result.phase_name) {
            phase = &candidate;
            break;
        }
    }
    for (std::size_t index = 0;
         index < kControlledVariableCount;
         ++index) {
        sample.current_strategies[index] =
            recipe.controllers[index].selected_strategy;
        if (phase != nullptr) {
            sample.current_setpoints[index] =
                phase->targets[index].setpoint;
        }
    }
    event_bus_->publish(sample);
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
