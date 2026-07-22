#include "smarthydro/sensor_simulator.hpp"

#include <cmath>

#include <gtest/gtest.h>

namespace {

TEST(SensorSimulatorTest, ValuesStayInSimulationRanges) {
    smarthydro::SensorSimulator simulator(42);

    for (int sample = 0; sample < 1000; ++sample) {
        const auto readings = simulator.read();

        EXPECT_GE(readings.temperature_c, 18.0);
        EXPECT_LE(readings.temperature_c, 30.0);
        EXPECT_GE(readings.air_humidity_percent, 30.0);
        EXPECT_LE(readings.air_humidity_percent, 90.0);
        EXPECT_GE(readings.ph, 4.0);
        EXPECT_LE(readings.ph, 8.0);
        EXPECT_GE(readings.light_percent, 0.0);
        EXPECT_LE(readings.light_percent, 100.0);
    }
}

TEST(SensorSimulatorTest, SeedMakesSimulationReproducible) {
    smarthydro::SensorSimulator first(1234);
    smarthydro::SensorSimulator second(1234);

    for (int sample = 0; sample < 10; ++sample) {
        const auto first_readings = first.read();
        const auto second_readings = second.read();

        EXPECT_DOUBLE_EQ(first_readings.temperature_c, second_readings.temperature_c);
        EXPECT_DOUBLE_EQ(
            first_readings.air_humidity_percent,
            second_readings.air_humidity_percent);
        EXPECT_DOUBLE_EQ(first_readings.ph, second_readings.ph);
        EXPECT_DOUBLE_EQ(first_readings.light_percent, second_readings.light_percent);
    }
}

TEST(SensorSimulatorTest, ConsecutiveReadingsAreContinuous) {
    smarthydro::SensorSimulator simulator(9876);
    auto previous = simulator.read();

    for (int sample = 0; sample < 1000; ++sample) {
        const auto current = simulator.read();

        EXPECT_LE(std::abs(current.temperature_c - previous.temperature_c), 0.25);
        EXPECT_LE(
            std::abs(current.air_humidity_percent - previous.air_humidity_percent),
            1.0);
        EXPECT_LE(std::abs(current.ph - previous.ph), 0.05);
        EXPECT_LE(std::abs(current.light_percent - previous.light_percent), 2.5);

        previous = current;
    }
}

}  // namespace

