#pragma once

/**
 * @file fault_injector.hpp
 * @brief Alterazione controllata dei simulatori, senza rilevazione o FSM.
 */

#include <smarthydro/runtime/edge_runtime_types.hpp>

#include <optional>
#include <string>
#include <unordered_map>

namespace smarthydro {

/**
 * @brief Applica esclusivamente gli effetti fisici dei fault sintetici.
 *
 * Il componente non pubblica eventi, non assegna severita e non conosce lo
 * stato operativo. Il FaultDetector osserva successivamente i sintomi.
 */
class FaultInjector {
public:
    void inject(
        FaultSpecification specification,
        double timestamp_seconds);
    bool reset(const std::string& fault_id) noexcept;
    bool contains(const std::string& fault_id) const noexcept;

    void alter_readings(
        SensorReadings& readings,
        double timestamp_seconds);
    ActuatorOutput alter_output(
        const ActuatorCommand& command,
        const ActuatorOutput& raw_output,
        const ActuatorConfig& config,
        double delta_time_seconds,
        double timestamp_seconds);

private:
    struct ActiveFault {
        FaultSpecification specification;
        std::optional<double> expires_at_seconds;
        std::optional<double> latched_sensor_value;
    };

    void expire(double timestamp_seconds);
    std::unordered_map<std::string, ActiveFault> active_faults_;
};

}  // namespace smarthydro
