#include "smarthydro/actuator_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

void validate_percentage(double value, const char* actuator_name) {
    if (!std::isfinite(value) || value < 0.0 || value > 100.0) {
        throw std::invalid_argument(
            std::string(actuator_name) + " must be between 0 and 100 percent");
    }
}

void validate_positive(double value, const char* field_name) {
    if (!std::isfinite(value) || value <= 0.0) {
        throw std::invalid_argument(std::string(field_name) + " must be positive");
    }
}

}  // namespace

std::size_t fertilizer_index(FertilizerType type) {
    const auto index = static_cast<std::size_t>(type);
    if (index >= kFertilizerTypeCount) {
        throw std::invalid_argument("unknown fertilizer type");
    }
    return index;
}

const char* to_string(FertilizerType type) noexcept {
    switch (type) {
        case FertilizerType::NITROGEN:
            return "nitrogen";
        case FertilizerType::PHOSPHORUS:
            return "phosphorus";
        case FertilizerType::POTASSIUM:
            return "potassium";
        case FertilizerType::PH_UP:
            return "ph-up";
        case FertilizerType::PH_DOWN:
            return "ph-down";
        case FertilizerType::COUNT:
            break;
    }
    return "unknown";
}

ActuatorSimulator::ActuatorSimulator(ActuatorConfig config)
    : config_(std::move(config)) {
    validate_positive(
        config_.water_pump_flow_liters_per_hour,
        "water pump flow");
    validate_positive(
        config_.maximum_irrigation_volume_liters,
        "maximum irrigation volume");
    for (const double flow : config_.fertilizer_flow_milliliters_per_hour) {
        validate_positive(flow, "fertilizer valve flow");
    }
    validate_positive(config_.maximum_lighting_power_watts, "maximum lighting power");
}

const ActuatorCommand& ActuatorSimulator::command() const noexcept {
    return command_;
}

const ActuatorOutput& ActuatorSimulator::output() const noexcept {
    return output_;
}

const ActuatorConfig& ActuatorSimulator::config() const noexcept {
    return config_;
}

void ActuatorSimulator::request_irrigation_volume_liters(double volume_liters) {
    validate_positive(volume_liters, "irrigation volume");
    if (volume_liters > config_.maximum_irrigation_volume_liters) {
        throw std::invalid_argument("irrigation volume exceeds configured maximum");
    }
    if (output_.water_pump_on) {
        throw std::logic_error("an irrigation request is already active");
    }

    command_.requested_irrigation_volume_liters = volume_liters;
    output_.water_pump_on = true;
    output_.water_pump_flow_liters_per_hour =
        config_.water_pump_flow_liters_per_hour;
    output_.irrigation_volume_liters_last_step = 0.0;
    output_.water_pump_on_time_seconds_last_step = 0.0;
    output_.remaining_irrigation_volume_liters = volume_liters;
    output_.fertilizer_volume_milliliters_last_step.fill(0.0);
}

void ActuatorSimulator::cancel_irrigation() noexcept {
    command_.requested_irrigation_volume_liters = 0.0;
    output_.water_pump_on = false;
    output_.water_pump_flow_liters_per_hour = 0.0;
    output_.irrigation_volume_liters_last_step = 0.0;
    output_.water_pump_on_time_seconds_last_step = 0.0;
    output_.remaining_irrigation_volume_liters = 0.0;
    output_.fertilizer_volume_milliliters_last_step.fill(0.0);
    close_fertilizer_valves_preserving_last_step();
}

double ActuatorSimulator::remaining_irrigation_time_seconds() const noexcept {
    return output_.remaining_irrigation_volume_liters /
           config_.water_pump_flow_liters_per_hour * 3600.0;
}

void ActuatorSimulator::set_fertilizer_valve_open(
    FertilizerType type,
    bool open) {
    const auto index = fertilizer_index(type);
    if (!open) {
        command_.fertilizer_valves_open[index] = false;
        output_.fertilizer_valves_open[index] = false;
        output_.fertilizer_flow_milliliters_per_hour[index] = 0.0;
        return;
    }
    if (!output_.water_pump_on) {
        throw std::logic_error(
            "fertilizer valves require an active irrigation");
    }

    if (type == FertilizerType::PH_UP &&
        command_.fertilizer_valves_open[
            fertilizer_index(FertilizerType::PH_DOWN)]) {
        throw std::logic_error("pH up and pH down cannot be open together");
    }
    if (type == FertilizerType::PH_DOWN &&
        command_.fertilizer_valves_open[
            fertilizer_index(FertilizerType::PH_UP)]) {
        throw std::logic_error("pH up and pH down cannot be open together");
    }

    command_.fertilizer_valves_open[index] = true;
    output_.fertilizer_valves_open[index] = true;
    output_.fertilizer_flow_milliliters_per_hour[index] =
        config_.fertilizer_flow_milliliters_per_hour[index];
}

void ActuatorSimulator::close_fertilizer_valves_preserving_last_step() noexcept {
    command_.fertilizer_valves_open.fill(false);
    output_.fertilizer_valves_open.fill(false);
    output_.fertilizer_flow_milliliters_per_hour.fill(0.0);
}

void ActuatorSimulator::close_all_fertilizer_valves() noexcept {
    close_fertilizer_valves_preserving_last_step();
}

bool ActuatorSimulator::fertilizer_valve_command(FertilizerType type) const {
    return command_.fertilizer_valves_open[fertilizer_index(type)];
}

bool ActuatorSimulator::fertilizer_valve_open(FertilizerType type) const {
    return output_.fertilizer_valves_open[fertilizer_index(type)];
}

double ActuatorSimulator::fertilizer_flow_milliliters_per_hour(
    FertilizerType type) const {
    return output_.fertilizer_flow_milliliters_per_hour[
        fertilizer_index(type)];
}

double ActuatorSimulator::fertilizer_volume_milliliters_last_step(
    FertilizerType type) const {
    return output_.fertilizer_volume_milliliters_last_step[
        fertilizer_index(type)];
}

void ActuatorSimulator::set_lighting_command_percent(double value) {
    validate_percentage(value, "lighting command");
    command_.lighting_percent = value;
    output_.lighting_power_watts =
        config_.maximum_lighting_power_watts * value / 100.0;
}

void ActuatorSimulator::step(double delta_time_seconds) {
    validate_positive(delta_time_seconds, "actuator step duration");
    output_.irrigation_volume_liters_last_step = 0.0;
    output_.water_pump_on_time_seconds_last_step = 0.0;
    output_.fertilizer_volume_milliliters_last_step.fill(0.0);
    if (!output_.water_pump_on) {
        return;
    }

    const double on_time_seconds = std::min(
        delta_time_seconds,
        remaining_irrigation_time_seconds());
    const double delivered_volume =
        config_.water_pump_flow_liters_per_hour * on_time_seconds / 3600.0;
    output_.water_pump_on_time_seconds_last_step = on_time_seconds;
    output_.irrigation_volume_liters_last_step = delivered_volume;
    output_.remaining_irrigation_volume_liters -= delivered_volume;

    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        output_.fertilizer_volume_milliliters_last_step[index] =
            output_.fertilizer_flow_milliliters_per_hour[index] *
            on_time_seconds / 3600.0;
    }

    constexpr double kCompletedToleranceLiters = 1e-12;
    if (output_.remaining_irrigation_volume_liters <= kCompletedToleranceLiters) {
        output_.remaining_irrigation_volume_liters = 0.0;
        output_.water_pump_on = false;
        output_.water_pump_flow_liters_per_hour = 0.0;
        close_fertilizer_valves_preserving_last_step();
    }
}

void ActuatorSimulator::stop_all() noexcept {
    command_ = {};
    output_ = {};
}

}  // namespace smarthydro
