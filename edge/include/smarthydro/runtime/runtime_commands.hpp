#pragma once

/**
 * @file runtime_commands.hpp
 * @brief Comandi operativi idempotenti ricevuti dall'Edge Controller.
 */

#include <smarthydro/runtime/edge_runtime.hpp>

#include <mutex>
#include <string>
#include <unordered_map>
#include <variant>

namespace smarthydro {

/** @brief Richiede il cambio della Strategy di una variabile. */
struct ChangeStrategyCommand {
    /** Variabile controllata da riconfigurare. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
    /** Nuova Strategy da selezionare. */
    StrategyType strategy = StrategyType::THRESHOLD;
    /** Parametri tipizzati compatibili con la nuova Strategy. */
    ControllerParameters parameters = ThresholdConfig{};
};

/** @brief Richiede l'acquisizione di una nuova versione della ricetta. */
struct LoadRecipeCommand {
    /** Ricetta completa, gia deserializzata dal livello di trasporto. */
    Recipe recipe;
};

/**
 * @brief Attiva una coltivazione in una zona Edge precedentemente inattiva.
 *
 * La ricetta viene scaricata dal livello HTTP prima che il comando raggiunga
 * il GreenhouseManager. L'attivazione crea un nuovo runtime simulato e
 * conferma le configurazioni soltanto dopo averle validate localmente.
 */
struct ActivateCultivationCommand {
    /** Identificativo stabile della coltivazione assegnato dal backend. */
    std::string cultivation_id;
    /** Ricetta completa e versionata da usare nel nuovo runtime. */
    Recipe recipe;
};

/** @brief Sospende tempo simulato e attuatori della coltivazione. */
struct PauseCultivationCommand {};

/** @brief Riprende una coltivazione precedentemente sospesa. */
struct ResumeCultivationCommand {};

/** @brief Arresta la coltivazione e riporta la zona nello stato Idle. */
struct StopCultivationCommand {};

/** @brief Conferma una configurazione agronomica pendente. */
struct ConfirmConfigurationCommand {
    /** Variabile la cui configurazione deve essere confermata. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
};

/** @brief Rifiuta una configurazione agronomica pendente. */
struct RejectConfigurationCommand {
    /** Variabile la cui configurazione deve essere rifiutata. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
};

/** @brief Inietta un guasto sintetico persistente nella simulazione. */
struct InjectFaultCommand {
    /** Identificatore usato per riconoscere e rimuovere il fault. */
    std::string fault_id;
    /** Severita con cui la FSM deve valutare il fault. */
    ControlFaultSeverity severity = ControlFaultSeverity::RECOVERABLE;
    /** Diagnostica leggibile che accompagna gli eventi e le transizioni. */
    std::string diagnostic;
};

/** @brief Rimuove un guasto sintetico identificato. */
struct ResetFaultCommand {
    /** Identificatore del fault da rimuovere. */
    std::string fault_id;
};

/** @brief Forza il passaggio alla fase successiva della ricetta. */
struct AdvanceRecipePhaseCommand {};

/** @brief Richiede l'arresto sicuro immediato della zona. */
struct EmergencyStopCommand {
    /** Motivazione operativa riportata negli eventi di emergenza. */
    std::string reason = "runtime emergency stop requested";
};

/** @brief Richiede il recovery controllato da EmergencyLockdown. */
struct ResetEmergencyCommand {};

/** @brief Insieme chiuso dei comandi operativi supportati dall'Edge. */
using RuntimeCommand = std::variant<
    ChangeStrategyCommand,
    LoadRecipeCommand,
    ActivateCultivationCommand,
    PauseCultivationCommand,
    ResumeCultivationCommand,
    StopCultivationCommand,
    ConfirmConfigurationCommand,
    RejectConfigurationCommand,
    InjectFaultCommand,
    ResetFaultCommand,
    AdvanceRecipePhaseCommand,
    EmergencyStopCommand,
    ResetEmergencyCommand>;

/**
 * @brief Busta ricevuta dal runtime con chiave obbligatoria di idempotenza.
 */
struct RuntimeCommandEnvelope {
    /** Identificatore univoco assegnato dal mittente. */
    std::string command_id;
    /** Payload tipizzato da eseguire. */
    RuntimeCommand command;
};

/** @brief Stato finale stabile dell'esecuzione di un comando. */
enum class RuntimeCommandStatus {
    SUCCEEDED,
    REJECTED,
};

/** @brief Esito restituito per ogni comando, inclusi i replay idempotenti. */
struct RuntimeCommandResult {
    /** Identificatore copiato dalla richiesta. */
    std::string command_id;
    /** Nome stabile del payload ricevuto. */
    std::string command_type;
    /** Successo o rifiuto definitivo. */
    RuntimeCommandStatus status = RuntimeCommandStatus::REJECTED;
    /** True quando l'esito proviene dalla cache e il comando non e rieseguito. */
    bool replayed = false;
    /** Messaggio leggibile destinato a backend e dashboard. */
    std::string message;

    /** @brief True soltanto per un comando applicato con successo. */
    bool success() const noexcept {
        return status == RuntimeCommandStatus::SUCCEEDED;
    }
};

/**
 * @brief Ricevitore idempotente dei comandi operativi diretti a EdgeRuntime.
 *
 * Ogni command_id viene eseguito al massimo una volta. I tentativi successivi
 * restituiscono lo stesso stato e messaggio con replayed=true, anche quando il
 * primo tentativo era stato rifiutato.
 */
class RuntimeCommandProcessor {
public:
    /** @brief Collega il processore a un runtime che deve restare valido. */
    explicit RuntimeCommandProcessor(EdgeRuntime& runtime);

    /**
     * @brief Esegue o riproduce dalla cache il comando indicato.
     *
     * Le eccezioni di validazione non attraversano questo confine: diventano
     * un RuntimeCommandResult con stato REJECTED.
     */
    RuntimeCommandResult execute(const RuntimeCommandEnvelope& envelope);

    /** @brief Restituisce il numero di identificatori gia elaborati. */
    std::size_t processed_command_count() const;

private:
    RuntimeCommandResult execute_once(
        const RuntimeCommandEnvelope& envelope) noexcept;

    EdgeRuntime& runtime_;
    mutable std::mutex mutex_;
    std::unordered_map<std::string, RuntimeCommandResult> results_;
};

/** @brief Nome stabile del tipo di comando contenuto nella variant. */
const char* runtime_command_type(const RuntimeCommand& command) noexcept;
/** @brief Nome stabile dello stato di esecuzione. */
const char* to_string(RuntimeCommandStatus status) noexcept;

}  // namespace smarthydro
