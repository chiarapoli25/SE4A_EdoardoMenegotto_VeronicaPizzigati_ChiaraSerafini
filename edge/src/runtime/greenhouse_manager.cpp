#include <smarthydro/runtime/greenhouse_manager.hpp>

#include <algorithm>
#include <cmath>
#include <exception>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace smarthydro {

const char* to_string(ZoneLifecycleState state) noexcept {
    switch (state) {
        case ZoneLifecycleState::IDLE:
            return "Idle";
        case ZoneLifecycleState::STARTING:
            return "Starting";
        case ZoneLifecycleState::RUNNING:
            return "Running";
        case ZoneLifecycleState::PAUSED:
            return "Paused";
        case ZoneLifecycleState::STOPPING:
            return "Stopping";
        case ZoneLifecycleState::ERROR:
            return "Error";
    }
    return "Unknown";
}

void ZoneController::transition_lifecycle(
    ZoneLifecycleState next_state,
    std::string reason) noexcept {
    const auto previous_state = lifecycle_state_;
    if (previous_state == next_state) {
        return;
    }
    lifecycle_state_ = next_state;
    if (!event_bus_) {
        return;
    }
    try {
        const double timestamp_seconds =
            runtime_
                ? runtime_->environment_state().simulation_time_seconds
                : 0.0;
        event_bus_->publish(
            ZoneLifecycleChanged{
                zone_id_,
                timestamp_seconds,
                to_string(previous_state),
                to_string(next_state),
                std::move(reason),
            });
    } catch (...) {
        // Il lifecycle locale non dipende dagli observer esterni.
    }
}

std::string ZoneController::require_zone_id(std::string zone_id) {
    if (zone_id.empty()) {
        throw std::invalid_argument(
            "zone controller identifier must not be empty");
    }
    return zone_id;
}

ZoneController::ZoneController(
    std::string zone_id,
    std::shared_ptr<EventBus> event_bus)
    : zone_id_(require_zone_id(std::move(zone_id))),
      event_bus_(std::move(event_bus)) {}

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
      lifecycle_state_(ZoneLifecycleState::RUNNING),
      runtime_(std::make_unique<EdgeRuntime>(
          std::move(recipe),
          std::move(actuator_config),
          std::move(environment_config),
          std::move(sensor_config),
          environment_seed,
          sensor_seed,
          state_policy)),
      command_processor_(
          std::make_unique<RuntimeCommandProcessor>(*runtime_)) {
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
      lifecycle_state_(ZoneLifecycleState::RUNNING),
      runtime_(std::make_unique<EdgeRuntime>(
          std::move(recipe),
          std::move(sensors),
          std::move(actuators),
          std::move(environment),
          state_policy)),
      command_processor_(
          std::make_unique<RuntimeCommandProcessor>(*runtime_)) {
    if (event_bus) {
        attach_event_bus(std::move(event_bus));
    }
}

const std::string& ZoneController::id() const noexcept {
    return zone_id_;
}

ZoneLifecycleState ZoneController::lifecycle_state() const noexcept {
    return lifecycle_state_;
}

bool ZoneController::is_active() const noexcept {
    return runtime_ != nullptr;
}

bool ZoneController::is_running() const noexcept {
    return lifecycle_state_ == ZoneLifecycleState::RUNNING;
}

const std::string& ZoneController::cultivation_id() const noexcept {
    return cultivation_id_;
}

const std::string& ZoneController::last_error() const noexcept {
    return last_error_;
}

double ZoneController::time_scale() const noexcept {
    return time_scale_;
}

void ZoneController::set_time_scale(double time_scale) {
    if (!std::isfinite(time_scale) ||
        time_scale < kMinimumSimulationTimeScale ||
        time_scale > kMaximumSimulationTimeScale) {
        throw std::invalid_argument(
            "time_scale must be finite and in [1, 60]");
    }
    if (
        lifecycle_state_ != ZoneLifecycleState::RUNNING &&
        lifecycle_state_ != ZoneLifecycleState::PAUSED) {
        throw std::logic_error(
            "zone cannot change simulation speed while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }
    const double previous_time_scale = time_scale_;
    time_scale_ = time_scale;
    if (previous_time_scale != time_scale_) {
        publish_time_scale_changed(
            previous_time_scale,
            time_scale_);
    }
}

std::optional<double>
ZoneController::simulation_duration_seconds() const noexcept {
    return simulation_duration_seconds_;
}

std::optional<double>
ZoneController::simulation_target_timestamp_seconds() const noexcept {
    return simulation_target_timestamp_seconds_;
}

std::optional<double>
ZoneController::remaining_simulation_seconds() const noexcept {
    if (!simulation_target_timestamp_seconds_ || !runtime_) {
        return std::nullopt;
    }
    return std::max(
        0.0,
        *simulation_target_timestamp_seconds_ -
            runtime_->environment_state().simulation_time_seconds);
}

void ZoneController::set_simulation_duration(
    std::optional<double> duration_seconds) {
    if (
        lifecycle_state_ != ZoneLifecycleState::RUNNING &&
        lifecycle_state_ != ZoneLifecycleState::PAUSED) {
        throw std::logic_error(
            "zone cannot change simulation duration while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }
    if (
        duration_seconds &&
        (!std::isfinite(*duration_seconds) ||
         *duration_seconds <= 0.0)) {
        throw std::invalid_argument(
            "duration_seconds must be positive and finite");
    }

    const double timestamp_seconds =
        runtime().environment_state().simulation_time_seconds;
    if (duration_seconds) {
        const double target =
            timestamp_seconds + *duration_seconds;
        if (!std::isfinite(target)) {
            throw std::invalid_argument(
                "simulation duration target must be finite");
        }
        simulation_duration_seconds_ = duration_seconds;
        simulation_target_timestamp_seconds_ = target;
    } else {
        simulation_duration_seconds_.reset();
        simulation_target_timestamp_seconds_.reset();
    }
    publish_simulation_duration_changed();
}

void ZoneController::complete_simulation_duration() {
    if (!is_running() ||
        !simulation_duration_seconds_ ||
        !simulation_target_timestamp_seconds_) {
        throw std::logic_error(
            "zone has no running simulation duration to complete");
    }
    const double timestamp_seconds =
        runtime().environment_state().simulation_time_seconds;
    const double tolerance =
        std::max(
            1.0,
            std::abs(*simulation_target_timestamp_seconds_)) *
        1.0e-12;
    if (timestamp_seconds + tolerance <
        *simulation_target_timestamp_seconds_) {
        throw std::logic_error(
            "simulation duration target has not been reached");
    }

    runtime().stop_all_actuators();
    publish_simulation_duration_completed(
        *simulation_duration_seconds_);
    transition_lifecycle(
        ZoneLifecycleState::PAUSED,
        "configured simulation duration completed");
}

EdgeRuntime& ZoneController::runtime() {
    if (!runtime_) {
        throw std::logic_error(
            "greenhouse zone is inactive: " + zone_id_);
    }
    return *runtime_;
}

const EdgeRuntime& ZoneController::runtime() const {
    if (!runtime_) {
        throw std::logic_error(
            "greenhouse zone is inactive: " + zone_id_);
    }
    return *runtime_;
}

void ZoneController::confirm_all_configurations() {
    runtime().confirm_all_configurations();
}

EdgeStepResult ZoneController::step(double delta_time_seconds) {
    if (!is_running()) {
        throw std::logic_error(
            "greenhouse zone is not running: " +
            std::string(to_string(lifecycle_state_)));
    }
    return runtime().step(delta_time_seconds);
}

RuntimeCommandResult ZoneController::execute_command(
    const RuntimeCommandEnvelope& envelope) {
    if (envelope.command_id.empty()) {
        return {
            envelope.command_id,
            runtime_command_type(envelope.command),
            RuntimeCommandStatus::REJECTED,
            false,
            "command_id must not be empty",
        };
    }

    std::lock_guard<std::mutex> lock(command_mutex_);
    const auto previous = command_results_.find(envelope.command_id);
    if (previous != command_results_.end()) {
        auto replay = previous->second;
        replay.replayed = true;
        return replay;
    }

    auto result = execute_command_once(envelope);
    command_results_.emplace(envelope.command_id, result);
    return result;
}

RuntimeCommandResult ZoneController::execute_command_once(
    const RuntimeCommandEnvelope& envelope) noexcept {
    try {
        if (const auto* activation =
                std::get_if<ActivateCultivationCommand>(
                    &envelope.command)) {
            activate_cultivation(
                activation->cultivation_id,
                activation->recipe);
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                "cultivation activated",
            };
        }
        if (std::holds_alternative<PauseCultivationCommand>(
                envelope.command)) {
            pause_cultivation();
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                "cultivation paused",
            };
        }
        if (std::holds_alternative<ResumeCultivationCommand>(
                envelope.command)) {
            resume_cultivation();
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                "cultivation resumed",
            };
        }
        if (std::holds_alternative<StopCultivationCommand>(
                envelope.command)) {
            stop_cultivation();
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                "cultivation stopped",
            };
        }
        if (const auto* speed =
                std::get_if<SetSimulationSpeedCommand>(
                    &envelope.command)) {
            set_time_scale(speed->time_scale);
            std::ostringstream message;
            message << "simulation speed set to "
                    << time_scale_ << 'x';
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                message.str(),
            };
        }
        if (const auto* duration =
                std::get_if<SetSimulationDurationCommand>(
                    &envelope.command)) {
            set_simulation_duration(duration->duration_seconds);
            std::ostringstream message;
            if (duration->duration_seconds) {
                message << "simulation duration set to "
                        << *duration->duration_seconds << 's';
            } else {
                message << "simulation duration cleared";
            }
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::SUCCEEDED,
                false,
                message.str(),
            };
        }
        if (!runtime_ || !command_processor_) {
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::REJECTED,
                false,
                "zone is inactive; ActivateCultivation is required",
            };
        }
        if (!is_running()) {
            return {
                envelope.command_id,
                runtime_command_type(envelope.command),
                RuntimeCommandStatus::REJECTED,
                false,
                "zone lifecycle is " +
                    std::string(to_string(lifecycle_state_)) +
                    "; ResumeCultivation is required",
            };
        }
        return command_processor_->execute(envelope);
    } catch (const std::exception& error) {
        return {
            envelope.command_id,
            runtime_command_type(envelope.command),
            RuntimeCommandStatus::REJECTED,
            false,
            error.what(),
        };
    } catch (...) {
        return {
            envelope.command_id,
            runtime_command_type(envelope.command),
            RuntimeCommandStatus::REJECTED,
            false,
            "unknown zone command execution failure",
        };
    }
}

void ZoneController::activate_cultivation(
    std::string cultivation_id,
    Recipe recipe) {
    if (
        lifecycle_state_ != ZoneLifecycleState::IDLE &&
        lifecycle_state_ != ZoneLifecycleState::ERROR) {
        throw std::logic_error(
            "zone cannot start a cultivation while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }
    if (cultivation_id.empty()) {
        throw std::invalid_argument(
            "cultivation_id must not be empty");
    }

    last_error_.clear();
    time_scale_ = 1.0;
    simulation_duration_seconds_.reset();
    simulation_target_timestamp_seconds_.reset();
    cultivation_id_ = std::move(cultivation_id);
    transition_lifecycle(
        ZoneLifecycleState::STARTING,
        "cultivation activation requested");
    try {
        auto candidate =
            std::make_unique<EdgeRuntime>(std::move(recipe));
        if (event_bus_) {
            candidate->attach_event_bus(event_bus_, zone_id_);
        }
        candidate->confirm_all_configurations();

        runtime_ = std::move(candidate);
        command_processor_ =
            std::make_unique<RuntimeCommandProcessor>(*runtime_);
        transition_lifecycle(
            ZoneLifecycleState::RUNNING,
            "cultivation runtime ready");
    } catch (const std::exception& error) {
        command_processor_.reset();
        runtime_.reset();
        last_error_ = error.what();
        transition_lifecycle(
            ZoneLifecycleState::ERROR,
            last_error_);
        throw;
    } catch (...) {
        command_processor_.reset();
        runtime_.reset();
        last_error_ = "unknown cultivation activation failure";
        transition_lifecycle(
            ZoneLifecycleState::ERROR,
            last_error_);
        throw;
    }
}

void ZoneController::pause_cultivation() {
    if (lifecycle_state_ != ZoneLifecycleState::RUNNING) {
        throw std::logic_error(
            "zone cannot pause while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }
    runtime().stop_all_actuators();
    transition_lifecycle(
        ZoneLifecycleState::PAUSED,
        "cultivation paused by command");
}

void ZoneController::resume_cultivation() {
    if (lifecycle_state_ != ZoneLifecycleState::PAUSED) {
        throw std::logic_error(
            "zone cannot resume while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }
    if (
        simulation_target_timestamp_seconds_ &&
        remaining_simulation_seconds().value_or(0.0) <= 0.0) {
        throw std::logic_error(
            "simulation duration is complete; set a new duration or clear the limit");
    }
    transition_lifecycle(
        ZoneLifecycleState::RUNNING,
        "cultivation resumed by command");
}

void ZoneController::stop_cultivation() {
    if (lifecycle_state_ == ZoneLifecycleState::IDLE) {
        return;
    }
    if (
        lifecycle_state_ == ZoneLifecycleState::STARTING ||
        lifecycle_state_ == ZoneLifecycleState::STOPPING) {
        throw std::logic_error(
            "zone cannot stop while lifecycle is " +
            std::string(to_string(lifecycle_state_)));
    }

    transition_lifecycle(
        ZoneLifecycleState::STOPPING,
        "cultivation stop requested");
    if (runtime_) {
        runtime_->stop_all_actuators();
    }
    command_processor_.reset();
    runtime_.reset();
    cultivation_id_.clear();
    last_error_.clear();
    time_scale_ = 1.0;
    simulation_duration_seconds_.reset();
    simulation_target_timestamp_seconds_.reset();
    transition_lifecycle(
        ZoneLifecycleState::IDLE,
        "cultivation runtime released");
}

void ZoneController::publish_time_scale_changed(
    double previous_time_scale,
    double current_time_scale) noexcept {
    if (!event_bus_) {
        return;
    }
    try {
        event_bus_->publish(
            SimulationSpeedChanged{
                zone_id_,
                runtime_
                    ? runtime_->environment_state()
                          .simulation_time_seconds
                    : 0.0,
                previous_time_scale,
                current_time_scale,
            });
    } catch (...) {
        // La velocita locale non dipende dagli observer esterni.
    }
}

void ZoneController::publish_simulation_duration_changed() noexcept {
    if (!event_bus_ || !runtime_) {
        return;
    }
    try {
        event_bus_->publish(
            SimulationDurationChanged{
                zone_id_,
                runtime_->environment_state()
                    .simulation_time_seconds,
                simulation_duration_seconds_.has_value(),
                simulation_duration_seconds_.value_or(0.0),
                simulation_target_timestamp_seconds_.value_or(0.0),
            });
    } catch (...) {
        // La configurazione locale non dipende dagli observer esterni.
    }
}

void ZoneController::publish_simulation_duration_completed(
    double duration_seconds) noexcept {
    if (!event_bus_ || !runtime_) {
        return;
    }
    try {
        event_bus_->publish(
            SimulationDurationCompleted{
                zone_id_,
                runtime_->environment_state()
                    .simulation_time_seconds,
                duration_seconds,
            });
    } catch (...) {
        // Il completamento locale non dipende dagli observer esterni.
    }
}

void ZoneController::attach_event_bus(
    std::shared_ptr<EventBus> event_bus) {
    if (!event_bus) {
        throw std::invalid_argument(
            "zone controller requires a non-null event bus");
    }
    event_bus_ = std::move(event_bus);
    if (runtime_) {
        runtime_->attach_event_bus(event_bus_, zone_id_);
    }
}

GreenhouseManager::GreenhouseManager(
    std::shared_ptr<EventBus> event_bus)
    : event_bus_(
          event_bus
              ? std::move(event_bus)
              : std::make_shared<EventBus>()) {}

ZoneController& GreenhouseManager::add_inactive_zone(
    std::string zone_id) {
    return add_zone(
        std::make_unique<ZoneController>(std::move(zone_id)));
}

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
        if (!controller->is_running()) {
            continue;
        }
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
