#include "smarthydro/actuator_simulator.hpp"

#include <stdexcept>

#include <gtest/gtest.h>

namespace {

TEST(ActuatorSimulatorTest, StartsInSafeState) {
    const smarthydro::ActuatorSimulator actuators;
    const auto& state = actuators.state();

    EXPECT_DOUBLE_EQ(state.water_pump_percent, 0.0);
    EXPECT_FALSE(state.selected_fertilizer_id.has_value());
    EXPECT_DOUBLE_EQ(state.fertilizer_dosing_percent, 0.0);
    EXPECT_DOUBLE_EQ(state.lighting_percent, 0.0);
}

TEST(ActuatorSimulatorTest, AppliesCommandsImmediately) {
    smarthydro::ActuatorSimulator actuators;

    actuators.set_water_pump_percent(65.0);
    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_dosing_percent(12.5);
    actuators.set_lighting_percent(80.0);

    EXPECT_DOUBLE_EQ(actuators.state().water_pump_percent, 65.0);
    EXPECT_DOUBLE_EQ(actuators.state().fertilizer_dosing_percent, 12.5);
    EXPECT_DOUBLE_EQ(actuators.state().lighting_percent, 80.0);
}

TEST(ActuatorSimulatorTest, EnforcesFertilizerSafetyRules) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(
        actuators.set_fertilizer_dosing_percent(10.0),
        std::logic_error);

    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_dosing_percent(10.0);
    actuators.clear_fertilizer_selection();

    EXPECT_FALSE(actuators.state().selected_fertilizer_id.has_value());
    EXPECT_DOUBLE_EQ(actuators.state().fertilizer_dosing_percent, 0.0);
}

TEST(ActuatorSimulatorTest, RejectsInvalidCommands) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(actuators.set_water_pump_percent(101.0), std::invalid_argument);
    EXPECT_THROW(actuators.set_lighting_percent(-1.0), std::invalid_argument);
    EXPECT_THROW(actuators.select_fertilizer(""), std::invalid_argument);
}

TEST(ActuatorSimulatorTest, StopAllClearsState) {
    smarthydro::ActuatorSimulator actuators;
    actuators.set_water_pump_percent(100.0);
    actuators.set_lighting_percent(100.0);

    actuators.stop_all();

    EXPECT_DOUBLE_EQ(actuators.state().water_pump_percent, 0.0);
    EXPECT_DOUBLE_EQ(actuators.state().lighting_percent, 0.0);
}

}  // namespace
