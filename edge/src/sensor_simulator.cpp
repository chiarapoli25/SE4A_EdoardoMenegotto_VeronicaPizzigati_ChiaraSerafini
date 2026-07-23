#include "smarthydro/sensor_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

void validate_channel(const SensorChannelConfig& channel, const char* name) {
    if (!std::isfinite(channel.bias) ||
        !std::isfinite(channel.noise_standard_deviation) ||
        !std::isfinite(channel.resolution) ||
        !std::isfinite(channel.dropout_probability) ||
        !std::isfinite(channel.calibration_correction)) {
        throw std::invalid_argument(std::string(name) + " sensor values must be finite");
    }
    if (channel.noise_standard_deviation < 0.0 || channel.resolution < 0.0) {
        throw std::invalid_argument(std::string(name) + " noise and resolution must not be negative");
    }
    if (channel.dropout_probability < 0.0 || channel.dropout_probability > 1.0) {
        throw std::invalid_argument(std::string(name) + " dropout probability must be in [0, 1]");
    }
}

void validate_config(const SensorConfig& config) {
    validate_channel(config.temperature, "temperature");
    validate_channel(config.air_humidity, "air humidity");
    validate_channel(config.ph, "pH");
    validate_channel(config.light_ppfd, "light PPFD");
}

}  // namespace

SensorSimulator::SensorSimulator(SensorConfig config)
    : SensorSimulator(std::move(config), std::random_device{}()) {}

SensorSimulator::SensorSimulator(std::uint32_t seed)
    : SensorSimulator(SensorConfig{}, seed) {}

SensorSimulator::SensorSimulator(SensorConfig config, std::uint32_t seed)
    : config_(std::move(config)), generator_(seed) {
    validate_config(config_);
}

std::optional<double> SensorSimulator::measure(
    double physical_value,
    const SensorChannelConfig& channel,
    double minimum_value,
    double maximum_value,
    bool preserve_physical_zero) {
    std::uniform_real_distribution<double> dropout(0.0, 1.0);
    if (dropout(generator_) < channel.dropout_probability) {
        return std::nullopt;
    }

    if (preserve_physical_zero && physical_value <= 0.0 && channel.bias == 0.0 &&
        channel.calibration_correction == 0.0) {
        return 0.0;
    }

    std::normal_distribution<double> noise(0.0, channel.noise_standard_deviation);
    double measured = physical_value + channel.bias +
                      channel.calibration_correction + noise(generator_);
    if (channel.resolution > 0.0) {
        measured = std::round(measured / channel.resolution) * channel.resolution;
    }
    return std::clamp(measured, minimum_value, maximum_value);
}

SensorReadings SensorSimulator::read(const EnvironmentState& environment_state) {
    return {
        environment_state.simulation_time_seconds,
        measure(environment_state.temperature_c, config_.temperature, -50.0, 80.0),
        measure(environment_state.air_humidity_percent, config_.air_humidity, 0.0, 100.0),
        measure(environment_state.ph, config_.ph, 0.0, 14.0),
        measure(
            environment_state.light_ppfd_umol_m2_s,
            config_.light_ppfd,
            0.0,
            3000.0,
            true),
    };
}

const SensorConfig& SensorSimulator::config() const noexcept {
    return config_;
}

}  // namespace smarthydro
