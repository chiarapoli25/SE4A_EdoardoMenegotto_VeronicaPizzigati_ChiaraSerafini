#include "smarthydro/sensor_simulator.hpp"

#include <cmath>
#include <iostream>

namespace {

int failures = 0;

void check(bool condition, const char* message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

void test_values_stay_in_simulation_ranges() {
    smarthydro::SensorSimulator simulator(42);

    for (int sample = 0; sample < 1000; ++sample) {
        const auto readings = simulator.read();

        check(
            readings.temperature_c >= 18.0 && readings.temperature_c <= 30.0,
            "temperature is outside the simulation range");
        check(
            readings.air_humidity_percent >= 30.0 &&
                readings.air_humidity_percent <= 90.0,
            "humidity is outside the simulation range");
        check(readings.ph >= 4.0 && readings.ph <= 8.0, "pH is outside the simulation range");
        check(
            readings.light_percent >= 0.0 && readings.light_percent <= 100.0,
            "light is outside the simulation range");
    }
}

void test_seed_makes_simulation_reproducible() {
    smarthydro::SensorSimulator first(1234);
    smarthydro::SensorSimulator second(1234);

    for (int sample = 0; sample < 10; ++sample) {
        const auto first_readings = first.read();
        const auto second_readings = second.read();

        check(
            first_readings.temperature_c == second_readings.temperature_c,
            "temperature differs for the same seed");
        check(
            first_readings.air_humidity_percent == second_readings.air_humidity_percent,
            "humidity differs for the same seed");
        check(first_readings.ph == second_readings.ph, "pH differs for the same seed");
        check(
            first_readings.light_percent == second_readings.light_percent,
            "light differs for the same seed");
    }
}

void test_consecutive_readings_are_continuous() {
    smarthydro::SensorSimulator simulator(9876);
    auto previous = simulator.read();

    for (int sample = 0; sample < 1000; ++sample) {
        const auto current = simulator.read();

        check(
            std::abs(current.temperature_c - previous.temperature_c) <= 0.25,
            "temperature changed too quickly");
        check(
            std::abs(current.air_humidity_percent - previous.air_humidity_percent) <= 1.0,
            "humidity changed too quickly");
        check(std::abs(current.ph - previous.ph) <= 0.05, "pH changed too quickly");
        check(
            std::abs(current.light_percent - previous.light_percent) <= 2.5,
            "light changed too quickly");

        previous = current;
    }
}

}  // namespace

int main() {
    test_values_stay_in_simulation_ranges();
    test_seed_makes_simulation_reproducible();
    test_consecutive_readings_are_continuous();

    if (failures != 0) {
        std::cerr << failures << " sensor test assertion(s) failed\n";
        return 1;
    }

    std::cout << "All sensor simulator tests passed\n";
    return 0;
}
