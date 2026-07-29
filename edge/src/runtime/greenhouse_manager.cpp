#include "smarthydro/greenhouse_manager.hpp"

#include <stdexcept>
#include <utility>

namespace smarthydro {

std::string ZoneController::require_zone_id(std::string zone_id) {
    if (zone_id.empty()) {
        throw std::invalid_argument(
            "zone controller identifier must not be empty");
    }
    return zone_id;
}

ZoneController::ZoneController(
    std::string zone_id,
    Recipe recipe,
    ActuatorConfig actuator_config,
    EnvironmentConfig environment_config,
    SensorConfig sensor_config,
    std::uint32_t environment_seed,
    std::uint32_t sensor_seed,
    OperationalStatePolicy state_policy,
    std::shared_ptr<EventBus> event_bus)
    : zone_id_(require_zone_id(std::move(zone_id))),
      runtime_(
          std::move(recipe),
          std::move(actuator_config),
          std::move(environment_config),
          std::move(sensor_config),
          environment_seed,
          sensor_seed,
          state_policy),
      command_processor_(runtime_) {
    if (event_bus) {
        attach_event_bus(std::move(event_bus));
    }
}

ZoneController::ZoneController(
    std::string zone_id,
    Recipe recipe,
    SensorAdapterArray sensors,
    std::unique_ptr<IActuator> actuators,
    std::unique_ptr<IEnvironment> environment,
    OperationalStatePolicy state_policy,
    std::shared_ptr<EventBus> event_bus)
    : zone_id_(require_zone_id(std::move(zone_id))),
      runtime_(
          std::move(recipe),
          std::move(sensors),
          std::move(actuators),
          std::move(environment),
          state_policy),
      command_processor_(runtime_) {
    if (event_bus) {
        attach_event_bus(std::move(event_bus));
    }
}

const std::string& ZoneController::id() const noexcept {
    return zone_id_;
}

EdgeRuntime& ZoneController::runtime() noexcept {
    return runtime_;
}

const EdgeRuntime& ZoneController::runtime() const noexcept {
    return runtime_;
}

void ZoneController::confirm_all_configurations() {
    runtime_.confirm_all_configurations();
}

EdgeStepResult ZoneController::step(double delta_time_seconds) {
    return runtime_.step(delta_time_seconds);
}

RuntimeCommandResult ZoneController::execute_command(
    const RuntimeCommandEnvelope& envelope) {
    return command_processor_.execute(envelope);
}

void ZoneController::attach_event_bus(
    std::shared_ptr<EventBus> event_bus) {
    runtime_.attach_event_bus(std::move(event_bus), zone_id_);
}

GreenhouseManager::GreenhouseManager(
    std::shared_ptr<EventBus> event_bus)
    : event_bus_(
          event_bus
              ? std::move(event_bus)
              : std::make_shared<EventBus>()) {}

ZoneController& GreenhouseManager::add_simulated_zone(
    std::string zone_id,
    Recipe recipe,
    ActuatorConfig actuator_config,
    EnvironmentConfig environment_config,
    SensorConfig sensor_config,
    std::uint32_t environment_seed,
    std::uint32_t sensor_seed,
    OperationalStatePolicy state_policy) {
    return add_zone(
        std::make_unique<ZoneController>(
            std::move(zone_id),
            std::move(recipe),
            std::move(actuator_config),
            std::move(environment_config),
            std::move(sensor_config),
            environment_seed,
            sensor_seed,
            state_policy));
}

ZoneController& GreenhouseManager::add_zone(
    std::unique_ptr<ZoneController> zone) {
    if (!zone) {
        throw std::invalid_argument(
            "greenhouse manager requires a non-null zone");
    }
    const auto zone_id = zone->id();
    if (contains(zone_id)) {
        throw std::invalid_argument(
            "duplicate greenhouse zone identifier: " + zone_id);
    }
    zone->attach_event_bus(event_bus_);
    auto [iterator, inserted] =
        zones_.emplace(zone_id, std::move(zone));
    if (!inserted) {
        throw std::runtime_error(
            "failed to register greenhouse zone: " + zone_id);
    }
    return *iterator->second;
}

bool GreenhouseManager::contains(
    const std::string& zone_id) const noexcept {
    return zones_.find(zone_id) != zones_.end();
}

std::size_t GreenhouseManager::size() const noexcept {
    return zones_.size();
}

std::vector<std::string> GreenhouseManager::zone_ids() const {
    std::vector<std::string> identifiers;
    identifiers.reserve(zones_.size());
    for (const auto& [zone_id, zone] : zones_) {
        static_cast<void>(zone);
        identifiers.push_back(zone_id);
    }
    return identifiers;
}

ZoneController& GreenhouseManager::zone(const std::string& zone_id) {
    const auto iterator = zones_.find(zone_id);
    if (iterator == zones_.end()) {
        throw std::out_of_range(
            "unknown greenhouse zone: " + zone_id);
    }
    return *iterator->second;
}

const ZoneController& GreenhouseManager::zone(
    const std::string& zone_id) const {
    const auto iterator = zones_.find(zone_id);
    if (iterator == zones_.end()) {
        throw std::out_of_range(
            "unknown greenhouse zone: " + zone_id);
    }
    return *iterator->second;
}

EdgeStepResult GreenhouseManager::step_zone(
    const std::string& zone_id,
    double delta_time_seconds) {
    return zone(zone_id).step(delta_time_seconds);
}

GreenhouseStepResults GreenhouseManager::step_all(
    double delta_time_seconds) {
    GreenhouseStepResults results;
    for (auto& [zone_id, controller] : zones_) {
        results.emplace(
            zone_id,
            controller->step(delta_time_seconds));
    }
    return results;
}

RuntimeCommandResult GreenhouseManager::execute_command(
    const std::string& zone_id,
    const RuntimeCommandEnvelope& envelope) {
    return zone(zone_id).execute_command(envelope);
}

const std::shared_ptr<EventBus>&
GreenhouseManager::event_bus() const noexcept {
    return event_bus_;
}

}  // namespace smarthydro
