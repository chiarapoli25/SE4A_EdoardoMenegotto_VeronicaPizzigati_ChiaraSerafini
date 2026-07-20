#include "smarthydro/sensor_simulator.hpp"

#include <algorithm>

namespace smarthydro {
namespace {

constexpr double kMinimumTemperatureC = 18.0;
constexpr double kMaximumTemperatureC = 30.0;
constexpr double kMinimumHumidityPercent = 30.0;
constexpr double kMaximumHumidityPercent = 90.0;
constexpr double kMinimumPh = 4.0;
constexpr double kMaximumPh = 8.0;
constexpr double kMinimumLightPercent = 0.0;
constexpr double kMaximumLightPercent = 100.0;

constexpr double kMaximumTemperatureStepC = 0.25;
constexpr double kMaximumHumidityStepPercent = 1.0;
constexpr double kMaximumPhStep = 0.05;
constexpr double kMaximumLightStepPercent = 2.5;

double next_value(
    std::mt19937& generator,
    double current_value,
    double maximum_step,
    double minimum_value,
    double maximum_value) {
    std::uniform_real_distribution<double> variation(-maximum_step, maximum_step);
    return std::clamp(
        current_value + variation(generator), minimum_value, maximum_value);
}

}  // namespace

SensorSimulator::SensorSimulator()
    : SensorSimulator(std::random_device{}()) {}

SensorSimulator::SensorSimulator(std::uint32_t seed)
    : generator_(seed),
      current_readings_{24.0, 60.0, 6.0, 50.0} {}

SensorReadings SensorSimulator::read() {
    current_readings_.temperature_c = next_value(
        generator_,
        current_readings_.temperature_c,
        kMaximumTemperatureStepC,
        kMinimumTemperatureC,
        kMaximumTemperatureC);
    current_readings_.air_humidity_percent = next_value(
        generator_,
        current_readings_.air_humidity_percent,
        kMaximumHumidityStepPercent,
        kMinimumHumidityPercent,
        kMaximumHumidityPercent);
    current_readings_.ph = next_value(
        generator_, current_readings_.ph, kMaximumPhStep, kMinimumPh, kMaximumPh);
    current_readings_.light_percent = next_value(
        generator_,
        current_readings_.light_percent,
        kMaximumLightStepPercent,
        kMinimumLightPercent,
        kMaximumLightPercent);

    return current_readings_;
}

}  // namespace smarthydro
