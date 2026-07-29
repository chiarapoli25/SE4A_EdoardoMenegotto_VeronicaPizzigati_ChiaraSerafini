#pragma once

/**
 * @file event_bus.hpp
 * @brief Eventi di dominio, EventBus sincrono e observer dell'Edge.
 */

#include "smarthydro/edge_runtime.hpp"

#include <cstdint>
#include <fstream>
#include <functional>
#include <memory>
#include <mutex>
#include <ostream>
#include <string>
#include <unordered_map>
#include <variant>

namespace smarthydro {

/** @brief Campione periodico pubblicato al termine di un ciclo Edge. */
struct TelemetrySample {
    /** Zona che ha prodotto il campione. */
    std::string zone_id;
    /** Progressivo monotono del campione. */
    std::uint64_t sequence_number = 0;
    /** Timestamp simulato del campione, in secondi. */
    double timestamp_seconds = 0.0;
    /** Stato operativo associato al campione. */
    OperationalState operational_state = OperationalState::NOMINAL;
    /** Letture sincronizzate dei sensori. */
    SensorReadings readings;
    /** Comando logico corrente degli attuatori. */
    ActuatorCommand actuator_command;
    /** Uscita fisica corrente degli attuatori. */
    ActuatorOutput actuator_output;
    /** Stato ambientale raggiunto. */
    EnvironmentState environment_state;
};

/** @brief Transizione osservabile della macchina a stati operativa. */
struct StateChanged {
    /** Zona interessata dalla transizione. */
    std::string zone_id;
    /** Timestamp simulato della transizione, in secondi. */
    double timestamp_seconds = 0.0;
    /** Stato prima della transizione. */
    OperationalState previous_state = OperationalState::NOMINAL;
    /** Stato dopo la transizione. */
    OperationalState current_state = OperationalState::NOMINAL;
    /** Causa diagnostica della transizione. */
    std::string reason;
};

/** @brief Guasto strutturato prodotto da un detector presente o futuro. */
struct FaultDetected {
    /** Zona nella quale e stato rilevato il guasto. */
    std::string zone_id;
    /** Timestamp simulato del rilevamento, in secondi. */
    double timestamp_seconds = 0.0;
    /** Tipo stabile del guasto. */
    std::string fault_type;
    /** Severita usata dalla FSM. */
    ControlFaultSeverity severity = ControlFaultSeverity::NONE;
    /** Diagnostica leggibile e contestuale. */
    std::string diagnostic;
};

/** @brief Cambio della Strategy associata a una variabile controllata. */
struct StrategyChanged {
    /** Zona interessata dalla modifica. */
    std::string zone_id;
    /** Timestamp simulato della modifica, in secondi. */
    double timestamp_seconds = 0.0;
    /** Variabile il cui controllore e stato modificato. */
    ControlledVariable variable = ControlledVariable::SOIL_MOISTURE;
    /** Strategy precedente. */
    StrategyType previous_strategy = StrategyType::THRESHOLD;
    /** Nuova Strategy. */
    StrategyType current_strategy = StrategyType::THRESHOLD;
};

/** @brief Passaggio temporale fra due fasi della ricetta. */
struct RecipePhaseChanged {
    /** Zona che sta eseguendo la ricetta. */
    std::string zone_id;
    /** Timestamp simulato del passaggio, in secondi. */
    double timestamp_seconds = 0.0;
    /** Nome della fase precedente. */
    std::string previous_phase;
    /** Nome della nuova fase. */
    std::string current_phase;
};

/** @brief Ingresso della FSM nello stato EmergencyLockdown. */
struct EmergencyTriggered {
    /** Zona portata nello stato sicuro. */
    std::string zone_id;
    /** Timestamp simulato dell'emergenza, in secondi. */
    double timestamp_seconds = 0.0;
    /** Causa diagnostica dell'emergenza. */
    std::string reason;
};

/** @brief Fallimento di una consegna verso il backend. */
struct BackendUnavailable {
    /** Zona i cui dati non sono stati consegnati. */
    std::string zone_id;
    /** Timestamp dell'evento non consegnato, in secondi. */
    double timestamp_seconds = 0.0;
    /** Endpoint o identificatore del backend. */
    std::string endpoint;
    /** Diagnostica del fallimento. */
    std::string diagnostic;
};

/** @brief Comandi applicati correttamente durante un ciclo operativo. */
struct CommandExecuted {
    /** Zona nella quale sono stati applicati i comandi. */
    std::string zone_id;
    /** Timestamp simulato del comando, in secondi. */
    double timestamp_seconds = 0.0;
    /** Comando logico accettato. */
    ActuatorCommand command;
    /** Uscita fisica prodotta. */
    ActuatorOutput output;
    /** Acqua realmente erogata nel ciclo, in litri. */
    double delivered_water_liters = 0.0;
    /** Concentrati realmente erogati nel ciclo, in millilitri. */
    FertilizerValues<double> delivered_fertilizer_milliliters{};
};

/** @brief Comando rifiutato o non applicabile agli attuatori. */
struct CommandFailed {
    /** Zona nella quale il comando e fallito. */
    std::string zone_id;
    /** Timestamp simulato del fallimento, in secondi. */
    double timestamp_seconds = 0.0;
    /** Attuatore o gruppo di attuatori interessato. */
    std::string actuator;
    /** Diagnostica del fallimento. */
    std::string diagnostic;
};

/** @brief Unione chiusa degli eventi pubblicabili sul bus dell'Edge. */
using EdgeDomainEvent = std::variant<
    TelemetrySample,
    StateChanged,
    FaultDetected,
    StrategyChanged,
    RecipePhaseChanged,
    EmergencyTriggered,
    BackendUnavailable,
    CommandExecuted,
    CommandFailed>;

/** @brief Restituisce il nome stabile del tipo contenuto nell'evento. */
const char* event_type_name(const EdgeDomainEvent& event) noexcept;

/** @brief Contratto Observer per i consumer degli eventi Edge. */
class IEventObserver {
public:
    virtual ~IEventObserver() = default;

    /**
     * @brief Riceve sincronicamente un evento pubblicato.
     *
     * Le eccezioni non attraversano EventBus::publish(): un observer difettoso
     * non puo interrompere il ciclo di controllo.
     */
    virtual void on_event(const EdgeDomainEvent& event) = 0;
};

/**
 * @brief Bus Observer sincrono, thread-safe e isolato dalle eccezioni.
 *
 * Il bus conserva riferimenti deboli agli observer. La registrazione non ne
 * prolunga quindi artificialmente la vita.
 */
class EventBus {
public:
    /** Identificatore opaco di una sottoscrizione. */
    using SubscriptionId = std::uint64_t;

    /**
     * @brief Registra un observer.
     * @throws std::invalid_argument Se observer e nullo.
     */
    SubscriptionId subscribe(std::shared_ptr<IEventObserver> observer);

    /** @brief Rimuove una sottoscrizione; non fallisce se era gia assente. */
    void unsubscribe(SubscriptionId subscription_id) noexcept;

    /**
     * @brief Notifica tutti gli observer ancora vivi.
     *
     * La copia degli observer viene costruita sotto lock, mentre i callback
     * sono eseguiti senza lock per consentire pubblicazioni rientranti.
     */
    void publish(const EdgeDomainEvent& event) noexcept;

private:
    std::mutex mutex_;
    std::unordered_map<
        SubscriptionId,
        std::weak_ptr<IEventObserver>>
        observers_;
    SubscriptionId next_subscription_id_ = 1;
};

/** @brief Observer che rende gli eventi leggibili su uno stream testuale. */
class ConsoleLogger final : public IEventObserver {
public:
    /** @brief Collega il logger allo stream, che deve restare valido. */
    explicit ConsoleLogger(std::ostream& output);

    /** @copydoc IEventObserver::on_event() */
    void on_event(const EdgeDomainEvent& event) override;

private:
    std::ostream& output_;
};

/** @brief Observer che salva eventi e telemetria in un CSV uniforme. */
class CsvLogger final : public IEventObserver {
public:
    /** @brief Scrive sullo stream indicato, che deve restare valido. */
    explicit CsvLogger(std::ostream& output);

    /**
     * @brief Crea o tronca il file CSV indicato.
     * @throws std::runtime_error Se il file non puo essere aperto.
     */
    explicit CsvLogger(const std::string& file_path);

    /** @copydoc IEventObserver::on_event() */
    void on_event(const EdgeDomainEvent& event) override;

private:
    void write_header();

    std::unique_ptr<std::ofstream> owned_output_;
    std::ostream* output_ = nullptr;
};

/**
 * @brief Observer con trasporto iniettabile per la consegna al backend.
 *
 * Il SendFunction puo incapsulare HTTP, MQTT o una coda. Restituendo false o
 * sollevando un'eccezione produce BackendUnavailable sul medesimo EventBus.
 */
class BackendClient final : public IEventObserver {
public:
    /** Funzione che restituisce true quando la consegna ha successo. */
    using SendFunction = std::function<bool(const EdgeDomainEvent&)>;

    /**
     * @brief Costruisce il client con endpoint e trasporto iniettabile.
     * @throws std::invalid_argument Se endpoint o sender non sono validi.
     */
    BackendClient(
        EventBus& event_bus,
        std::string endpoint,
        SendFunction sender);

    /** @copydoc IEventObserver::on_event() */
    void on_event(const EdgeDomainEvent& event) override;

private:
    EventBus& event_bus_;
    std::string endpoint_;
    SendFunction sender_;
    bool reporting_failure_ = false;
};

}  // namespace smarthydro
