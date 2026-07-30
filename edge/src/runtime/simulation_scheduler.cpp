#include <smarthydro/runtime/simulation_scheduler.hpp>

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace smarthydro {
namespace {

bool keeps_schedule(ZoneLifecycleState state) noexcept {
    return state == ZoneLifecycleState::RUNNING ||
           state == ZoneLifecycleState::PAUSED;
}

}  // namespace

SimulationScheduler::SimulationScheduler(
    GreenhouseManager& greenhouse,
    SimulationSchedulerConfig config)
    : greenhouse_(greenhouse),
      config_(std::move(config)) {
    if (!std::isfinite(config_.step_seconds) ||
        config_.step_seconds <= 0.0) {
        throw std::invalid_argument(
            "scheduler step_seconds must be positive and finite");
    }
    if (config_.max_catch_up_steps == 0) {
        throw std::invalid_argument(
            "scheduler max_catch_up_steps must be positive");
    }
}

void SimulationScheduler::accrue(TimePoint now) {
    for (const auto& zone_id : greenhouse_.zone_ids()) {
        const auto state =
            greenhouse_.zone(zone_id).lifecycle_state();
        auto schedule = schedules_.find(zone_id);

        if (!keeps_schedule(state)) {
            if (schedule != schedules_.end()) {
                schedules_.erase(schedule);
            }
            continue;
        }
        if (schedule == schedules_.end()) {
            schedules_.emplace(
                zone_id,
                ZoneSchedule{now, 0.0, false});
            continue;
        }
        if (now < schedule->second.last_real_time) {
            throw std::logic_error(
                "scheduler steady clock moved backwards");
        }

        const double elapsed_real_seconds =
            std::chrono::duration<double>(
                now - schedule->second.last_real_time)
                .count();
        if (state == ZoneLifecycleState::RUNNING) {
            schedule->second.accumulated_simulation_seconds +=
                elapsed_real_seconds *
                greenhouse_.zone(zone_id).time_scale();
        }
        schedule->second.last_real_time = now;
    }
}

void SimulationScheduler::synchronize(TimePoint now) {
    for (const auto& zone_id : greenhouse_.zone_ids()) {
        const auto state =
            greenhouse_.zone(zone_id).lifecycle_state();
        auto schedule = schedules_.find(zone_id);
        if (!keeps_schedule(state)) {
            if (schedule != schedules_.end()) {
                schedules_.erase(schedule);
            }
            continue;
        }
        if (schedule == schedules_.end()) {
            schedules_.emplace(
                zone_id,
                ZoneSchedule{now, 0.0, false});
        }
    }
    if (round_robin_cursor_ >= greenhouse_.size()) {
        round_robin_cursor_ = 0;
    }
}

ScheduledStepResults SimulationScheduler::run_due_steps() {
    ScheduledStepResults results;
    const auto zone_ids = greenhouse_.zone_ids();
    if (zone_ids.empty()) {
        return results;
    }
    if (round_robin_cursor_ >= zone_ids.size()) {
        round_robin_cursor_ = 0;
    }

    std::size_t executed_steps = 0;
    while (executed_steps < config_.max_catch_up_steps) {
        bool found_due_zone = false;
        for (std::size_t offset = 0;
             offset < zone_ids.size();
             ++offset) {
            const std::size_t index =
                (round_robin_cursor_ + offset) %
                zone_ids.size();
            const auto& zone_id = zone_ids[index];
            if (!zone_has_due_step(zone_id)) {
                continue;
            }

            auto& schedule = schedules_.at(zone_id);
            results[zone_id].push_back(
                greenhouse_.step_zone(
                    zone_id,
                    config_.step_seconds));
            schedule.accumulated_simulation_seconds =
                std::max(
                    0.0,
                    schedule.accumulated_simulation_seconds -
                        config_.step_seconds);
            round_robin_cursor_ =
                (index + 1) % zone_ids.size();
            ++executed_steps;
            found_due_zone = true;
            break;
        }
        if (!found_due_zone) {
            break;
        }
    }

    for (auto& [zone_id, schedule] : schedules_) {
        if (!greenhouse_.zone(zone_id).is_running()) {
            continue;
        }
        const bool lagging = pending_steps(schedule) > 0;
        if (lagging != schedule.lagging) {
            publish_lag_transition(
                zone_id,
                schedule,
                lagging);
        }
    }
    return results;
}

double SimulationScheduler::accumulated_simulation_seconds(
    const std::string& zone_id) const noexcept {
    const auto schedule = schedules_.find(zone_id);
    return schedule == schedules_.end()
               ? 0.0
               : schedule->second
                     .accumulated_simulation_seconds;
}

std::size_t SimulationScheduler::pending_step_count(
    const std::string& zone_id) const noexcept {
    const auto schedule = schedules_.find(zone_id);
    return schedule == schedules_.end()
               ? 0
               : pending_steps(schedule->second);
}

const SimulationSchedulerConfig&
SimulationScheduler::config() const noexcept {
    return config_;
}

bool SimulationScheduler::zone_has_due_step(
    const std::string& zone_id) const {
    const auto schedule = schedules_.find(zone_id);
    if (schedule == schedules_.end() ||
        !greenhouse_.zone(zone_id).is_running()) {
        return false;
    }
    const double tolerance =
        config_.step_seconds * 1.0e-12;
    return schedule->second.accumulated_simulation_seconds +
               tolerance >=
           config_.step_seconds;
}

std::size_t SimulationScheduler::pending_steps(
    const ZoneSchedule& schedule) const noexcept {
    const double tolerance =
        config_.step_seconds * 1.0e-12;
    if (schedule.accumulated_simulation_seconds +
            tolerance <
        config_.step_seconds) {
        return 0;
    }
    return static_cast<std::size_t>(
        std::floor(
            (schedule.accumulated_simulation_seconds +
             tolerance) /
            config_.step_seconds));
}

void SimulationScheduler::publish_lag_transition(
    const std::string& zone_id,
    ZoneSchedule& schedule,
    bool lagging) noexcept {
    schedule.lagging = lagging;
    try {
        const auto& zone = greenhouse_.zone(zone_id);
        greenhouse_.event_bus()->publish(
            SchedulerLagStateChanged{
                zone_id,
                zone.runtime()
                    .environment_state()
                    .simulation_time_seconds,
                lagging,
                schedule.accumulated_simulation_seconds,
                pending_steps(schedule),
                zone.time_scale(),
            });
    } catch (...) {
        // Il controllo temporale locale non dipende dagli observer esterni.
    }
}

}  // namespace smarthydro
