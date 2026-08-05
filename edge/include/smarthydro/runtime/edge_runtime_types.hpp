#pragma once

/**
 * @file edge_runtime_types.hpp
 * @brief Tipi di stato, eventi e risultati prodotti dal runtime Edge.
 */

#include <smarthydro/adapters/io_adapters.hpp>
#include <smarthydro/control/control_system.hpp>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace smarthydro {

/** @brief Stato operativo sintetico della zona controllata dall'Edge. */
enum class OperationalState {
    NOMINAL,
    DEGRADED,
    EMERGENCY_LOCKDOWN,
};

/** @brief Famiglia del componente sul quale viene simulata un'anomalia. */
enum class FaultTargetKind {
    SENSOR,
    ACTUATOR,
};

/** @brief Anomalie riproducibili supportate dalla simulazione Edge. */
enum class FaultMode {
    SENSOR_DROPOUT,
    SENSOR_STUCK,
    SENSOR_OFFSET,
    ACTUATOR_STUCK_OFF,
    ACTUATOR_STUCK_ON,
    ACTUATOR_SLOW_RESPONSE,
};

/**
 * @brief Descrizione utente di un'anomalia da applicare alla simulazione.
 *
 * `target` usa i nomi stabili dei sei sensori (`temperature`,
 * `air_humidity`, `soil_moisture`, `soil_conductivity`, `ph`, `light`) o degli attuatori
 * (`water_pump`, `lighting`, `nitrogen_valve`, `phosphorus_valve`,
 * `potassium_valve`, `ph_up_valve`, `ph_down_valve`).
 *
 * `value` e obbligatorio per sensor_offset e actuator_slow_response. Per
 * sensor_stuck e opzionale: se manca viene congelata la prima lettura.
 * `duration_seconds` assente rende il fault persistente fino a ResetFault.
 */
struct FaultSpecification {
    std::string fault_id;
    FaultTargetKind target_kind = FaultTargetKind::SENSOR;
    std::string target = "soil_moisture";
    FaultMode mode = FaultMode::SENSOR_DROPOUT;
    std::optional<double> value;
    std::optional<double> duration_seconds;
};

/** @brief Tipi di evento prodotti dal runtime locale in questa fase. */
enum class EdgeEventType {
    RUNTIME_STARTED,
    RECIPE_PHASE_CHANGED,
    RECIPE_COMPLETED,
    OPERATIONAL_STATE_CHANGED,
    EMERGENCY_LOCKDOWN_ENTERED,
};

/** @brief Evento osservabile prodotto durante un ciclo operativo. */
struct EdgeEvent {
    /** Categoria stabile dell'evento. */
    EdgeEventType type = EdgeEventType::RUNTIME_STARTED;
    /** Timestamp simulato al quale l'evento e stato rilevato. */
    double timestamp_seconds = 0.0;
    /** Descrizione leggibile destinata a log e diagnostica. */
    std::string message;
    /** Stato precedente, presente per una transizione operativa. */
    std::optional<OperationalState> previous_operational_state;
    /** Nuovo stato, presente per una transizione operativa. */
    std::optional<OperationalState> current_operational_state;
};

/**
 * @brief Soglie temporali, espresse in numero di cicli, della FSM operativa.
 */
struct OperationalStatePolicy {
    /**
     * Numero di cicli consecutivi con guasto recuperabile che causa il
     * passaggio da Degraded a EmergencyLockdown.
     */
    std::size_t recoverable_faults_before_lockdown = 3;
    /**
     * Numero di cicli sani consecutivi necessari per il recovery automatico
     * da Degraded a Nominal.
     */
    std::size_t healthy_steps_before_nominal = 3;
};

/**
 * @brief Risultato osservabile di un singolo ciclo operativo dell'Edge.
 *
 * Le letture e le decisioni appartengono all'inizio del passo; stato ambientale
 * e volumi erogati descrivono invece il risultato fisico alla fine del passo.
 */
struct EdgeStepResult {
    /** Progressivo monotono del campione prodotto dal runtime. */
    std::uint64_t sequence_number = 0;
    /** Tempo simulato all'inizio del ciclo, in secondi. */
    double start_time_seconds = 0.0;
    /** Durata del ciclo, in secondi. */
    double duration_seconds = 0.0;
    /** Nome della fase usata per calcolare i comandi. */
    std::string phase_name;
    /** True dopo il termine temporale dell'ultima fase. */
    bool recipe_completed = false;
    /** Stato operativo della zona durante il ciclo. */
    OperationalState operational_state = OperationalState::NOMINAL;
    /** Eventi prodotti all'inizio del ciclo. */
    std::vector<EdgeEvent> events;
    /** Campione sincronizzato letto prima del controllo. */
    SensorReadings readings;
    /** Decisione sicura per ciascuna delle sei variabili controllate. */
    ControlledValues<ControlDecision> decisions;
    /** Acqua realmente erogata nell'intero ciclo, in litri. */
    double delivered_water_liters = 0.0;
    /** Concentrati realmente erogati nell'intero ciclo, in millilitri. */
    FertilizerValues<double> delivered_fertilizer_milliliters{};
    /** Comando logico degli attuatori alla fine del ciclo. */
    ActuatorCommand actuator_command;
    /** Stato fisico degli attuatori alla fine del ciclo. */
    ActuatorOutput actuator_output;
    /** Stato ambientale raggiunto alla fine del ciclo. */
    EnvironmentState environment_state;
};

/** @brief Nome stabile dello stato operativo per log e serializzazione. */
const char* to_string(OperationalState state) noexcept;
/** @brief Nome JSON stabile della famiglia del componente guasto. */
const char* to_string(FaultTargetKind kind) noexcept;
/** @brief Nome JSON stabile della modalita di fault. */
const char* to_string(FaultMode mode) noexcept;
/** @brief Nome stabile del tipo di evento per log e serializzazione. */
const char* to_string(EdgeEventType type) noexcept;

}  // namespace smarthydro
