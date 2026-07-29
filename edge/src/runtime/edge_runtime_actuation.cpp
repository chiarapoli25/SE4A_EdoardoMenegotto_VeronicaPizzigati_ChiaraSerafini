#include "smarthydro/edge_runtime.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <utility>
#include <vector>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;
constexpr double kEventToleranceSeconds = 1.0e-9;
constexpr double kDeliveredTolerance = 1.0e-12;

}  // namespace

void EdgeRuntime::apply_safe_fallback(
    double delta_time_seconds,
    EdgeStepResult& result,
    bool replace_decisions) {
    actuators_->stop_all();
    result.operational_state = operational_state_;
    if (replace_decisions) {
        for (std::size_t index = 0;
             index < kControlledVariableCount;
             ++index) {
            auto& decision = result.decisions[index];
            decision.status = ControlDecisionStatus::BLOCKED;
            decision.safety_critical = true;
            decision.fault_severity = ControlFaultSeverity::CRITICAL;
            decision.actuator =
                control_system_.recipe().controllers[index].actuator;
            decision.message =
                "runtime is in " +
                std::string(to_string(operational_state_));
        }
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
