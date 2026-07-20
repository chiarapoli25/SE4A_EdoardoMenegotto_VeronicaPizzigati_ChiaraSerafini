#include "smarthydro/actuator_manager.hpp"

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

const ActuatorState& ActuatorManager::state() const noexcept {
    return state_;
}

void ActuatorManager::set_water_pump_percent(double value) {
    validate_percentage(value, "water pump");
    state_.water_pump_percent = value;
}

void ActuatorManager::select_fertilizer(const std::string& fertilizer_id) {
    if (fertilizer_id.empty()) {
        throw std::invalid_argument("fertilizer_id must not be empty");
    }
    state_.selected_fertilizer_id = fertilizer_id;
}

void ActuatorManager::clear_fertilizer_selection() noexcept {
    state_.fertilizer_dosing_percent = 0.0;
    state_.selected_fertilizer_id.reset();
}

void ActuatorManager::set_fertilizer_dosing_percent(double value) {
    validate_percentage(value, "fertilizer dosing");
    if (value > 0.0 && !state_.selected_fertilizer_id.has_value()) {
        throw std::logic_error("a fertilizer must be selected before dosing");
    }
    state_.fertilizer_dosing_percent = value;
}

void ActuatorManager::set_lighting_percent(double value) {
    validate_percentage(value, "lighting");
    state_.lighting_percent = value;
}

void ActuatorManager::stop_all() noexcept {
    state_ = {};
}

}  // namespace smarthydro
