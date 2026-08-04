#include <smarthydro/adapters/io_adapters.hpp>

#include <stdexcept>
#include <utility>

namespace smarthydro {
namespace {

void require_sampler(
    const std::shared_ptr<SimulatedSensorSampler>& sampler) {
    if (!sampler) {
        throw std::invalid_argument("sensor adapter requires a sampler");
    }
}

}  // namespace

std::size_t sensor_channel_index(SensorChannel channel) {
    const auto index = static_cast<std::size_t>(channel);
    if (index >= kSensorChannelCount) {
        throw std::invalid_argument("invalid sensor channel");
    }
    return index;
}

SimulatedSensorSampler::SimulatedSensorSampler(
    SensorConfig config,
    std::uint32_t seed)
    : simulator_(std::move(config), seed) {}

const SensorReadings& SimulatedSensorSampler::sample(
    const EnvironmentState& environment_state) {
    if (!cached_timestamp_seconds_.has_value() ||
        *cached_timestamp_seconds_ !=
            environment_state.simulation_time_seconds) {
        cached_readings_ = simulator_.read(environment_state);
        cached_timestamp_seconds_ =
            environment_state.simulation_time_seconds;
    }
    return cached_readings_;
}

TemperatureSensorAdapter::TemperatureSensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel TemperatureSensorAdapter::channel() const noexcept {
    return SensorChannel::TEMPERATURE;
}

std::optional<double> TemperatureSensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).temperature_c;
}

AirHumiditySensorAdapter::AirHumiditySensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel AirHumiditySensorAdapter::channel() const noexcept {
    return SensorChannel::AIR_HUMIDITY;
}

std::optional<double> AirHumiditySensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).air_humidity_percent;
}

SoilMoistureSensorAdapter::SoilMoistureSensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel SoilMoistureSensorAdapter::channel() const noexcept {
    return SensorChannel::SOIL_MOISTURE;
}

std::optional<double> SoilMoistureSensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).soil_moisture_percent;
}

SoilConductivitySensorAdapter::SoilConductivitySensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel SoilConductivitySensorAdapter::channel() const noexcept {
    return SensorChannel::SOIL_CONDUCTIVITY;
}

std::optional<double> SoilConductivitySensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).soil_bulk_ec_ms_cm;
}

PhSensorAdapter::PhSensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel PhSensorAdapter::channel() const noexcept {
    return SensorChannel::PH;
}

std::optional<double> PhSensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).ph;
}

LightSensorAdapter::LightSensorAdapter(
    std::shared_ptr<SimulatedSensorSampler> sampler)
    : sampler_(std::move(sampler)) {
    require_sampler(sampler_);
}

SensorChannel LightSensorAdapter::channel() const noexcept {
    return SensorChannel::LIGHT;
}

std::optional<double> LightSensorAdapter::read(
    const EnvironmentState& environment_state) {
    return sampler_->sample(environment_state).light_ppfd_umol_m2_s;
}

SensorAdapterArray make_simulated_sensor_adapters(
    SensorConfig config,
    std::uint32_t seed) {
    auto sampler = std::make_shared<SimulatedSensorSampler>(
        std::move(config), seed);
    SensorAdapterArray adapters;
    adapters[sensor_channel_index(SensorChannel::TEMPERATURE)] =
        std::make_unique<TemperatureSensorAdapter>(sampler);
    adapters[sensor_channel_index(SensorChannel::AIR_HUMIDITY)] =
        std::make_unique<AirHumiditySensorAdapter>(sampler);
    adapters[sensor_channel_index(SensorChannel::SOIL_MOISTURE)] =
        std::make_unique<SoilMoistureSensorAdapter>(sampler);
    adapters[sensor_channel_index(SensorChannel::SOIL_CONDUCTIVITY)] =
        std::make_unique<SoilConductivitySensorAdapter>(sampler);
    adapters[sensor_channel_index(SensorChannel::PH)] =
        std::make_unique<PhSensorAdapter>(sampler);
    adapters[sensor_channel_index(SensorChannel::LIGHT)] =
        std::make_unique<LightSensorAdapter>(std::move(sampler));
    return adapters;
}

ActuatorSimulatorAdapter::ActuatorSimulatorAdapter(ActuatorConfig config)
    : simulator_(std::move(config)) {}

const ActuatorCommand& ActuatorSimulatorAdapter::command() const noexcept {
    return simulator_.command();
}

const ActuatorOutput& ActuatorSimulatorAdapter::output() const noexcept {
    return simulator_.output();
}

const ActuatorConfig& ActuatorSimulatorAdapter::config() const noexcept {
    return simulator_.config();
}

void ActuatorSimulatorAdapter::request_irrigation_volume_liters(
    double volume_liters) {
    simulator_.request_irrigation_volume_liters(volume_liters);
}

void ActuatorSimulatorAdapter::cancel_irrigation() noexcept {
    simulator_.cancel_irrigation();
}

double ActuatorSimulatorAdapter::remaining_irrigation_time_seconds()
    const noexcept {
    return simulator_.remaining_irrigation_time_seconds();
}

void ActuatorSimulatorAdapter::set_fertilizer_valve_open(
    FertilizerType type,
    bool open) {
    simulator_.set_fertilizer_valve_open(type, open);
}

void ActuatorSimulatorAdapter::close_all_fertilizer_valves() noexcept {
    simulator_.close_all_fertilizer_valves();
}

bool ActuatorSimulatorAdapter::fertilizer_valve_open(
    FertilizerType type) const {
    return simulator_.fertilizer_valve_open(type);
}

void ActuatorSimulatorAdapter::set_lighting_command_percent(double value) {
    simulator_.set_lighting_command_percent(value);
}

void ActuatorSimulatorAdapter::step(double delta_time_seconds) {
    simulator_.step(delta_time_seconds);
}

void ActuatorSimulatorAdapter::stop_all() noexcept {
    simulator_.stop_all();
}

WaterPumpAdapter::WaterPumpAdapter(IActuator& actuator)
    : actuator_(actuator) {}

void WaterPumpAdapter::request_volume_liters(double volume_liters) {
    actuator_.request_irrigation_volume_liters(volume_liters);
}

void WaterPumpAdapter::cancel() noexcept {
    actuator_.cancel_irrigation();
}

bool WaterPumpAdapter::active() const noexcept {
    return actuator_.output().water_pump_on;
}

double WaterPumpAdapter::remaining_time_seconds() const noexcept {
    return actuator_.remaining_irrigation_time_seconds();
}

LightingAdapter::LightingAdapter(IActuator& actuator)
    : actuator_(actuator) {}

void LightingAdapter::set_command_percent(double value) {
    actuator_.set_lighting_command_percent(value);
}

double LightingAdapter::power_watts() const noexcept {
    return actuator_.output().lighting_power_watts;
}

FertilizerValveAdapter::FertilizerValveAdapter(IActuator& actuator)
    : actuator_(actuator) {}

void FertilizerValveAdapter::set_open(
    FertilizerType type,
    bool open) {
    actuator_.set_fertilizer_valve_open(type, open);
}

void FertilizerValveAdapter::close_all() noexcept {
    actuator_.close_all_fertilizer_valves();
}

bool FertilizerValveAdapter::open(FertilizerType type) const {
    return actuator_.fertilizer_valve_open(type);
}

EnvironmentSimulatorAdapter::EnvironmentSimulatorAdapter(
    EnvironmentConfig config)
    : simulator_(std::move(config)) {}

EnvironmentSimulatorAdapter::EnvironmentSimulatorAdapter(
    EnvironmentConfig config,
    std::uint32_t seed)
    : simulator_(std::move(config), seed) {}

const EnvironmentState&
EnvironmentSimulatorAdapter::state() const noexcept {
    return simulator_.state();
}

void EnvironmentSimulatorAdapter::step(
    double delta_time_seconds,
    const ActuatorOutput& actuator_output) {
    simulator_.step(delta_time_seconds, actuator_output);
}

}  // namespace smarthydro
