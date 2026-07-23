#include "smarthydro/actuator_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
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

ActuatorSimulator::ActuatorSimulator(ActuatorConfig config)
    : config_(std::move(config)) {
    validate_positive(
        config_.water_pump_flow_liters_per_hour,
        "water pump flow");
    validate_positive(
        config_.maximum_irrigation_volume_liters,
        "maximum irrigation volume");
    validate_positive(
        config_.maximum_fertilizer_flow_milliliters_per_hour,
        "maximum fertilizer flow");
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
}

void ActuatorSimulator::cancel_irrigation() noexcept {
    command_.requested_irrigation_volume_liters = 0.0;
    output_.water_pump_on = false;
    output_.water_pump_flow_liters_per_hour = 0.0;
    output_.irrigation_volume_liters_last_step = 0.0;
    output_.water_pump_on_time_seconds_last_step = 0.0;
    output_.remaining_irrigation_volume_liters = 0.0;
}

double ActuatorSimulator::remaining_irrigation_time_seconds() const noexcept {
    return output_.remaining_irrigation_volume_liters /
           config_.water_pump_flow_liters_per_hour * 3600.0;
}

void ActuatorSimulator::select_fertilizer(const std::string& fertilizer_id) {
    if (fertilizer_id.empty()) {
        throw std::invalid_argument("fertilizer_id must not be empty");
    }
    output_.selected_fertilizer_id = fertilizer_id;
}

void ActuatorSimulator::clear_fertilizer_selection() noexcept {
    command_.fertilizer_doser_percent = 0.0;
    output_.fertilizer_flow_milliliters_per_hour = 0.0;
    output_.selected_fertilizer_id.reset();
}

void ActuatorSimulator::set_fertilizer_doser_command_percent(double value) {
    validate_percentage(value, "fertilizer dosing command");
    if (value > 0.0 && !output_.selected_fertilizer_id.has_value()) {
        throw std::logic_error("a fertilizer must be selected before dosing");
    }
    command_.fertilizer_doser_percent = value;
    output_.fertilizer_flow_milliliters_per_hour =
        config_.maximum_fertilizer_flow_milliliters_per_hour * value / 100.0;
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

    constexpr double kCompletedToleranceLiters = 1e-12;
    if (output_.remaining_irrigation_volume_liters <= kCompletedToleranceLiters) {
        output_.remaining_irrigation_volume_liters = 0.0;
        output_.water_pump_on = false;
        output_.water_pump_flow_liters_per_hour = 0.0;
    }
}

void ActuatorSimulator::stop_all() noexcept {
    command_ = {};
    output_ = {};
}

}  // namespace smarthydro
