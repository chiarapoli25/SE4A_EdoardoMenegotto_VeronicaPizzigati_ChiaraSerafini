#include "smarthydro/actuator_simulator.hpp"

#include <array>
#include <stdexcept>

#include <gtest/gtest.h>

namespace {

constexpr std::array<smarthydro::FertilizerType, 5> kFertilizerTypes{
    smarthydro::FertilizerType::NITROGEN,
    smarthydro::FertilizerType::PHOSPHORUS,
    smarthydro::FertilizerType::POTASSIUM,
    smarthydro::FertilizerType::PH_UP,
    smarthydro::FertilizerType::PH_DOWN,
};

TEST(ActuatorSimulatorTest, StartsInSafeState) {
    const smarthydro::ActuatorSimulator actuators;
    const auto& command = actuators.command();
    const auto& output = actuators.output();

    EXPECT_DOUBLE_EQ(command.requested_irrigation_volume_liters, 0.0);
    EXPECT_DOUBLE_EQ(command.lighting_percent, 0.0);
    EXPECT_FALSE(output.water_pump_on);
    EXPECT_DOUBLE_EQ(output.water_pump_flow_liters_per_hour, 0.0);
    EXPECT_DOUBLE_EQ(output.irrigation_volume_liters_last_step, 0.0);
    EXPECT_DOUBLE_EQ(output.water_pump_on_time_seconds_last_step, 0.0);
    EXPECT_DOUBLE_EQ(output.remaining_irrigation_volume_liters, 0.0);
    for (const auto type : kFertilizerTypes) {
        EXPECT_FALSE(actuators.fertilizer_valve_command(type));
        EXPECT_FALSE(actuators.fertilizer_valve_open(type));
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_flow_milliliters_per_hour(type), 0.0);
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_volume_milliliters_last_step(type), 0.0);
    }
    EXPECT_DOUBLE_EQ(output.lighting_power_watts, 0.0);
}

TEST(ActuatorSimulatorTest, DeliversRequestedWaterVolumeAtFixedFlow) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);

    EXPECT_DOUBLE_EQ(actuators.command().requested_irrigation_volume_liters, 1.0);
    EXPECT_TRUE(actuators.output().water_pump_on);
    EXPECT_DOUBLE_EQ(actuators.output().water_pump_flow_liters_per_hour, 2.0);
    EXPECT_DOUBLE_EQ(actuators.remaining_irrigation_time_seconds(), 1800.0);

    actuators.step(900.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    EXPECT_DOUBLE_EQ(actuators.output().remaining_irrigation_volume_liters, 0.5);
    EXPECT_TRUE(actuators.output().water_pump_on);

    actuators.step(900.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    EXPECT_FALSE(actuators.output().water_pump_on);
}

TEST(ActuatorSimulatorTest, FertilizerValvesRequireActiveIrrigation) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(
        actuators.set_fertilizer_valve_open(
            smarthydro::FertilizerType::NITROGEN, true),
        std::logic_error);
    EXPECT_NO_THROW(
        actuators.set_fertilizer_valve_open(
            smarthydro::FertilizerType::NITROGEN, false));
}

TEST(ActuatorSimulatorTest, IntegratesMultipleConcentratesWithoutReducingWaterFlow) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);
    for (const auto type : {
             smarthydro::FertilizerType::NITROGEN,
             smarthydro::FertilizerType::PHOSPHORUS,
             smarthydro::FertilizerType::POTASSIUM,
             smarthydro::FertilizerType::PH_DOWN}) {
        actuators.set_fertilizer_valve_open(type, true);
    }

    actuators.step(900.0);

    EXPECT_DOUBLE_EQ(actuators.output().water_pump_flow_liters_per_hour, 2.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    for (const auto type : {
             smarthydro::FertilizerType::NITROGEN,
             smarthydro::FertilizerType::PHOSPHORUS,
             smarthydro::FertilizerType::POTASSIUM,
             smarthydro::FertilizerType::PH_DOWN}) {
        EXPECT_TRUE(actuators.fertilizer_valve_open(type));
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_flow_milliliters_per_hour(type), 20.0);
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_volume_milliliters_last_step(type), 5.0);
    }
}

TEST(ActuatorSimulatorTest, InterlocksOppositePhCorrectors) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::PH_UP, true);

    EXPECT_THROW(
        actuators.set_fertilizer_valve_open(
            smarthydro::FertilizerType::PH_DOWN, true),
        std::logic_error);
    EXPECT_TRUE(actuators.fertilizer_valve_open(
        smarthydro::FertilizerType::PH_UP));
    EXPECT_FALSE(actuators.fertilizer_valve_open(
        smarthydro::FertilizerType::PH_DOWN));
}

TEST(ActuatorSimulatorTest, IntegratesConcentrateOnlyUntilWaterDoseCompletes) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(0.5);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::NITROGEN, true);

    actuators.step(1800.0);

    EXPECT_DOUBLE_EQ(actuators.output().water_pump_on_time_seconds_last_step, 900.0);
    EXPECT_DOUBLE_EQ(actuators.output().irrigation_volume_liters_last_step, 0.5);
    EXPECT_DOUBLE_EQ(
        actuators.fertilizer_volume_milliliters_last_step(
            smarthydro::FertilizerType::NITROGEN),
        5.0);
    EXPECT_FALSE(actuators.output().water_pump_on);
    EXPECT_FALSE(actuators.fertilizer_valve_command(
        smarthydro::FertilizerType::NITROGEN));
    EXPECT_FALSE(actuators.fertilizer_valve_open(
        smarthydro::FertilizerType::NITROGEN));
}

TEST(ActuatorSimulatorTest, CancelIrrigationClosesAllValves) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::NITROGEN, true);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::POTASSIUM, true);

    actuators.cancel_irrigation();

    EXPECT_FALSE(actuators.output().water_pump_on);
    for (const auto type : kFertilizerTypes) {
        EXPECT_FALSE(actuators.fertilizer_valve_command(type));
        EXPECT_FALSE(actuators.fertilizer_valve_open(type));
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_flow_milliliters_per_hour(type), 0.0);
    }
}

TEST(ActuatorSimulatorTest, RejectsInvalidCommandsAndConfiguration) {
    smarthydro::ActuatorSimulator actuators;

    EXPECT_THROW(actuators.request_irrigation_volume_liters(0.0), std::invalid_argument);
    EXPECT_THROW(actuators.request_irrigation_volume_liters(5.1), std::invalid_argument);
    EXPECT_THROW(actuators.step(0.0), std::invalid_argument);
    EXPECT_THROW(actuators.set_lighting_command_percent(-1.0), std::invalid_argument);
    EXPECT_THROW(
        actuators.fertilizer_valve_open(smarthydro::FertilizerType::COUNT),
        std::invalid_argument);

    smarthydro::ActuatorConfig invalid_config;
    invalid_config.fertilizer_flow_milliliters_per_hour[2] = 0.0;
    EXPECT_THROW(
        static_cast<void>(smarthydro::ActuatorSimulator{invalid_config}),
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

TEST(ActuatorSimulatorTest, StopAllClearsEveryActuator) {
    smarthydro::ActuatorSimulator actuators;
    actuators.request_irrigation_volume_liters(1.0);
    actuators.set_fertilizer_valve_open(
        smarthydro::FertilizerType::PHOSPHORUS, true);
    actuators.set_lighting_command_percent(100.0);

    actuators.stop_all();

    EXPECT_DOUBLE_EQ(actuators.command().requested_irrigation_volume_liters, 0.0);
    EXPECT_DOUBLE_EQ(actuators.command().lighting_percent, 0.0);
    EXPECT_FALSE(actuators.output().water_pump_on);
    EXPECT_DOUBLE_EQ(actuators.output().lighting_power_watts, 0.0);
    for (const auto type : kFertilizerTypes) {
        EXPECT_FALSE(actuators.fertilizer_valve_open(type));
        EXPECT_DOUBLE_EQ(
            actuators.fertilizer_volume_milliliters_last_step(type), 0.0);
    }
}

}  // namespace
