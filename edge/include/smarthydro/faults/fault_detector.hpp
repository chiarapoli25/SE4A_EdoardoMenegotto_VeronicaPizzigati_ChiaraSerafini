#pragma once

/**
 * @file fault_detector.hpp
 * @brief Rilevazione osservazionale di anomalie su sensori, modelli e attuatori.
 */

#include <smarthydro/control/control_system.hpp>
#include <smarthydro/simulation/actuator_simulator.hpp>
#include <smarthydro/simulation/sensor_simulator.hpp>

#include <array>
#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace smarthydro {

/** Canali osservati dal detector, inclusi i valori derivati dal modello. */
enum class ObservedValue : std::size_t {
    TEMPERATURE = 0,
    AIR_HUMIDITY,
    SOIL_MOISTURE,
    SOIL_BULK_EC,
    SOIL_EC,
    FERTILIZER_CONCENTRATION,
    NITROGEN,
    PHOSPHORUS,
    POTASSIUM,
    PH,
    LIGHT,
    COUNT,
};

constexpr std::size_t kObservedValueCount =
    static_cast<std::size_t>(ObservedValue::COUNT);

/** Limiti e dinamica massima plausibile di una singola grandezza. */
struct ObservedValuePolicy {
    ValueRange physical_range;
    double maximum_rate_per_second = 1.0;
    double frozen_tolerance = 1.0e-9;
    bool detect_frozen = true;
};

/** Soglie configurabili applicate dal detector. */
struct FaultDetectorConfig {
    FaultDetectorConfig();

    /** Cicli consecutivi senza valore prima della segnalazione. */
    std::size_t missing_cycles = 1;
    /** Cicli consecutivi invariati prima della segnalazione. */
    std::size_t frozen_cycles = 8;
    /** Cicli comandati senza alcuna risposta. */
    std::size_t actuator_no_response_cycles = 1;
    /** Cicli con risposta insufficiente prima della segnalazione. */
    std::size_t actuator_low_response_cycles = 2;
    /** Rapporto minimo tra risposta osservata e risposta nominale. */
    double minimum_actuator_response_ratio = 0.50;
    /** Tolleranza sotto la quale comando e risposta sono considerati nulli. */
    double actuator_zero_tolerance = 1.0e-9;
    std::array<ObservedValuePolicy, kObservedValueCount> values;
};

/** Evidenza prodotta dal detector e consumata dalla FSM. */
struct DetectedFault {
    std::string component;
    std::string rule;
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    std::string diagnostic;

    std::string key() const;
};

/**
 * @brief Detector stateful che osserva dati senza modificarli.
 *
 * Non conosce i fault iniettati e non modifica la FSM. Due sequenze di letture
 * e uscite uguali producono le stesse evidenze, indipendentemente dalla loro
 * origine simulata o hardware.
 */
class FaultDetector {
public:
    explicit FaultDetector(FaultDetectorConfig config = {});

    /** Osserva letture e limiti della fase attiva. */
    std::vector<DetectedFault> observe_readings(
        const SensorReadings& readings,
        const ControlledValues<ValueRange>& safety_ranges);

    /** Osserva comandi e uscite aggregate dell'intero ciclo. */
    std::vector<DetectedFault> observe_actuators(
        const ActuatorCommand& command,
        const ActuatorOutput& output,
        const ActuatorConfig& config,
        double delta_time_seconds);

    /** Azzera gli storici, per esempio quando viene caricata una ricetta. */
    void reset() noexcept;
    const FaultDetectorConfig& config() const noexcept;

private:
    struct ValueState {
        std::optional<double> previous_value;
        std::optional<double> previous_timestamp_seconds;
        std::size_t missing_count = 0;
        std::size_t frozen_count = 0;
    };

    struct ActuatorState {
        std::size_t no_response_count = 0;
        std::size_t low_response_count = 0;
    };

    FaultDetectorConfig config_;
    std::array<ValueState, kObservedValueCount> value_states_{};
    std::array<ActuatorState, 7> actuator_states_{};
};

std::size_t observed_value_index(ObservedValue value);
const char* to_string(ObservedValue value) noexcept;

}  // namespace smarthydro
