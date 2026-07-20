#include "smarthydro/actuator_simulator.hpp"

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
    const smarthydro::ActuatorSimulator actuators;
    const auto& state = actuators.state();

    check(state.water_pump_percent == 0.0, "water pump does not start stopped");
    check(!state.selected_fertilizer_id.has_value(), "a fertilizer is selected at startup");
    check(state.fertilizer_dosing_percent == 0.0, "fertilizer dosing does not start stopped");
    check(state.lighting_percent == 0.0, "lighting does not start off");
}

void test_actuators_move_towards_targets() {
    smarthydro::ActuatorSimulator actuators;

    actuators.set_water_pump_target_percent(65.0);
    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_dosing_target_percent(12.5);
    actuators.set_lighting_target_percent(80.0);

    check(actuators.state().water_pump_percent == 0.0, "water pump changed before step");
    check(
        actuators.target_state().water_pump_percent == 65.0,
        "water pump target was not stored");

    actuators.step();
    check(actuators.state().water_pump_percent == 20.0, "water pump step is incorrect");
    check(
        actuators.state().fertilizer_dosing_percent == 5.0,
        "fertilizer dosing step is incorrect");
    check(actuators.state().lighting_percent == 25.0, "lighting step is incorrect");

    for (int step = 0; step < 20; ++step) {
        actuators.step();
    }

    check(actuators.state().water_pump_percent == 65.0, "water pump did not reach target");
    check(
        actuators.state().fertilizer_dosing_percent == 12.5,
        "fertilizer dosing did not reach target");
    check(actuators.state().lighting_percent == 80.0, "lighting did not reach target");
}

void test_fertilizer_safety_rules() {
    smarthydro::ActuatorSimulator actuators;

    check_throws<std::logic_error>(
        [&actuators] { actuators.set_fertilizer_dosing_target_percent(10.0); },
        "fertilizer dosing started without a selected fertilizer");

    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_dosing_target_percent(10.0);
    actuators.step();
    actuators.clear_fertilizer_selection();

    check(
        !actuators.state().selected_fertilizer_id.has_value(),
        "fertilizer selection was not cleared");
    check(
        actuators.state().fertilizer_dosing_percent == 0.0,
        "clearing fertilizer did not stop dosing");
}

void test_invalid_commands_and_stop() {
    smarthydro::ActuatorSimulator actuators;

    check_throws<std::invalid_argument>(
        [&actuators] { actuators.set_water_pump_target_percent(101.0); },
        "water pump accepted a value above 100");
    check_throws<std::invalid_argument>(
        [&actuators] { actuators.set_lighting_target_percent(-1.0); },
        "lighting accepted a negative value");
    check_throws<std::invalid_argument>(
        [&actuators] { actuators.select_fertilizer(""); },
        "an empty fertilizer id was accepted");

    actuators.set_water_pump_target_percent(100.0);
    actuators.set_lighting_target_percent(100.0);
    actuators.step();
    actuators.stop_all();

    check(actuators.state().water_pump_percent == 0.0, "stop_all did not stop water pump");
    check(actuators.state().lighting_percent == 0.0, "stop_all did not turn off lighting");
    check(
        actuators.target_state().water_pump_percent == 0.0,
        "stop_all did not clear water pump target");
}

}  // namespace

int main() {
    test_actuators_start_in_safe_state();
    test_actuators_move_towards_targets();
    test_fertilizer_safety_rules();
    test_invalid_commands_and_stop();

    if (failures != 0) {
        std::cerr << failures << " actuator test assertion(s) failed\n";
        return 1;
    }

    std::cout << "All actuator simulator tests passed\n";
    return 0;
}
