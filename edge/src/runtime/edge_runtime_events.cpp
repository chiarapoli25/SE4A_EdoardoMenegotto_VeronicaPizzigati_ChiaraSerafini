#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/edge_runtime.hpp>

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
