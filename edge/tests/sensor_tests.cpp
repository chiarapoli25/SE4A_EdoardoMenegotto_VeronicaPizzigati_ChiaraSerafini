#include "smarthydro/sensor_simulator.hpp"

#include <gtest/gtest.h>

namespace {

smarthydro::SensorConfig ideal_sensor_config() {
    smarthydro::SensorConfig config;
    config.temperature = {0.0, 0.0, 0.0, 0.0, 0.0};
    config.air_humidity = {0.0, 0.0, 0.0, 0.0, 0.0};
    config.soil_moisture = {0.0, 0.0, 0.0, 0.0, 0.0};
    config.ph = {0.0, 0.0, 0.0, 0.0, 0.0};
    config.light_ppfd = {0.0, 0.0, 0.0, 0.0, 0.0};
    return config;
}

TEST(SensorSimulatorTest, ReadDoesNotModifyEnvironment) {
    smarthydro::EnvironmentState environment;
    environment.simulation_time_seconds = 900.0;
    environment.temperature_c = 23.4;
    environment.air_humidity_percent = 67.0;
    environment.ph = 5.9;
    environment.ec_ms_cm = 2.4;
    environment.soil_moisture_percent = 80.0;
    environment.light_ppfd_umol_m2_s = 512.0;
    const auto original = environment;
    smarthydro::SensorSimulator sensors(ideal_sensor_config(), 42);

    const auto readings = sensors.read(environment);

    EXPECT_DOUBLE_EQ(environment.simulation_time_seconds, original.simulation_time_seconds);
    EXPECT_DOUBLE_EQ(environment.temperature_c, original.temperature_c);
    EXPECT_DOUBLE_EQ(environment.air_humidity_percent, original.air_humidity_percent);
    EXPECT_DOUBLE_EQ(environment.ph, original.ph);
    EXPECT_DOUBLE_EQ(environment.ec_ms_cm, original.ec_ms_cm);
    EXPECT_DOUBLE_EQ(environment.soil_moisture_percent, original.soil_moisture_percent);
    EXPECT_DOUBLE_EQ(environment.light_ppfd_umol_m2_s, original.light_ppfd_umol_m2_s);
    ASSERT_TRUE(readings.temperature_c.has_value());
    EXPECT_DOUBLE_EQ(*readings.temperature_c, 23.4);
    ASSERT_TRUE(readings.soil_moisture_percent.has_value());
    EXPECT_DOUBLE_EQ(*readings.soil_moisture_percent, 80.0);
    EXPECT_DOUBLE_EQ(readings.timestamp_seconds, 900.0);
}

TEST(SensorSimulatorTest, AppliesBiasCalibrationAndQuantization) {
    auto config = ideal_sensor_config();
    config.temperature = {0.20, 0.0, 0.10, 0.0, -0.10};
    smarthydro::SensorSimulator sensors(config, 7);
    smarthydro::EnvironmentState environment;
    environment.temperature_c = 20.03;

    const auto readings = sensors.read(environment);

    ASSERT_TRUE(readings.temperature_c.has_value());
    EXPECT_NEAR(*readings.temperature_c, 20.1, 1e-12);
}

TEST(SensorSimulatorTest, SupportsCompleteDropout) {
    auto config = ideal_sensor_config();
    config.temperature.dropout_probability = 1.0;
    config.air_humidity.dropout_probability = 1.0;
    config.soil_moisture.dropout_probability = 1.0;
    config.ph.dropout_probability = 1.0;
    config.light_ppfd.dropout_probability = 1.0;
    smarthydro::SensorSimulator sensors(config, 8);

    const auto readings = sensors.read({});

    EXPECT_FALSE(readings.temperature_c.has_value());
    EXPECT_FALSE(readings.air_humidity_percent.has_value());
    EXPECT_FALSE(readings.soil_moisture_percent.has_value());
    EXPECT_FALSE(readings.ph.has_value());
    EXPECT_FALSE(readings.light_ppfd_umol_m2_s.has_value());
}

TEST(SensorSimulatorTest, SeedMakesInstrumentNoiseReproducible) {
    smarthydro::SensorSimulator first(1234);
    smarthydro::SensorSimulator second(1234);
    smarthydro::EnvironmentState environment;
    environment.simulation_time_seconds = 1800.0;
    environment.temperature_c = 24.0;
    environment.air_humidity_percent = 65.0;
    environment.ph = 5.8;
    environment.ec_ms_cm = 2.5;
    environment.soil_moisture_percent = 90.0;
    environment.light_ppfd_umol_m2_s = 600.0;

    for (int sample = 0; sample < 20; ++sample) {
        const auto a = first.read(environment);
        const auto b = second.read(environment);
        EXPECT_EQ(a.temperature_c, b.temperature_c);
        EXPECT_EQ(a.air_humidity_percent, b.air_humidity_percent);
        EXPECT_EQ(a.soil_moisture_percent, b.soil_moisture_percent);
        EXPECT_EQ(a.ph, b.ph);
        EXPECT_EQ(a.light_ppfd_umol_m2_s, b.light_ppfd_umol_m2_s);
    }
}

TEST(SensorSimulatorTest, ReportsLightAsPpfdAndPreservesDarkness) {
    smarthydro::SensorSimulator sensors(ideal_sensor_config(), 9);
    smarthydro::EnvironmentState environment;
    environment.light_ppfd_umol_m2_s = 734.5;

    auto readings = sensors.read(environment);
    ASSERT_TRUE(readings.light_ppfd_umol_m2_s.has_value());
    EXPECT_DOUBLE_EQ(*readings.light_ppfd_umol_m2_s, 734.5);

    environment.light_ppfd_umol_m2_s = 0.0;
    readings = sensors.read(environment);
    ASSERT_TRUE(readings.light_ppfd_umol_m2_s.has_value());
    EXPECT_DOUBLE_EQ(*readings.light_ppfd_umol_m2_s, 0.0);
}

TEST(SensorSimulatorTest, ReportsSoilMoistureAsPercentage) {
    smarthydro::SensorSimulator sensors(ideal_sensor_config(), 10);
    smarthydro::EnvironmentState environment;
    environment.soil_moisture_percent = 63.5;

    const auto readings = sensors.read(environment);

    ASSERT_TRUE(readings.soil_moisture_percent.has_value());
    EXPECT_DOUBLE_EQ(*readings.soil_moisture_percent, 63.5);
}

TEST(SensorSimulatorTest, RejectsInvalidChannelConfiguration) {
    auto config = ideal_sensor_config();
    config.ph.dropout_probability = 1.1;
    EXPECT_THROW(smarthydro::SensorSimulator(config, 1), std::invalid_argument);
}

}  // namespace
