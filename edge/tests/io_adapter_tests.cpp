#include "smarthydro/io_adapters.hpp"

#include <gtest/gtest.h>

#include <memory>
#include <optional>

namespace {

smarthydro::SensorConfig deterministic_sensors() {
    smarthydro::SensorConfig config;
    for (auto* channel : {
             &config.temperature,
             &config.air_humidity,
             &config.soil_moisture,
             &config.ph,
             &config.light_ppfd}) {
        channel->bias = 0.0;
        channel->noise_standard_deviation = 0.0;
        channel->resolution = 0.0;
        channel->dropout_probability = 0.0;
        channel->calibration_correction = 0.0;
    }
    return config;
}

TEST(IoAdapterTest, RoutesOneSynchronizedSampleToAllSensorChannels) {
    auto adapters = smarthydro::make_simulated_sensor_adapters(
        deterministic_sensors(), 42U);
    smarthydro::EnvironmentState state;
    state.simulation_time_seconds = 120.0;
    state.temperature_c = 21.5;
    state.air_humidity_percent = 64.0;
    state.soil_moisture_percent = 58.0;
    state.ph = 6.1;
    state.light_ppfd_umol_m2_s = 430.0;

    EXPECT_DOUBLE_EQ(
        *adapters[smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::TEMPERATURE)]
             ->read(state),
        state.temperature_c);
    EXPECT_DOUBLE_EQ(
        *adapters[smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::AIR_HUMIDITY)]
             ->read(state),
        state.air_humidity_percent);
    EXPECT_DOUBLE_EQ(
        *adapters[smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::SOIL_MOISTURE)]
             ->read(state),
        state.soil_moisture_percent);
    EXPECT_DOUBLE_EQ(
        *adapters[smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::PH)]
             ->read(state),
        state.ph);
    EXPECT_DOUBLE_EQ(
        *adapters[smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::LIGHT)]
             ->read(state),
        state.light_ppfd_umol_m2_s);
}

TEST(IoAdapterTest, ComponentActuatorAdaptersShareTheSameDriver) {
    smarthydro::ActuatorSimulatorAdapter driver;
    smarthydro::WaterPumpAdapter water_pump(driver);
    smarthydro::LightingAdapter lighting(driver);
    smarthydro::FertilizerValveAdapter valves(driver);

    water_pump.request_volume_liters(0.5);
    valves.set_open(smarthydro::FertilizerType::NITROGEN, true);
    lighting.set_command_percent(50.0);
    driver.step(900.0);

    EXPECT_FALSE(water_pump.active());
    EXPECT_FALSE(valves.open(smarthydro::FertilizerType::NITROGEN));
    EXPECT_DOUBLE_EQ(
        driver.output().irrigation_volume_liters_last_step,
        0.5);
    EXPECT_DOUBLE_EQ(
        driver.output().fertilizer_volume_milliliters_last_step[
            smarthydro::fertilizer_index(
                smarthydro::FertilizerType::NITROGEN)],
        5.0);
    EXPECT_DOUBLE_EQ(lighting.power_watts(), 100.0);
}

TEST(IoAdapterTest, EnvironmentAdapterImplementsTheAbstractContract) {
    std::unique_ptr<smarthydro::IEnvironment> environment =
        std::make_unique<smarthydro::EnvironmentSimulatorAdapter>(
            smarthydro::EnvironmentConfig{}, 7U);
    const smarthydro::ActuatorOutput safe_output;

    environment->step(60.0, safe_output);

    EXPECT_DOUBLE_EQ(
        environment->state().simulation_time_seconds,
        60.0);
}

TEST(IoAdapterTest, RejectsInvalidSensorChannelAndMissingSampler) {
    EXPECT_THROW(
        smarthydro::sensor_channel_index(
            smarthydro::SensorChannel::COUNT),
        std::invalid_argument);
    EXPECT_THROW(
        smarthydro::SoilMoistureSensorAdapter(nullptr),
        std::invalid_argument);
}

}  // namespace
