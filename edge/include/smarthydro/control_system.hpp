#pragma once

/**
 * @file control_system.hpp
 * @brief Ricette versionate, configurazione Strategy e limiti di sicurezza.
 */

#include "smarthydro/controllers.hpp"
#include "smarthydro/environment_simulator.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace smarthydro {

/** @brief Variabili controllate da una ricetta SmartHydro. */
enum class ControlledVariable : std::size_t {
    SOIL_MOISTURE = 0,
    LIGHT,
    PH,
    NITROGEN,
    PHOSPHORUS,
    POTASSIUM,
    COUNT,
};

/** @brief Numero di variabili controllabili, escluso il sentinella COUNT. */
constexpr std::size_t kControlledVariableCount =
    static_cast<std::size_t>(ControlledVariable::COUNT);

/** @brief Collezione indicizzata con ControlledVariable. */
template <typename T>
using ControlledValues = std::array<T, kControlledVariableCount>;

/** @brief Origine del valore usato dal controllo. */
enum class SensorType {
    SOIL_MOISTURE_SENSOR,
    LIGHT_SENSOR,
    PH_SENSOR,
    NITROGEN_MODEL,
    PHOSPHORUS_MODEL,
    POTASSIUM_MODEL,
};

/** @brief Attuatore comandato dalla configurazione. */
enum class ActuatorType {
    WATER_PUMP,
    LIGHTING,
    PH_CORRECTOR_VALVES,
    NITROGEN_VALVE,
    PHOSPHORUS_VALVE,
    POTASSIUM_VALVE,
};

/** @brief Stato di approvazione della configurazione. */
enum class ConfirmationState {
    PENDING_CONFIRMATION,
    CONFIRMED,
    REJECTED,
    INVALID,
};

/** @brief Intervallo chiuso espresso nell'unita della variabile. */
struct ValueRange {
    /** Estremo inferiore incluso. */
    double minimum = 0.0;
    /** Estremo superiore incluso. */
    double maximum = 0.0;
};

/** @brief Fotoperiodo giornaliero della fase. */
struct Photoperiod {
    /** Ora di inizio, nell'intervallo [0, 24). */
    double start_hour = 6.0;
    /** Durata di luce richiesta, in ore. */
    double duration_hours = 16.0;
};

/** @brief Target e limiti di una variabile nella fase di crescita. */
struct PhaseVariableTarget {
    /** Variabile cui appartiene il target. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
    /** Valore nominale desiderato. */
    double setpoint = 0.0;
    /** Intervallo operativo accettabile. */
    ValueRange allowed_range;
    /** Intervallo oltre il quale il controllo viene bloccato. */
    ValueRange safety_range;
    /** Dose totale suggerita nella fase; zero per variabili non dosate. */
    double suggested_phase_dose_milliliters = 0.0;
};

/** @brief Limiti prioritari applicati dopo il calcolo Strategy. */
struct OutputSafetyLimits {
    /** Massimo volume d'acqua accettabile per comando, in litri. */
    double maximum_water_volume_liters = 5.0;
    /** Massima durata continuativa della pompa, in secondi. */
    double maximum_pump_duration_seconds = 3600.0;
    /** Portata nominale usata per convertire durata e volume, in L/h. */
    double water_pump_flow_liters_per_hour = 2.0;
    /** Dose massima del prodotto per comando, in millilitri. */
    double maximum_dose_per_command_milliliters = 5.0;
    /** Dose massima del prodotto in un giorno, in millilitri. */
    double maximum_daily_dose_milliliters = 20.0;
    /** Intervallo minimo tra due dosaggi, in secondi. */
    double minimum_seconds_between_doses = 900.0;
    /** Tempo minimo di assestamento dopo una correzione pH, in secondi. */
    double ph_settling_time_seconds = 1800.0;
};

/**
 * @brief Strategia scelta, associazioni hardware e stato di conferma.
 */
struct ControllerConfiguration {
    /** Variabile governata dalla configurazione. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
    /** Sensore fisico o modello che fornisce il valore corrente. */
    SensorType sensor = SensorType::SOIL_MOISTURE_SENSOR;
    /** Attuatore destinatario del comando. */
    ActuatorType actuator = ActuatorType::WATER_PUMP;
    /** Strategia raccomandata dalla ricetta. */
    StrategyType default_strategy = StrategyType::THRESHOLD;
    /** Strategia scelta dall'agronomo. */
    StrategyType selected_strategy = StrategyType::THRESHOLD;
    /** Parametri tipizzati della strategia selezionata. */
    ControllerParameters parameters = ThresholdConfig{};
    /** Unita della variabile controllata. */
    std::string unit;
    /** Limiti prioritari applicati al comando calcolato. */
    OutputSafetyLimits output_limits;
    /** Stato della revisione agronomica. */
    ConfirmationState confirmation_state =
        ConfirmationState::PENDING_CONFIRMATION;
    /** Versione della singola configurazione. */
    std::uint64_t version = 1;
    /** Versione ricetta cui si riferisce l'ultima conferma. */
    std::uint64_t confirmed_recipe_version = 0;
};

/** @brief Fase ordinata della ricetta di coltivazione. */
struct RecipePhase {
    /** Nome stabile della fase di crescita. */
    std::string name;
    /** Durata della fase, in ore. */
    double duration_hours = 0.0;
    /** Finestra luminosa giornaliera. */
    Photoperiod photoperiod;
    /** Target delle sei variabili, indicizzati per tipo. */
    ControlledValues<PhaseVariableTarget> targets;
};

/** @brief Ricetta completa associata a pianta e substrato. */
struct Recipe {
    /** Identificatore stabile della ricetta. */
    std::string id;
    /** Specie o cultivar cui e destinata la ricetta. */
    std::string plant_type;
    /**
     * Tipo di substrato dichiarato dalla ricetta.
     *
     * Non esiste un fallback: una ricetta priva di questo valore e invalida.
     */
    std::optional<SoilType> substrate;
    /** Versione globale invalidante della ricetta. */
    std::uint64_t version = 1;
    /** Sequenza cronologica delle fasi di crescita. */
    std::vector<RecipePhase> phases;
    /** Configurazioni Strategy condivise dalle fasi. */
    ControlledValues<ControllerConfiguration> controllers;
};

/** @brief Esito di conferma o rifiuto richiesto dall'interfaccia agronomo. */
struct ConfirmationResult {
    /** True quando la configurazione e compatibile e valida. */
    bool success = false;
    /** Motivo leggibile del fallimento, vuoto in caso di successo. */
    std::string error;
};

/** @brief Input operativo aggiuntivo per sicurezza e fotoperiodo. */
struct ControlRequest {
    /** Valori e contesto forniti alla Strategy. */
    ControllerInput controller_input;
    /** Validita dichiarata del sensore o del modello. */
    bool source_valid = true;
    /** Tempo trascorso dall'inizio della ricetta, in ore. */
    double elapsed_recipe_hours = 0.0;
    /** Tempo assoluto della simulazione, in secondi. */
    double simulated_time_seconds = 0.0;
    /** Ora locale corrente nell'intervallo [0, 24). */
    double hour_of_day = 0.0;
    /** Dose del prodotto gia erogata oggi, in millilitri. */
    double daily_dose_milliliters = 0.0;
    /** Secondi trascorsi dall'ultimo dosaggio del prodotto. */
    double seconds_since_last_dose = 1.0e30;
    /** Indica che la valvola pH+ e fisicamente attiva. */
    bool ph_up_active = false;
    /** Indica che la valvola pH- e fisicamente attiva. */
    bool ph_down_active = false;
};

/** @brief Stato finale della decisione dopo i limiti prioritari. */
enum class ControlDecisionStatus {
    APPLIED,
    LIMITED,
    BLOCKED,
};

/** @brief Comando sicuro prodotto dall'orchestratore. */
struct ControlDecision {
    /** Esito dell'applicazione dei limiti prioritari. */
    ControlDecisionStatus status = ControlDecisionStatus::BLOCKED;
    /** Comando finale sicuro, nell'unita dell'attuatore. */
    double command = 0.0;
    /** Attuatore associato alla variabile. */
    ActuatorType actuator = ActuatorType::WATER_PUMP;
    /** Diagnostica del blocco o della limitazione. */
    std::string message;
    /** Stima futura prodotta dalla strategia predittiva. */
    std::optional<double> predicted_value;
};

/**
 * @brief Orchestratore Strategy di una ricetta versionata.
 *
 * Nessun controllore viene eseguito finche tutte le modifiche rilevanti non
 * sono state confermate. Il passaggio automatico di fase non cambia versione
 * e quindi non invalida l'approvazione.
 */
class RecipeControlSystem {
public:
    /** @brief Valida e acquisisce una ricetta, azzerandone le conferme. */
    explicit RecipeControlSystem(Recipe recipe);

    /** @brief Restituisce la ricetta e i suoi stati correnti. */
    const Recipe& recipe() const noexcept;
    /** @brief Restituisce la fase corrispondente al tempo trascorso. */
    const RecipePhase& active_phase(double elapsed_recipe_hours) const;

    /** @brief Conferma una configurazione se strategia e parametri sono validi. */
    ConfirmationResult confirm_configuration(ControlledVariable variable);
    /** @brief Registra il rifiuto esplicito di una configurazione. */
    void reject_configuration(ControlledVariable variable);

    /**
     * @brief Cambia Strategy e parametri, incrementando la versione ricetta.
     *
     * Tutte le conferme vengono invalidate per evitare configurazioni miste
     * approvate su versioni differenti.
     */
    void select_strategy(
        ControlledVariable variable,
        StrategyType strategy,
        ControllerParameters parameters);

    /** @brief Sostituisce la ricetta con una versione strettamente maggiore. */
    void replace_recipe(Recipe recipe);

    /** @brief Indica se tutte le conferme valgono per la versione corrente. */
    bool all_configurations_confirmed() const noexcept;

    /**
     * @brief Esegue il controllo e applica fotoperiodo e limiti prioritari.
     */
    ControlDecision execute(
        ControlledVariable variable,
        const ControlRequest& request);

    /** @brief Verifica struttura, unita, associazioni e parametri. */
    static void validate_recipe(const Recipe& recipe);

private:
    void invalidate_all_confirmations() noexcept;
    void rebuild_controllers(std::size_t phase_index);
    std::size_t phase_index(double elapsed_recipe_hours) const;

    Recipe recipe_;
    ControlledValues<std::unique_ptr<IController>> controllers_;
    std::size_t controller_phase_index_ = kControlledVariableCount;
};

/** @brief Converte una variabile nel suo indice verificato. */
std::size_t controlled_variable_index(ControlledVariable variable);
/** @brief Nome JSON stabile della variabile. */
const char* to_string(ControlledVariable variable) noexcept;
/** @brief Nome JSON stabile del sensore o modello. */
const char* to_string(SensorType sensor) noexcept;
/** @brief Nome JSON stabile dell'attuatore. */
const char* to_string(ActuatorType actuator) noexcept;
/** @brief Nome JSON stabile dello stato di conferma. */
const char* to_string(ConfirmationState state) noexcept;

}  // namespace smarthydro
