#include "smarthydro/actuator_simulator.hpp"

#include <cmath>
#include <stdexcept>

namespace smarthydro {
namespace {

void validate_percentage(double value, const char* actuator_name) {
    if (!std::isfinite(value) || value < 0.0 || value > 100.0) {
        throw std::invalid_argument(
            std::string(actuator_name) + " must be between 0 and 100 percent");
    }
}

}  // namespace

const ActuatorState& ActuatorSimulator::state() const noexcept {
    return state_;
}

void ActuatorSimulator::set_water_pump_percent(double value) {
    validate_percentage(value, "water pump command");
    state_.water_pump_percent = value;
}

void ActuatorSimulator::select_fertilizer(const std::string& fertilizer_id) {
    if (fertilizer_id.empty()) {
        throw std::invalid_argument("fertilizer_id must not be empty");
    }
    state_.selected_fertilizer_id = fertilizer_id;
}

void ActuatorSimulator::clear_fertilizer_selection() noexcept {
    state_.fertilizer_dosing_percent = 0.0;
    state_.selected_fertilizer_id.reset();
}

void ActuatorSimulator::set_fertilizer_dosing_percent(double value) {
    validate_percentage(value, "fertilizer dosing command");
    if (value > 0.0 && !state_.selected_fertilizer_id.has_value()) {
        throw std::logic_error("a fertilizer must be selected before dosing");
    }
    state_.fertilizer_dosing_percent = value;
}

void ActuatorSimulator::set_lighting_percent(double value) {
    validate_percentage(value, "lighting command");
    state_.lighting_percent = value;
}

void ActuatorSimulator::stop_all() noexcept {
    state_ = {};
}

}  // namespace smarthydro
