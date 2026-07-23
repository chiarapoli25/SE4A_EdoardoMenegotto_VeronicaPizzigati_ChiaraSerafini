#include "smarthydro/actuator_simulator.hpp"

#include <stdexcept>

#include <gtest/gtest.h>

namespace {

TEST(ActuatorSimulatorTest, StartsInSafeState) {
    const smarthydro::ActuatorSimulator actuators;
    const auto& command = actuators.command();
    const auto& output = actuators.output();

    EXPECT_DOUBLE_EQ(command.requested_irrigation_volume_liters, 0.0);
    EXPECT_DOUBLE_EQ(command.fertilizer_doser_percent, 0.0);
    EXPECT_DOUBLE_EQ(command.lighting_percent, 0.0);
    EXPECT_FALSE(output.water_pump_on);
    EXPECT_DOUBLE_EQ(output.water_pump_flow_liters_per_hour, 0.0);
    EXPECT_DOUBLE_EQ(output.irrigation_volume_liters_last_step, 0.0);
    EXPECT_DOUBLE_EQ(output.water_pump_on_time_seconds_last_step, 0.0);
    EXPECT_DOUBLE_EQ(output.remaining_irrigation_volume_liters, 0.0);
    EXPECT_FALSE(output.selected_fertilizer_id.has_value());
    EXPECT_DOUBLE_EQ(output.fertilizer_flow_milliliters_per_hour, 0.0);
    EXPECT_DOUBLE_EQ(output.lighting_power_watts, 0.0);
}

TEST(ActuatorSimulatorTest, DeliversRequestedVolumeAtFixedOnOffFlow) {
    smarthydro::ActuatorSimulator actuators;

    actuators.request_irrigation_volume_liters(1.0);

    EXPECT_DOUBLE_EQ(actuators.command().requested_irrigation_volume_liters, 1.0);
    EXPECT_TRUE(actuators.output().water_pump_on);
    EXPECT_DOUBLE_EQ(actuators.output().water_pump_flow_liters_per_hour, 2.0);
    EXPECT_DOUBLE_EQ(actuators.output().remaining_irrigation_volume_liters, 1.0);
    EXPECT_DOUBLE_EQ(actuators.remaining_irrigation_time_seconds(), 1800.0);

    actuators.step(900.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    EXPECT_DOUBLE_EQ(actuators.output().water_pump_on_time_seconds_last_step, 900.0);
    EXPECT_DOUBLE_EQ(actuators.output().remaining_irrigation_volume_liters, 0.5);
    EXPECT_TRUE(actuators.output().water_pump_on);

    actuators.step(900.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    EXPECT_DOUBLE_EQ(actuators.output().water_pump_on_time_seconds_last_step, 900.0);
    EXPECT_DOUBLE_EQ(actuators.output().remaining_irrigation_volume_liters, 0.0);
    EXPECT_FALSE(actuators.output().water_pump_on);
    EXPECT_DOUBLE_EQ(actuators.output().water_pump_flow_liters_per_hour, 0.0);
}

TEST(ActuatorSimulatorTest, ConvertsContinuousCommandsImmediately) {
    smarthydro::ActuatorSimulator actuators;

    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_doser_command_percent(12.5);
    actuators.set_lighting_command_percent(80.0);

    EXPECT_DOUBLE_EQ(actuators.command().fertilizer_doser_percent, 12.5);
    EXPECT_DOUBLE_EQ(actuators.command().lighting_percent, 80.0);
    EXPECT_DOUBLE_EQ(actuators.output().fertilizer_flow_milliliters_per_hour, 2.5);
    EXPECT_DOUBLE_EQ(actuators.output().lighting_power_watts, 160.0);
}

TEST(ActuatorSimulatorTest, EnforcesFertilizerSafetyRules) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(
        actuators.set_fertilizer_doser_command_percent(10.0),
        std::logic_error);

    actuators.select_fertilizer("tomato-growth");
    actuators.set_fertilizer_doser_command_percent(10.0);
    actuators.clear_fertilizer_selection();

    EXPECT_FALSE(actuators.output().selected_fertilizer_id.has_value());
    EXPECT_DOUBLE_EQ(actuators.command().fertilizer_doser_percent, 0.0);
    EXPECT_DOUBLE_EQ(actuators.output().fertilizer_flow_milliliters_per_hour, 0.0);
}

TEST(ActuatorSimulatorTest, RejectsInvalidCommands) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(actuators.request_irrigation_volume_liters(0.0), std::invalid_argument);
    EXPECT_THROW(actuators.request_irrigation_volume_liters(5.1), std::invalid_argument);
    EXPECT_THROW(actuators.step(0.0), std::invalid_argument);
    EXPECT_THROW(actuators.set_lighting_command_percent(-1.0), std::invalid_argument);
    EXPECT_THROW(actuators.select_fertilizer(""), std::invalid_argument);
    EXPECT_THROW(
        static_cast<void>(smarthydro::ActuatorSimulator{
            {0.0, 5.0, 20.0, 200.0}}),
        std::invalid_argument);
}

TEST(ActuatorSimulatorTest, RejectsOverlappingIrrigationRequests) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);

    EXPECT_THROW(
        actuators.request_irrigation_volume_liters(0.5),
        std::logic_error);

    actuators.cancel_irrigation();
    EXPECT_NO_THROW(actuators.request_irrigation_volume_liters(0.5));
}

TEST(ActuatorSimulatorTest, StopAllClearsState) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);
    actuators.set_lighting_command_percent(100.0);

    actuators.stop_all();

    EXPECT_DOUBLE_EQ(actuators.command().requested_irrigation_volume_liters, 0.0);
    EXPECT_DOUBLE_EQ(actuators.command().lighting_percent, 0.0);
    EXPECT_FALSE(actuators.output().water_pump_on);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.0);
    EXPECT_DOUBLE_EQ(actuators.output().remaining_irrigation_volume_liters, 0.0);
    EXPECT_DOUBLE_EQ(actuators.output().lighting_power_watts, 0.0);
}

}  // namespace
