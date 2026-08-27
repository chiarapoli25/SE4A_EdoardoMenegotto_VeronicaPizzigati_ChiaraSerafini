#include <smarthydro/simulation/sensor_simulator.hpp>

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
    validate_channel(config.soil_moisture, "soil moisture");
    validate_channel(config.soil_conductivity, "soil conductivity");
    validate_channel(config.ph, "pH");
    validate_channel(config.light_ppfd, "light PPFD");
    const auto& model = config.soil_probe_model;
    if (!std::isfinite(model.dry_capacitance_pf) ||
        !std::isfinite(model.saturated_capacitance_pf) ||
        !std::isfinite(model.conductivity_moisture_exponent) ||
        !std::isfinite(model.minimum_moisture_fraction) ||
        !std::isfinite(model.background_ec_ms_cm) ||
        !std::isfinite(model.fertilizer_mg_per_liter_per_ms_cm) ||
        model.dry_capacitance_pf < 0.0 ||
        model.saturated_capacitance_pf <= model.dry_capacitance_pf ||
        model.conductivity_moisture_exponent <= 0.0 ||
        model.minimum_moisture_fraction <= 0.0 ||
        model.minimum_moisture_fraction > 1.0 ||
        model.background_ec_ms_cm < 0.0 ||
        model.fertilizer_mg_per_liter_per_ms_cm <= 0.0) {
        throw std::invalid_argument("invalid soil probe model configuration");
    }
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

    // channel.noise_standard_deviation == 0.0 is a legitimate, validated
    // configuration (validate_channel above only rejects < 0.0) — every
    // "deterministic, zero-noise" test config uses it deliberately, to get
    // reproducible readings. std::normal_distribution(mean, 0.0) is well
    // defined on libstdc++ (always returns mean), but MSVC's Debug STL
    // asserts on a non-positive sigma ("invalid sigma argument"). Skip
    // constructing the distribution entirely when sigma <= 0 and use its
    // mean (0.0 here) directly instead, which reproduces the exact
    // observable behavior the code already had on Linux — not a workaround
    // that changes results, just avoiding a distribution call that would
    // add nothing anyway.
    const double noise_sample = channel.noise_standard_deviation > 0.0
        ? std::normal_distribution<double>(0.0, channel.noise_standard_deviation)(generator_)
        : 0.0;
    double measured = physical_value + channel.bias +
                      channel.calibration_correction + noise_sample;
    if (channel.resolution > 0.0) {
        measured = std::round(measured / channel.resolution) * channel.resolution;
    }
    return std::clamp(measured, minimum_value, maximum_value);
}

void update_soil_probe_estimates(
    SensorReadings& readings,
    const EnvironmentState& environment_state,
    const SoilProbeModelConfig& config) {
    readings.soil_ec_ms_cm.reset();
    readings.fertilizer_concentration_mg_per_liter.reset();
    readings.nitrogen_estimate_mg_per_liter.reset();
    readings.phosphorus_estimate_mg_per_liter.reset();
    readings.potassium_estimate_mg_per_liter.reset();
    if (!readings.soil_moisture_percent.has_value() ||
        !readings.soil_bulk_ec_ms_cm.has_value()) {
        return;
    }

    const double moisture_fraction = std::max(
        config.minimum_moisture_fraction,
        *readings.soil_moisture_percent / 100.0);
    const double moisture_correction = std::pow(
        moisture_fraction,
        config.conductivity_moisture_exponent);
    const double pore_water_ec = std::clamp(
        *readings.soil_bulk_ec_ms_cm / moisture_correction,
        0.0,
        8.0);
    const double total_fertilizer = std::max(
        0.0,
        (pore_water_ec - config.background_ec_ms_cm) *
            config.fertilizer_mg_per_liter_per_ms_cm);

    readings.soil_ec_ms_cm = pore_water_ec;
    readings.fertilizer_concentration_mg_per_liter = total_fertilizer;

    const double model_total =
        environment_state.nitrogen_mg_per_liter +
        environment_state.phosphorus_mg_per_liter +
        environment_state.potassium_mg_per_liter;
    if (model_total <= 0.0) {
        readings.nitrogen_estimate_mg_per_liter = 0.0;
        readings.phosphorus_estimate_mg_per_liter = 0.0;
        readings.potassium_estimate_mg_per_liter = 0.0;
        return;
    }
    readings.nitrogen_estimate_mg_per_liter =
        total_fertilizer * environment_state.nitrogen_mg_per_liter /
        model_total;
    readings.phosphorus_estimate_mg_per_liter =
        total_fertilizer * environment_state.phosphorus_mg_per_liter /
        model_total;
    readings.potassium_estimate_mg_per_liter =
        total_fertilizer * environment_state.potassium_mg_per_liter /
        model_total;
}

SensorReadings SensorSimulator::read(const EnvironmentState& environment_state) {
    SensorReadings readings;
    readings.timestamp_seconds = environment_state.simulation_time_seconds;
    readings.temperature_c = measure(
        environment_state.temperature_c, config_.temperature, -50.0, 80.0);
    readings.air_humidity_percent = measure(
        environment_state.air_humidity_percent,
        config_.air_humidity,
        0.0,
        100.0);

    // La capacita cresce con il contenuto d'acqua. Il segnale strumentale in
    // pF viene perturbato e poi invertito mediante la curva di calibrazione.
    const auto& probe_model = config_.soil_probe_model;
    const double capacitance_span =
        probe_model.saturated_capacitance_pf -
        probe_model.dry_capacitance_pf;
    const double physical_capacitance =
        probe_model.dry_capacitance_pf +
        environment_state.soil_moisture_percent / 100.0 * capacitance_span;
    const auto measured_capacitance = measure(
        physical_capacitance,
        config_.soil_moisture,
        probe_model.dry_capacitance_pf,
        probe_model.saturated_capacitance_pf);
    if (measured_capacitance.has_value()) {
        readings.soil_moisture_percent =
            (*measured_capacitance - probe_model.dry_capacitance_pf) /
            capacitance_span * 100.0;
    }

    // Una coppia di elettrodi resistivi osserva la conducibilita del volume di
    // terriccio. A parita di EC dell'acqua nei pori, il segnale cala da secco.
    const double physical_moisture_fraction = std::max(
        config_.soil_probe_model.minimum_moisture_fraction,
        environment_state.soil_moisture_percent / 100.0);
    const double bulk_ec = environment_state.ec_ms_cm * std::pow(
        physical_moisture_fraction,
        config_.soil_probe_model.conductivity_moisture_exponent);
    readings.soil_bulk_ec_ms_cm = measure(
        bulk_ec, config_.soil_conductivity, 0.0, 8.0);
    update_soil_probe_estimates(
        readings, environment_state, config_.soil_probe_model);

    readings.ph = measure(environment_state.ph, config_.ph, 0.0, 14.0);
    readings.light_ppfd_umol_m2_s = measure(
        environment_state.light_ppfd_umol_m2_s,
        config_.light_ppfd,
        0.0,
        3000.0,
        true);
    return readings;
}

}  // namespace smarthydro
