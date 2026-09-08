#include <smarthydro/faults/fault_detector.hpp>

#include <algorithm>
#include <cmath>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace smarthydro {
namespace {

constexpr std::size_t kActuatorCount = 7;

struct ValueObservation {
    ObservedValue channel;
    const std::optional<double>* value;
    std::optional<ValueRange> model_range;
};

bool is_nutrient(ObservedValue value) noexcept {
    return value == ObservedValue::NITROGEN ||
           value == ObservedValue::PHOSPHORUS ||
           value == ObservedValue::POTASSIUM;
}

std::string range_diagnostic(
    double value,
    const ValueRange& range,
    const std::string& label) {
    std::ostringstream output;
    output << label << " value " << value << " is outside ["
           << range.minimum << ", " << range.maximum << ']';
    return output.str();
}

std::string ratio_diagnostic(double ratio, double minimum) {
    std::ostringstream output;
    output << "observed response ratio " << ratio
           << " is below minimum " << minimum;
    return output.str();
}

void validate_policy(const ObservedValuePolicy& policy) {
    if (!std::isfinite(policy.physical_range.minimum) ||
        !std::isfinite(policy.physical_range.maximum) ||
        policy.physical_range.minimum > policy.physical_range.maximum ||
        !std::isfinite(policy.maximum_rate_per_second) ||
        policy.maximum_rate_per_second <= 0.0 ||
        !std::isfinite(policy.frozen_tolerance) ||
        policy.frozen_tolerance < 0.0) {
        throw std::invalid_argument("invalid observed value policy");
    }
}

}  // namespace

FaultDetectorConfig::FaultDetectorConfig() {
    values[observed_value_index(ObservedValue::TEMPERATURE)] =
        {{-50.0, 80.0}, 2.0, 1.0e-6, true};
    values[observed_value_index(ObservedValue::AIR_HUMIDITY)] =
        {{0.0, 100.0}, 10.0, 1.0e-6, true};
    values[observed_value_index(ObservedValue::SOIL_MOISTURE)] =
        {{0.0, 100.0}, 5.0, 1.0e-6, true};
    values[observed_value_index(ObservedValue::SOIL_BULK_EC)] =
        {{0.0, 8.0}, 1.0, 1.0e-6, true};
    values[observed_value_index(ObservedValue::SOIL_EC)] =
        {{0.0, 20.0}, 2.0, 1.0e-6, false};
    values[observed_value_index(ObservedValue::FERTILIZER_CONCENTRATION)] =
        {{0.0, 5000.0}, 500.0, 1.0e-6, false};
    values[observed_value_index(ObservedValue::NITROGEN)] =
        {{0.0, 10000.0}, 100.0, 1.0e-6, false};
    values[observed_value_index(ObservedValue::PHOSPHORUS)] =
        {{0.0, 10000.0}, 100.0, 1.0e-6, false};
    values[observed_value_index(ObservedValue::POTASSIUM)] =
        {{0.0, 10000.0}, 150.0, 1.0e-6, false};
    values[observed_value_index(ObservedValue::PH)] =
        {{0.0, 14.0}, 1.0, 1.0e-6, true};
    values[observed_value_index(ObservedValue::LIGHT)] =
        {{0.0, 3000.0}, 1000.0, 1.0e-6, true};
}

std::string DetectedFault::key() const {
    return component + ':' + rule;
}

std::size_t observed_value_index(ObservedValue value) {
    const auto index = static_cast<std::size_t>(value);
    if (index >= kObservedValueCount) {
        throw std::invalid_argument("invalid observed value");
    }
    return index;
}

const char* to_string(ObservedValue value) noexcept {
    switch (value) {
        case ObservedValue::TEMPERATURE: return "temperature_sensor";
        case ObservedValue::AIR_HUMIDITY: return "air_humidity_sensor";
        case ObservedValue::SOIL_MOISTURE: return "soil_moisture_sensor";
        case ObservedValue::SOIL_BULK_EC: return "soil_conductivity_sensor";
        case ObservedValue::SOIL_EC: return "soil_ec_model";
        case ObservedValue::FERTILIZER_CONCENTRATION:
            return "fertilizer_concentration_model";
        case ObservedValue::NITROGEN: return "nitrogen_model";
        case ObservedValue::PHOSPHORUS: return "phosphorus_model";
        case ObservedValue::POTASSIUM: return "potassium_model";
        case ObservedValue::PH: return "ph_sensor";
        case ObservedValue::LIGHT: return "light_sensor";
        case ObservedValue::COUNT: break;
    }
    return "unknown";
}

FaultDetector::FaultDetector(FaultDetectorConfig config)
    : config_(std::move(config)) {
    if (config_.missing_cycles == 0 || config_.frozen_cycles == 0 ||
        config_.actuator_no_response_cycles == 0 ||
        config_.actuator_low_response_cycles == 0 ||
        !std::isfinite(config_.minimum_actuator_response_ratio) ||
        config_.minimum_actuator_response_ratio <= 0.0 ||
        config_.minimum_actuator_response_ratio > 1.0 ||
        !std::isfinite(config_.actuator_zero_tolerance) ||
        config_.actuator_zero_tolerance < 0.0) {
        throw std::invalid_argument("invalid fault detector thresholds");
    }
    for (const auto& policy : config_.values) {
        validate_policy(policy);
    }
}

std::vector<DetectedFault> FaultDetector::observe_readings(
    const SensorReadings& readings,
    const ControlledValues<ValueRange>& safety_ranges) {
    const std::array<ValueObservation, kObservedValueCount> observations{{
        {ObservedValue::TEMPERATURE, &readings.temperature_c, std::nullopt},
        {ObservedValue::AIR_HUMIDITY, &readings.air_humidity_percent, std::nullopt},
        {ObservedValue::SOIL_MOISTURE, &readings.soil_moisture_percent,
         safety_ranges[controlled_variable_index(ControlledVariable::SOIL_MOISTURE)]},
        {ObservedValue::SOIL_BULK_EC, &readings.soil_bulk_ec_ms_cm, std::nullopt},
        {ObservedValue::SOIL_EC, &readings.soil_ec_ms_cm, std::nullopt},
        {ObservedValue::FERTILIZER_CONCENTRATION,
         &readings.fertilizer_concentration_mg_per_liter, std::nullopt},
        {ObservedValue::NITROGEN, &readings.nitrogen_estimate_mg_per_liter,
         safety_ranges[controlled_variable_index(ControlledVariable::NITROGEN)]},
        {ObservedValue::PHOSPHORUS, &readings.phosphorus_estimate_mg_per_liter,
         safety_ranges[controlled_variable_index(ControlledVariable::PHOSPHORUS)]},
        {ObservedValue::POTASSIUM, &readings.potassium_estimate_mg_per_liter,
         safety_ranges[controlled_variable_index(ControlledVariable::POTASSIUM)]},
        {ObservedValue::PH, &readings.ph,
         safety_ranges[controlled_variable_index(ControlledVariable::PH)]},
        // Niente model_range per la luce: il target di fase
        // (target.safety_range) e' ormai un DLI giornaliero (mol/m^2/giorno,
        // vedi RecipeControlSystem::execute()), non piu' un livello PPFD
        // istantaneo — confrontarlo con la lettura del momento (sempre
        // nell'ordine delle centinaia di umol/(m2 s)) farebbe scattare una
        // falsa violazione di sicurezza ad ogni ciclo. Resta comunque il
        // controllo sul range fisico del sensore (policy.physical_range,
        // sotto), che non e' cambiato.
        {ObservedValue::LIGHT, &readings.light_ppfd_umol_m2_s, std::nullopt},
    }};

    std::vector<DetectedFault> faults;
    for (const auto& observation : observations) {
        const auto index = observed_value_index(observation.channel);
        auto& state = value_states_[index];
        const auto& policy = config_.values[index];
        const auto component = std::string(to_string(observation.channel));

        if (!observation.value->has_value()) {
            ++state.missing_count;
            state.frozen_count = 0;
            if (state.missing_count >= config_.missing_cycles) {
                faults.push_back({
                    component,
                    "missing_value",
                    ControlFaultSeverity::RECOVERABLE,
                    "value is unavailable for " +
                        std::to_string(state.missing_count) +
                        " consecutive cycles",
                });
            }
            continue;
        }

        const double value = **observation.value;
        state.missing_count = 0;
        if (!std::isfinite(value)) {
            state.frozen_count = 0;
            faults.push_back({
                component,
                is_nutrient(observation.channel)
                    ? "non_finite_model_value"
                    : "non_finite_value",
                is_nutrient(observation.channel)
                    ? ControlFaultSeverity::RECOVERABLE
                    : ControlFaultSeverity::CRITICAL,
                "observed value is not finite",
            });
            continue;
        }
        if (value < policy.physical_range.minimum ||
            value > policy.physical_range.maximum) {
            state.frozen_count = 0;
            faults.push_back({
                component,
                "outside_physical_range",
                ControlFaultSeverity::CRITICAL,
                range_diagnostic(value, policy.physical_range, "physical"),
            });
        } else if (observation.model_range.has_value() &&
                   (value < observation.model_range->minimum ||
                    value > observation.model_range->maximum)) {
            state.frozen_count = 0;
            faults.push_back({
                component,
                is_nutrient(observation.channel)
                    ? "model_limit_incompatible"
                    : "outside_recipe_safety_range",
                is_nutrient(observation.channel)
                    ? ControlFaultSeverity::RECOVERABLE
                    : ControlFaultSeverity::CRITICAL,
                range_diagnostic(value, *observation.model_range, "model"),
            });
        } else {
            if (state.previous_value.has_value() &&
                state.previous_timestamp_seconds.has_value()) {
                const double elapsed = readings.timestamp_seconds -
                    *state.previous_timestamp_seconds;
                if (std::isfinite(elapsed) && elapsed > 0.0) {
                    const double rate = std::abs(
                        value - *state.previous_value) / elapsed;
                    if (rate > policy.maximum_rate_per_second) {
                        std::ostringstream diagnostic;
                        diagnostic << "rate " << rate
                                   << " exceeds maximum "
                                   << policy.maximum_rate_per_second;
                        faults.push_back({
                            component,
                            "maximum_rate_exceeded",
                            ControlFaultSeverity::RECOVERABLE,
                            diagnostic.str(),
                        });
                    }
                }
                if (policy.detect_frozen &&
                    std::abs(value - *state.previous_value) <=
                        policy.frozen_tolerance &&
                    value > policy.physical_range.minimum +
                        policy.frozen_tolerance &&
                    value < policy.physical_range.maximum -
                        policy.frozen_tolerance) {
                    ++state.frozen_count;
                } else {
                    state.frozen_count = 0;
                }
                if (policy.detect_frozen &&
                    state.frozen_count + 1 >= config_.frozen_cycles) {
                    faults.push_back({
                        component,
                        "frozen_value",
                        ControlFaultSeverity::RECOVERABLE,
                        "value remained unchanged for " +
                            std::to_string(state.frozen_count + 1) +
                            " consecutive cycles",
                    });
                }
            } else {
                state.frozen_count = 0;
            }
        }
        state.previous_value = value;
        state.previous_timestamp_seconds = readings.timestamp_seconds;
    }
    return faults;
}

std::vector<DetectedFault> FaultDetector::observe_actuators(
    const ActuatorCommand& command,
    const ActuatorOutput& output,
    const ActuatorConfig& config,
    double delta_time_seconds) {
    if (!std::isfinite(delta_time_seconds) || delta_time_seconds <= 0.0) {
        throw std::invalid_argument("detector step duration must be positive");
    }
    struct Observation {
        std::string component;
        bool commanded;
        bool active;
        double response_ratio;
    };
    const double tolerance = config_.actuator_zero_tolerance;
    const double expected_water = std::min(
        command.requested_irrigation_volume_liters,
        config.water_pump_flow_liters_per_hour *
            delta_time_seconds / 3600.0);
    std::array<Observation, kActuatorCount> observations;
    observations[0] = {
        "water_pump",
        command.requested_irrigation_volume_liters > tolerance,
        output.water_pump_on ||
            output.irrigation_volume_liters_last_step > tolerance,
        expected_water > tolerance
            ? output.irrigation_volume_liters_last_step / expected_water
            : 1.0,
    };
    const double expected_light = config.maximum_lighting_power_watts *
        command.lighting_percent / 100.0;
    observations[1] = {
        "lighting",
        command.lighting_percent > tolerance,
        output.lighting_power_watts > tolerance,
        expected_light > tolerance
            ? output.lighting_power_watts / expected_light
            : 1.0,
    };
    for (std::size_t index = 0; index < kFertilizerTypeCount; ++index) {
        const double nominal =
            config.fertilizer_flow_milliliters_per_hour[index];
        observations[index + 2] = {
            std::string(to_string(static_cast<FertilizerType>(index))) +
                "_valve",
            command.fertilizer_valves_open[index],
            output.fertilizer_valves_open[index] ||
                output.fertilizer_volume_milliliters_last_step[index] >
                    tolerance,
            nominal > tolerance
                ? output.fertilizer_flow_milliliters_per_hour[index] /
                    nominal
                : 1.0,
        };
    }

    std::vector<DetectedFault> faults;
    for (std::size_t index = 0; index < observations.size(); ++index) {
        const auto& observation = observations[index];
        auto& state = actuator_states_[index];
        if (!observation.commanded && observation.active) {
            state = {};
            faults.push_back({
                observation.component,
                "active_without_command",
                ControlFaultSeverity::CRITICAL,
                "actuator is physically active without a command",
            });
            continue;
        }
        if (!observation.commanded) {
            state = {};
            continue;
        }
        if (!observation.active) {
            ++state.no_response_count;
            state.low_response_count = 0;
            if (state.no_response_count >=
                config_.actuator_no_response_cycles) {
                faults.push_back({
                    observation.component,
                    "commanded_without_response",
                    ControlFaultSeverity::RECOVERABLE,
                    "actuator produced no response for " +
                        std::to_string(state.no_response_count) +
                        " commanded cycles",
                });
            }
            continue;
        }
        state.no_response_count = 0;
        if (!std::isfinite(observation.response_ratio) ||
            observation.response_ratio <
                config_.minimum_actuator_response_ratio) {
            ++state.low_response_count;
            if (state.low_response_count >=
                config_.actuator_low_response_cycles) {
                faults.push_back({
                    observation.component,
                    "persistent_low_response",
                    ControlFaultSeverity::RECOVERABLE,
                    ratio_diagnostic(
                        observation.response_ratio,
                        config_.minimum_actuator_response_ratio),
                });
            }
        } else {
            state.low_response_count = 0;
        }
    }
    return faults;
}

void FaultDetector::reset() noexcept {
    value_states_ = {};
    actuator_states_ = {};
}

const FaultDetectorConfig& FaultDetector::config() const noexcept {
    return config_;
}

}  // namespace smarthydro
