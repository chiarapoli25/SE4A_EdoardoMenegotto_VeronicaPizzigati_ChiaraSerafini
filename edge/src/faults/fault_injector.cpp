#include <smarthydro/faults/fault_injector.hpp>

#include <cmath>
#include <stdexcept>
#include <utility>

namespace smarthydro {
namespace {

bool is_sensor_mode(FaultMode mode) noexcept {
    return mode == FaultMode::SENSOR_DROPOUT ||
           mode == FaultMode::SENSOR_STUCK ||
           mode == FaultMode::SENSOR_OFFSET;
}

bool is_actuator_mode(FaultMode mode) noexcept {
    return mode == FaultMode::ACTUATOR_STUCK_OFF ||
           mode == FaultMode::ACTUATOR_STUCK_ON ||
           mode == FaultMode::ACTUATOR_SLOW_RESPONSE;
}

bool is_sensor_target(const std::string& target) noexcept {
    return target == "temperature" || target == "air_humidity" ||
           target == "soil_moisture" ||
           target == "soil_conductivity" || target == "ph" ||
           target == "light";
}

std::optional<FertilizerType> fertilizer_target(
    const std::string& target) noexcept {
    if (target == "nitrogen_valve") return FertilizerType::NITROGEN;
    if (target == "phosphorus_valve") return FertilizerType::PHOSPHORUS;
    if (target == "potassium_valve") return FertilizerType::POTASSIUM;
    if (target == "ph_up_valve") return FertilizerType::PH_UP;
    if (target == "ph_down_valve") return FertilizerType::PH_DOWN;
    return std::nullopt;
}

bool is_actuator_target(const std::string& target) noexcept {
    return target == "water_pump" || target == "lighting" ||
           fertilizer_target(target).has_value();
}

std::optional<double>* sensor_value(
    SensorReadings& readings,
    const std::string& target) noexcept {
    if (target == "temperature") return &readings.temperature_c;
    if (target == "air_humidity") return &readings.air_humidity_percent;
    if (target == "soil_moisture") return &readings.soil_moisture_percent;
    if (target == "soil_conductivity") return &readings.soil_bulk_ec_ms_cm;
    if (target == "ph") return &readings.ph;
    if (target == "light") return &readings.light_ppfd_umol_m2_s;
    return nullptr;
}

void validate(const FaultSpecification& specification) {
    if (specification.fault_id.empty() || specification.target.empty()) {
        throw std::invalid_argument(
            "fault specification requires identifier and target");
    }
    if (specification.target_kind == FaultTargetKind::SENSOR) {
        if (!is_sensor_mode(specification.mode) ||
            !is_sensor_target(specification.target)) {
            throw std::invalid_argument("invalid sensor fault specification");
        }
    } else if (!is_actuator_mode(specification.mode) ||
               !is_actuator_target(specification.target)) {
        throw std::invalid_argument("invalid actuator fault specification");
    }
    if (specification.duration_seconds.has_value() &&
        (!std::isfinite(*specification.duration_seconds) ||
         *specification.duration_seconds <= 0.0)) {
        throw std::invalid_argument(
            "fault duration must be positive and finite");
    }
    if (specification.value.has_value() &&
        !std::isfinite(*specification.value)) {
        throw std::invalid_argument("fault value must be finite");
    }
    if (specification.mode == FaultMode::SENSOR_OFFSET &&
        !specification.value.has_value()) {
        throw std::invalid_argument("sensor_offset requires a value");
    }
    if (specification.mode == FaultMode::ACTUATOR_SLOW_RESPONSE &&
        (!specification.value.has_value() ||
         *specification.value <= 0.0 ||
         *specification.value >= 1.0)) {
        throw std::invalid_argument(
            "actuator_slow_response requires a factor in (0, 1)");
    }
}

}  // namespace

void FaultInjector::inject(
    FaultSpecification specification,
    double timestamp_seconds) {
    validate(specification);
    if (!std::isfinite(timestamp_seconds) || timestamp_seconds < 0.0) {
        throw std::invalid_argument("fault timestamp must be finite");
    }
    for (const auto& entry : active_faults_) {
        const auto& active = entry.second.specification;
        if (active.target_kind == specification.target_kind &&
            active.target == specification.target) {
            throw std::invalid_argument(
                "target already has an active injected fault");
        }
    }
    const auto identifier = specification.fault_id;
    std::optional<double> expires_at;
    if (specification.duration_seconds.has_value()) {
        expires_at = timestamp_seconds + *specification.duration_seconds;
    }
    const auto inserted = active_faults_.emplace(
        identifier,
        ActiveFault{std::move(specification), expires_at, std::nullopt});
    if (!inserted.second) {
        throw std::invalid_argument(
            "injected fault identifier is already active");
    }
}

bool FaultInjector::reset(const std::string& fault_id) noexcept {
    return !fault_id.empty() && active_faults_.erase(fault_id) != 0;
}

bool FaultInjector::contains(const std::string& fault_id) const noexcept {
    return active_faults_.find(fault_id) != active_faults_.end();
}

void FaultInjector::expire(double timestamp_seconds) {
    for (auto iterator = active_faults_.begin();
         iterator != active_faults_.end();) {
        if (iterator->second.expires_at_seconds.has_value() &&
            timestamp_seconds >= *iterator->second.expires_at_seconds) {
            iterator = active_faults_.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

void FaultInjector::alter_readings(
    SensorReadings& readings,
    double timestamp_seconds) {
    expire(timestamp_seconds);
    for (auto& entry : active_faults_) {
        auto& fault = entry.second;
        if (fault.specification.target_kind != FaultTargetKind::SENSOR) {
            continue;
        }
        auto* value = sensor_value(readings, fault.specification.target);
        if (!value) continue;
        if (fault.specification.mode == FaultMode::SENSOR_DROPOUT) {
            value->reset();
        } else if (fault.specification.mode == FaultMode::SENSOR_OFFSET) {
            if (value->has_value()) **value += *fault.specification.value;
        } else if (fault.specification.mode == FaultMode::SENSOR_STUCK) {
            if (!fault.latched_sensor_value.has_value()) {
                fault.latched_sensor_value =
                    fault.specification.value.has_value()
                        ? fault.specification.value
                        : *value;
            }
            if (fault.latched_sensor_value.has_value()) {
                *value = fault.latched_sensor_value;
            }
        }
    }
}

ActuatorOutput FaultInjector::alter_output(
    const ActuatorCommand& command,
    const ActuatorOutput& raw_output,
    const ActuatorConfig& config,
    double delta_time_seconds,
    double timestamp_seconds) {
    static_cast<void>(command);
    expire(timestamp_seconds);
    ActuatorOutput effective = raw_output;
    for (const auto& entry : active_faults_) {
        const auto& fault = entry.second.specification;
        if (fault.target_kind != FaultTargetKind::ACTUATOR) continue;
        const double factor = fault.value.value_or(1.0);
        if (fault.target == "water_pump") {
            if (fault.mode == FaultMode::ACTUATOR_STUCK_OFF) {
                effective.water_pump_on = false;
                effective.water_pump_flow_liters_per_hour = 0.0;
                effective.irrigation_volume_liters_last_step = 0.0;
                effective.water_pump_on_time_seconds_last_step = 0.0;
                effective.fertilizer_valves_open.fill(false);
                effective.fertilizer_flow_milliliters_per_hour.fill(0.0);
                effective.fertilizer_volume_milliliters_last_step.fill(0.0);
            } else if (fault.mode == FaultMode::ACTUATOR_STUCK_ON) {
                effective.water_pump_on = true;
                effective.water_pump_flow_liters_per_hour =
                    config.water_pump_flow_liters_per_hour;
                effective.water_pump_on_time_seconds_last_step =
                    delta_time_seconds;
                effective.irrigation_volume_liters_last_step =
                    config.water_pump_flow_liters_per_hour *
                    delta_time_seconds / 3600.0;
            } else {
                effective.water_pump_flow_liters_per_hour *= factor;
                effective.irrigation_volume_liters_last_step *= factor;
            }
        } else if (fault.target == "lighting") {
            if (fault.mode == FaultMode::ACTUATOR_STUCK_OFF) {
                effective.lighting_power_watts = 0.0;
            } else if (fault.mode == FaultMode::ACTUATOR_STUCK_ON) {
                effective.lighting_power_watts =
                    config.maximum_lighting_power_watts;
            } else {
                effective.lighting_power_watts *= factor;
            }
        } else if (const auto fertilizer = fertilizer_target(fault.target)) {
            const auto index = fertilizer_index(*fertilizer);
            if (fault.mode == FaultMode::ACTUATOR_STUCK_OFF) {
                effective.fertilizer_valves_open[index] = false;
                effective.fertilizer_flow_milliliters_per_hour[index] = 0.0;
                effective.fertilizer_volume_milliliters_last_step[index] = 0.0;
            } else if (fault.mode == FaultMode::ACTUATOR_STUCK_ON) {
                effective.fertilizer_valves_open[index] = true;
                if (effective.water_pump_on) {
                    effective.fertilizer_flow_milliliters_per_hour[index] =
                        config.fertilizer_flow_milliliters_per_hour[index];
                    effective.fertilizer_volume_milliliters_last_step[index] =
                        config.fertilizer_flow_milliliters_per_hour[index] *
                        delta_time_seconds / 3600.0;
                }
            } else {
                effective.fertilizer_flow_milliliters_per_hour[index] *= factor;
                effective.fertilizer_volume_milliliters_last_step[index] *= factor;
            }
        }
    }
    return effective;
}

}  // namespace smarthydro
