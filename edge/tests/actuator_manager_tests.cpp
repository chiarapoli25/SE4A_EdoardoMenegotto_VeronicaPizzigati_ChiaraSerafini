#include "smarthydro/actuator_manager.hpp"

#include <iostream>
#include <stdexcept>

namespace {

int failures = 0;

void check(bool condition, const char* message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

template <typename Exception, typename Action>
void check_throws(Action action, const char* message) {
    try {
        action();
        check(false, message);
    } catch (const Exception&) {
        check(true, message);
    } catch (...) {
        check(false, message);
    }
}

void test_actuators_start_in_safe_state() {
    const smarthydro::ActuatorManager actuators;
    const auto& state = actuators.state();

    check(state.water_pump_percent == 0.0, "water pump does not start stopped");
    check(!state.selected_fertilizer_id.has_value(), "a fertilizer is selected at startup");
    check(state.fertilizer_dosing_percent == 0.0, "fertilizer dosing does not start stopped");
    check(state.lighting_percent == 0.0, "lighting does not start off");
}

void test_actuator_commands_update_state() {
    smarthydro::ActuatorManager actuators;

    actuators.set_water_pump_percent(65.0);
    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_dosing_percent(12.5);
    actuators.set_lighting_percent(80.0);

    const auto& state = actuators.state();
    check(state.water_pump_percent == 65.0, "water pump command was not stored");
    check(
        state.selected_fertilizer_id == "tomato-growth",
        "fertilizer selection was not stored");
    check(state.fertilizer_dosing_percent == 12.5, "fertilizer dosing was not stored");
    check(state.lighting_percent == 80.0, "lighting command was not stored");

    actuators.stop_all();
    check(actuators.state().water_pump_percent == 0.0, "stop_all did not stop the water pump");
    check(
        !actuators.state().selected_fertilizer_id.has_value(),
        "stop_all did not clear the fertilizer selection");
    check(
        actuators.state().fertilizer_dosing_percent == 0.0,
        "stop_all did not stop fertilizer dosing");
    check(actuators.state().lighting_percent == 0.0, "stop_all did not turn off lighting");
}

void test_invalid_commands_are_rejected() {
    smarthydro::ActuatorManager actuators;

    check_throws<std::invalid_argument>(
        [&actuators] { actuators.set_water_pump_percent(101.0); },
        "water pump accepted a value above 100");
    check_throws<std::invalid_argument>(
        [&actuators] { actuators.set_lighting_percent(-1.0); },
        "lighting accepted a negative value");
    check_throws<std::invalid_argument>(
        [&actuators] { actuators.select_fertilizer(""); },
        "an empty fertilizer id was accepted");
    check_throws<std::logic_error>(
        [&actuators] { actuators.set_fertilizer_dosing_percent(10.0); },
        "fertilizer dosing started without a selected fertilizer");
}

}  // namespace

int main() {
    test_actuators_start_in_safe_state();
    test_actuator_commands_update_state();
    test_invalid_commands_are_rejected();

    if (failures != 0) {
        std::cerr << failures << " actuator test assertion(s) failed\n";
        return 1;
    }

    std::cout << "All actuator manager tests passed\n";
    return 0;
}
