#include "smarthydro/actuator_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace smarthydro {
namespace {

constexpr double kWaterPumpStepPercent = 20.0;
constexpr double kFertilizerDosingStepPercent = 5.0;
constexpr double kLightingStepPercent = 25.0;

void validate_percentage(double value, const char* actuator_name) {
    if (!std::isfinite(value) || value < 0.0 || value > 100.0) {
        throw std::invalid_argument(
            std::string(actuator_name) + " must be between 0 and 100 percent");
    }
}

double move_towards(double current, double target, double maximum_step) noexcept {
    if (current < target) {
        return std::min(current + maximum_step, target);
    }
    if (current > target) {
        return std::max(current - maximum_step, target);
    }
    return current;
}

}  // namespace

const ActuatorState& ActuatorSimulator::state() const noexcept {
    return state_;
}

const ActuatorState& ActuatorSimulator::target_state() const noexcept {
    return target_state_;
}

void ActuatorSimulator::set_water_pump_target_percent(double value) {
    validate_percentage(value, "water pump target");
    target_state_.water_pump_percent = value;
}

void ActuatorSimulator::select_fertilizer(const std::string& fertilizer_id) {
    if (fertilizer_id.empty()) {
        throw std::invalid_argument("fertilizer_id must not be empty");
    }
    state_.selected_fertilizer_id = fertilizer_id;
    target_state_.selected_fertilizer_id = fertilizer_id;
}

void ActuatorSimulator::clear_fertilizer_selection() noexcept {
    state_.fertilizer_dosing_percent = 0.0;
    target_state_.fertilizer_dosing_percent = 0.0;
    state_.selected_fertilizer_id.reset();
    target_state_.selected_fertilizer_id.reset();
}

void ActuatorSimulator::set_fertilizer_dosing_target_percent(double value) {
    validate_percentage(value, "fertilizer dosing target");
    if (value > 0.0 && !target_state_.selected_fertilizer_id.has_value()) {
        throw std::logic_error("a fertilizer must be selected before dosing");
    }
    target_state_.fertilizer_dosing_percent = value;
}

void ActuatorSimulator::set_lighting_target_percent(double value) {
    validate_percentage(value, "lighting target");
    target_state_.lighting_percent = value;
}

void ActuatorSimulator::step() noexcept {
    state_.water_pump_percent = move_towards(
        state_.water_pump_percent,
        target_state_.water_pump_percent,
        kWaterPumpStepPercent);
    state_.fertilizer_dosing_percent = move_towards(
        state_.fertilizer_dosing_percent,
        target_state_.fertilizer_dosing_percent,
        kFertilizerDosingStepPercent);
    state_.lighting_percent = move_towards(
        state_.lighting_percent,
        target_state_.lighting_percent,
        kLightingStepPercent);
}

void ActuatorSimulator::stop_all() noexcept {
    state_ = {};
    target_state_ = {};
}

}  // namespace smarthydro
