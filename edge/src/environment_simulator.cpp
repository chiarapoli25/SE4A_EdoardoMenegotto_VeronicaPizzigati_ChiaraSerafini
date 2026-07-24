#include "smarthydro/environment_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>

namespace smarthydro {
namespace {

constexpr double kSecondsPerHour = 3600.0;
constexpr double kHoursPerDay = 24.0;
constexpr double kPi = 3.14159265358979323846;
constexpr double kMinimumCloudTransmission = 0.20;
constexpr double kMaximumCloudTransmission = 1.0;

void require_finite(double value, const char* name) {
    if (!std::isfinite(value)) {
        throw std::invalid_argument(std::string(name) + " must be finite");
    }
}

void validate_percentage(double value, const char* name) {
    require_finite(value, name);
    if (value < 0.0 || value > 100.0) {
        throw std::invalid_argument(std::string(name) + " must be between 0 and 100");
    }
}

void validate_non_negative(double value, const char* name) {
    require_finite(value, name);
    if (value < 0.0) {
        throw std::invalid_argument(std::string(name) + " must not be negative");
    }
}

void validate_config(const EnvironmentConfig& config) {
    switch (config.soil_type) {
        case SoilType::AERATED_UNIVERSAL:
        case SoilType::DRAINING:
        case SoilType::ORGANIC_RETENTIVE:
            break;
        default:
            throw std::invalid_argument("unknown soil type");
    }

    require_finite(config.sunrise_hour, "sunrise_hour");
    require_finite(config.photoperiod_hours, "photoperiod_hours");
    if (config.sunrise_hour < 0.0 || config.sunrise_hour >= 24.0 ||
        config.photoperiod_hours <= 0.0 || config.photoperiod_hours > 24.0) {
        throw std::invalid_argument("sunrise and photoperiod must describe one day");
    }
    require_finite(config.night_temperature_c, "night_temperature_c");
    require_finite(config.day_temperature_c, "day_temperature_c");
    validate_percentage(config.night_relative_humidity_percent, "night RH");
    validate_percentage(config.day_relative_humidity_percent, "day RH");
    require_finite(config.initial_ph, "initial_ph");
    require_finite(config.initial_ec_ms_cm, "initial_ec_ms_cm");
    validate_non_negative(
        config.initial_nitrogen_mg_per_liter,
        "initial nitrogen concentration");
    validate_non_negative(
        config.initial_phosphorus_mg_per_liter,
        "initial phosphorus concentration");
    validate_non_negative(
        config.initial_potassium_mg_per_liter,
        "initial potassium concentration");
    validate_non_negative(
        config.nitrogen_uptake_milligrams_per_hour,
        "nitrogen uptake");
    validate_non_negative(
        config.phosphorus_uptake_milligrams_per_hour,
        "phosphorus uptake");
    validate_non_negative(
        config.potassium_uptake_milligrams_per_hour,
        "potassium uptake");
    require_finite(
        config.minimum_effective_root_water_liters,
        "minimum effective root water");
    require_finite(
        config.maximum_nutrient_concentration_mg_per_liter,
        "maximum nutrient concentration");
    require_finite(config.natural_light_peak_ppfd, "natural_light_peak_ppfd");
    require_finite(config.mean_cloud_transmission, "mean cloud transmission");
    validate_non_negative(
        config.daily_cloud_transmission_stddev,
        "daily cloud transmission standard deviation");
    validate_non_negative(
        config.hourly_cloud_transmission_stddev,
        "hourly cloud transmission standard deviation");
    require_finite(config.cloud_persistence_hours, "cloud persistence");
    require_finite(config.lamp_ppfd_umol_m2_s_per_watt, "lamp PPFD per watt");
    require_finite(config.lamp_heating_c_per_watt, "lamp heating per watt");
    if (config.initial_ph < 0.0 || config.initial_ph > 14.0 ||
        config.initial_ec_ms_cm < 0.0 || config.natural_light_peak_ppfd < 0.0 ||
        config.mean_cloud_transmission < 0.0 ||
        config.mean_cloud_transmission > 1.0 ||
        config.cloud_persistence_hours <= 0.0 ||
        config.lamp_ppfd_umol_m2_s_per_watt < 0.0 ||
        config.lamp_heating_c_per_watt < 0.0 ||
        config.minimum_effective_root_water_liters <= 0.0 ||
        config.maximum_nutrient_concentration_mg_per_liter <= 0.0 ||
        config.initial_nitrogen_mg_per_liter >
            config.maximum_nutrient_concentration_mg_per_liter ||
        config.initial_phosphorus_mg_per_liter >
            config.maximum_nutrient_concentration_mg_per_liter ||
        config.initial_potassium_mg_per_liter >
            config.maximum_nutrient_concentration_mg_per_liter) {
        throw std::invalid_argument(
            "pH, EC, nutrient, cloud and PPFD configuration values are out of range");
    }
    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        const auto& profile = config.fertilizer_profiles[index];
        if (fertilizer_index(profile.type) != index) {
            throw std::invalid_argument(
                "fertilizer profiles must match their reservoir index");
        }
        validate_non_negative(
            profile.nitrogen_milligrams_per_milliliter,
            "fertilizer nitrogen content");
        validate_non_negative(
            profile.phosphorus_milligrams_per_milliliter,
            "fertilizer phosphorus content");
        validate_non_negative(
            profile.potassium_milligrams_per_milliliter,
            "fertilizer potassium content");
        validate_non_negative(
            profile.ec_increase_ms_cm_per_milliliter,
            "fertilizer EC effect");
        require_finite(profile.ph_change_per_milliliter, "fertilizer pH effect");
    }
    const auto& ph_up = config.fertilizer_profiles[
        fertilizer_index(FertilizerType::PH_UP)];
    const auto& ph_down = config.fertilizer_profiles[
        fertilizer_index(FertilizerType::PH_DOWN)];
    if (ph_up.ph_change_per_milliliter <= 0.0 ||
        ph_down.ph_change_per_milliliter >= 0.0) {
        throw std::invalid_argument(
            "pH corrector profiles must change pH in the declared direction");
    }
}

double daylight_factor(double hour, const EnvironmentConfig& config) {
    const double relative_hour = hour - config.sunrise_hour;
    if (relative_hour < 0.0 || relative_hour > config.photoperiod_hours) {
        return 0.0;
    }
    return std::max(0.0, std::sin(kPi * relative_hour / config.photoperiod_hours));
}

double saturation_vapor_density(double temperature_c) {
    const double vapor_pressure_hpa =
        6.112 * std::exp((17.67 * temperature_c) / (temperature_c + 243.5));
    return 216.7 * vapor_pressure_hpa / (temperature_c + 273.15);
}

struct SoilDynamics {
    double depletion_per_hour;
    double available_water_capacity_liters;
    double irrigation_retention_efficiency;
    double drainage_per_hour;
    double field_capacity;
    double initial_soil_moisture_percent;
    double ec_leaching_per_liter;
    double ph_equilibrium_offset;
    double ph_buffering_days;
};

SoilDynamics soil_dynamics(SoilType soil_type) {
    switch (soil_type) {
        case SoilType::AERATED_UNIVERSAL:
            return {0.020, 4.0, 0.90, 0.55, 0.78, 75.0, 0.06, 0.15, 7.0};
        case SoilType::DRAINING:
            return {0.030, 3.0, 0.90, 1.10, 0.66, 65.0, 0.125, 0.10, 5.0};
        case SoilType::ORGANIC_RETENTIVE:
            return {0.012, 5.0, 0.80, 0.22, 0.86, 82.0, 0.025, -0.05, 10.0};
    }
    return {0.020, 4.0, 0.90, 0.55, 0.78, 75.0, 0.06, 0.15, 7.0};
}

}  // namespace

const char* to_string(SoilType soil_type) noexcept {
    switch (soil_type) {
        case SoilType::AERATED_UNIVERSAL:
            return "aerated-universal";
        case SoilType::DRAINING:
            return "draining";
        case SoilType::ORGANIC_RETENTIVE:
            return "organic-retentive";
    }
    return "unknown";
}

EnvironmentSimulator::EnvironmentSimulator(EnvironmentConfig config)
    : EnvironmentSimulator(std::move(config), std::random_device{}()) {}

EnvironmentSimulator::EnvironmentSimulator(EnvironmentConfig config, std::uint32_t seed)
    : config_(std::move(config)), generator_(seed) {
    validate_config(config_);
    std::normal_distribution<double> standard_normal(0.0, 1.0);
    daily_cloud_transmission_ = std::clamp(
        config_.mean_cloud_transmission +
            config_.daily_cloud_transmission_stddev * standard_normal(generator_),
        kMinimumCloudTransmission,
        kMaximumCloudTransmission);
    hourly_cloud_deviation_ =
        config_.hourly_cloud_transmission_stddev * standard_normal(generator_);
    state_.temperature_c = config_.night_temperature_c;
    state_.air_humidity_percent = config_.night_relative_humidity_percent;
    state_.ph = config_.initial_ph;
    state_.ec_ms_cm = config_.initial_ec_ms_cm;
    state_.nitrogen_mg_per_liter = config_.initial_nitrogen_mg_per_liter;
    state_.phosphorus_mg_per_liter = config_.initial_phosphorus_mg_per_liter;
    state_.potassium_mg_per_liter = config_.initial_potassium_mg_per_liter;
    state_.soil_moisture_percent =
        soil_dynamics(config_.soil_type).initial_soil_moisture_percent;
    const double initial_root_water_liters =
        soil_dynamics(config_.soil_type).available_water_capacity_liters *
        state_.soil_moisture_percent / 100.0;
    nutrient_masses_milligrams_ = {
        state_.nitrogen_mg_per_liter * initial_root_water_liters,
        state_.phosphorus_mg_per_liter * initial_root_water_liters,
        state_.potassium_mg_per_liter * initial_root_water_liters,
    };
    vapor_density_g_m3_ = saturation_vapor_density(state_.temperature_c) *
                          state_.air_humidity_percent / 100.0;
}

void EnvironmentSimulator::step(
    double delta_time_seconds,
    const ActuatorOutput& actuator_output) {
    require_finite(delta_time_seconds, "delta_time_seconds");
    if (delta_time_seconds <= 0.0) {
        throw std::invalid_argument("delta_time_seconds must be positive");
    }
    validate_non_negative(
        actuator_output.irrigation_volume_liters_last_step,
        "irrigation volume");
    validate_non_negative(actuator_output.lighting_power_watts, "lighting power");
    bool has_fertilizer = false;
    for (const double volume :
         actuator_output.fertilizer_volume_milliliters_last_step) {
        validate_non_negative(volume, "fertilizer volume");
        has_fertilizer = has_fertilizer || volume > 0.0;
    }
    if (has_fertilizer &&
        actuator_output.irrigation_volume_liters_last_step <= 0.0) {
        throw std::invalid_argument(
            "fertilizer requires irrigation water for dilution");
    }
    if (actuator_output.fertilizer_volume_milliliters_last_step[
            fertilizer_index(FertilizerType::PH_UP)] > 0.0 &&
        actuator_output.fertilizer_volume_milliliters_last_step[
            fertilizer_index(FertilizerType::PH_DOWN)] > 0.0) {
        throw std::invalid_argument(
            "pH up and pH down cannot be delivered together");
    }

    double remaining = delta_time_seconds;
    while (remaining > 0.0) {
        const double substep = std::min(remaining, 300.0);
        auto substep_output = actuator_output;
        substep_output.irrigation_volume_liters_last_step =
            actuator_output.irrigation_volume_liters_last_step *
            substep / delta_time_seconds;
        for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
            substep_output.fertilizer_volume_milliliters_last_step[index] =
                actuator_output.fertilizer_volume_milliliters_last_step[index] *
                substep / delta_time_seconds;
        }
        integrate_substep(substep, substep_output);
        remaining -= substep;
    }
}

void EnvironmentSimulator::integrate_substep(
    double delta_time_seconds,
    const ActuatorOutput& actuator_output) {
    const double delta_hours = delta_time_seconds / kSecondsPerHour;
    state_.simulation_time_seconds += delta_time_seconds;
    const double hour = std::fmod(state_.simulation_time_seconds / kSecondsPerHour, kHoursPerDay);
    const double sun_factor = daylight_factor(hour, config_);

    std::normal_distribution<double> standard_normal(0.0, 1.0);
    const auto current_day = static_cast<std::uint64_t>(
        state_.simulation_time_seconds / (kHoursPerDay * kSecondsPerHour));
    if (current_day != cloud_regime_day_) {
        daily_cloud_transmission_ = std::clamp(
            config_.mean_cloud_transmission +
                config_.daily_cloud_transmission_stddev * standard_normal(generator_),
            kMinimumCloudTransmission,
            kMaximumCloudTransmission);
        cloud_regime_day_ = current_day;
    }

    // Processo di Ornstein-Uhlenbeck discreto: le variazioni orarie sono
    // correlate e persistono, invece di diventare rumore bianco a ogni passo.
    const double cloud_memory =
        std::exp(-delta_hours / config_.cloud_persistence_hours);
    const double cloud_innovation_scale =
        config_.hourly_cloud_transmission_stddev *
        std::sqrt(1.0 - cloud_memory * cloud_memory);
    hourly_cloud_deviation_ =
        cloud_memory * hourly_cloud_deviation_ +
        cloud_innovation_scale * standard_normal(generator_);
    const double cloud_transmission = std::clamp(
        daily_cloud_transmission_ + hourly_cloud_deviation_,
        kMinimumCloudTransmission,
        kMaximumCloudTransmission);

    const double noise_scale = std::sqrt(delta_hours);
    temperature_disturbance_c_ +=
        -temperature_disturbance_c_ * delta_hours / 4.0 +
        0.08 * noise_scale * standard_normal(generator_);
    temperature_disturbance_c_ = std::clamp(temperature_disturbance_c_, -1.5, 1.5);

    const double natural_ppfd =
        config_.natural_light_peak_ppfd * sun_factor * cloud_transmission;
    const double lamp_ppfd = config_.lamp_ppfd_umol_m2_s_per_watt *
                             actuator_output.lighting_power_watts;
    state_.light_ppfd_umol_m2_s = std::max(0.0, natural_ppfd + lamp_ppfd);

    const double external_temperature =
        config_.night_temperature_c +
        (config_.day_temperature_c - config_.night_temperature_c) * sun_factor +
        temperature_disturbance_c_;
    const double solar_heating_c = natural_ppfd * 0.0025;
    const double lamp_heating_c = config_.lamp_heating_c_per_watt *
                                  actuator_output.lighting_power_watts;
    const double temperature_equilibrium =
        external_temperature + solar_heating_c + lamp_heating_c;
    const double temperature_response = 1.0 - std::exp(-delta_hours / 1.7);
    state_.temperature_c +=
        (temperature_equilibrium - state_.temperature_c) * temperature_response;
    state_.temperature_c = std::clamp(state_.temperature_c, 5.0, 45.0);

    const auto soil = soil_dynamics(config_.soil_type);
    const double light_activity =
        std::clamp(state_.light_ppfd_umol_m2_s / 700.0, 0.0, 1.5);
    double soil_water_fraction = state_.soil_moisture_percent / 100.0;
    const double water_before_step = soil_water_fraction;
    const double depletion = soil.depletion_per_hour * (0.20 + 0.80 * light_activity);
    const double irrigation_volume_liters =
        actuator_output.irrigation_volume_liters_last_step;
    const double recovery =
        irrigation_volume_liters * soil.irrigation_retention_efficiency /
        soil.available_water_capacity_liters;
    const double provisional_water =
        soil_water_fraction + recovery - depletion * delta_hours;
    const double drainage = soil.drainage_per_hour *
                            std::max(0.0, provisional_water - soil.field_capacity);
    const double root_water_liters_before_drainage =
        std::max(0.0, provisional_water) *
        soil.available_water_capacity_liters;
    soil_water_fraction = std::clamp(
        provisional_water - drainage * delta_hours, 0.0, 1.0);
    const double root_water_liters_after =
        soil_water_fraction * soil.available_water_capacity_liters;
    const double drained_or_overflow_liters = std::max(
        0.0,
        root_water_liters_before_drainage - root_water_liters_after);
    state_.soil_moisture_percent = soil_water_fraction * 100.0;

    const double external_rh =
        config_.night_relative_humidity_percent +
        (config_.day_relative_humidity_percent -
         config_.night_relative_humidity_percent) * sun_factor;
    const double external_vapor = saturation_vapor_density(external_temperature) *
                                  external_rh / 100.0;
    const double air_exchange = (external_vapor - vapor_density_g_m3_) * delta_hours / 2.0;
    const double transpiration = 0.30 * light_activity *
                                 soil_water_fraction * delta_hours;
    const double irrigation_evaporation = 0.03 * irrigation_volume_liters;
    vapor_density_g_m3_ += air_exchange + transpiration + irrigation_evaporation;
    vapor_density_g_m3_ = std::max(0.0, vapor_density_g_m3_);
    state_.air_humidity_percent = std::clamp(
        100.0 * vapor_density_g_m3_ / saturation_vapor_density(state_.temperature_c),
        20.0,
        99.0);

    const double uptake_activity = (0.20 + 0.80 * light_activity) *
                                   soil_water_fraction;
    const double ph_equilibrium = config_.initial_ph + soil.ph_equilibrium_offset;
    state_.ph += (ph_equilibrium - state_.ph) * delta_hours /
                 (24.0 * soil.ph_buffering_days);
    state_.ph += 0.004 * uptake_activity * delta_hours / 24.0;
    state_.ec_ms_cm -= 0.025 * uptake_activity * delta_hours / 24.0;

    const double water_change =
        soil_water_fraction - water_before_step;
    if (water_change < 0.0) {
        state_.ec_ms_cm *= 1.0 + (-water_change) * 0.08;
    }
    if (irrigation_volume_liters > 0.0) {
        constexpr double kIrrigationWaterEcMsCm = 0.60;
        state_.ec_ms_cm += (kIrrigationWaterEcMsCm - state_.ec_ms_cm) *
                           soil.ec_leaching_per_liter * irrigation_volume_liters;
    }

    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        const double fertilizer_volume_milliliters =
            actuator_output.fertilizer_volume_milliliters_last_step[index];
        if (fertilizer_volume_milliliters <= 0.0) {
            continue;
        }
        const auto& profile = config_.fertilizer_profiles[index];
        nutrient_masses_milligrams_[0] +=
            profile.nitrogen_milligrams_per_milliliter *
            fertilizer_volume_milliliters;
        nutrient_masses_milligrams_[1] +=
            profile.phosphorus_milligrams_per_milliliter *
            fertilizer_volume_milliliters;
        nutrient_masses_milligrams_[2] +=
            profile.potassium_milligrams_per_milliliter *
            fertilizer_volume_milliliters;
        state_.ph += profile.ph_change_per_milliliter *
                     fertilizer_volume_milliliters;
        state_.ec_ms_cm += profile.ec_increase_ms_cm_per_milliliter *
                           fertilizer_volume_milliliters;
    }

    // Il drenaggio rimuove la stessa frazione di ogni massa presente nella
    // soluzione ben miscelata. La massa non puo diventare negativa.
    const double mixed_root_water_liters = std::max(
        root_water_liters_before_drainage,
        config_.minimum_effective_root_water_liters);
    const double drained_fraction = std::clamp(
        drained_or_overflow_liters / mixed_root_water_liters, 0.0, 1.0);
    for (double& mass : nutrient_masses_milligrams_) {
        mass *= 1.0 - drained_fraction;
    }

    const std::array<double, 3> uptake_rates{
        config_.nitrogen_uptake_milligrams_per_hour,
        config_.phosphorus_uptake_milligrams_per_hour,
        config_.potassium_uptake_milligrams_per_hour,
    };
    for (std::size_t index = 0; index < nutrient_masses_milligrams_.size();
         ++index) {
        const double uptake =
            uptake_rates[index] * uptake_activity * delta_hours;
        nutrient_masses_milligrams_[index] = std::max(
            0.0,
            nutrient_masses_milligrams_[index] - uptake);
    }

    const double effective_root_water_liters = std::max(
        root_water_liters_after,
        config_.minimum_effective_root_water_liters);
    state_.nitrogen_mg_per_liter = std::clamp(
        nutrient_masses_milligrams_[0] / effective_root_water_liters,
        0.0,
        config_.maximum_nutrient_concentration_mg_per_liter);
    state_.phosphorus_mg_per_liter = std::clamp(
        nutrient_masses_milligrams_[1] / effective_root_water_liters,
        0.0,
        config_.maximum_nutrient_concentration_mg_per_liter);
    state_.potassium_mg_per_liter = std::clamp(
        nutrient_masses_milligrams_[2] / effective_root_water_liters,
        0.0,
        config_.maximum_nutrient_concentration_mg_per_liter);

    state_.ph = std::clamp(state_.ph, 3.0, 9.0);
    state_.ec_ms_cm = std::clamp(state_.ec_ms_cm, 0.0, 8.0);
}

const EnvironmentState& EnvironmentSimulator::state() const noexcept {
    return state_;
}

}  // namespace smarthydro
