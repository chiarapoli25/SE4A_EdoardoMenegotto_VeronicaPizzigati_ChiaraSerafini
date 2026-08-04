#pragma once

/**
 * @file greenhouse_manager.hpp
 * @brief Gestione di piu zone Edge indipendenti nella stessa serra.
 */

#include <smarthydro/events/event_bus.hpp>
#include <smarthydro/runtime/runtime_commands.hpp>

#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace smarthydro {

/** Velocita temporale minima ammessa per una zona in simulazione. */
inline constexpr double kMinimumSimulationTimeScale = 1.0;
/** Velocita temporale massima ammessa per una zona in simulazione. */
inline constexpr double kMaximumSimulationTimeScale = 60.0;

/**
 * @brief Lifecycle applicativo di una zona, esterno alla FSM di sicurezza.
 *
 * RUNNING indica che esiste un EdgeRuntime eseguibile. Gli stati Nominal,
 * Degraded ed EmergencyLockdown restano responsabilita di EdgeRuntime.
 */
enum class ZoneLifecycleState {
    IDLE,
    STARTING,
    RUNNING,
    PAUSED,
    STOPPING,
    ERROR,
};

/** @brief Nome stabile del lifecycle per log e serializzazione. */
const char* to_string(ZoneLifecycleState state) noexcept;

/**
 * @brief Controllore completo e indipendente di una singola zona.
 *
 * Una zona puo essere registrata senza ricetta e restare inattiva. In tale
 * stato non possiede ambiente, sensori o attuatori e non produce cicli. Il
 * primo comando ActivateCultivation valido crea il relativo EdgeRuntime.
 * Soltanto l'EventBus puo essere condiviso con altre zone.
 */
class ZoneController {
public:
    /**
     * @brief Registra una zona inattiva, priva di ricetta e runtime.
     *
     * La zona mantiene gli attuatori spenti per costruzione: nessun adapter
     * viene creato fino al comando ActivateCultivation.
     */
    explicit ZoneController(
        std::string zone_id,
        std::shared_ptr<EventBus> event_bus = nullptr);

    /**
     * @brief Crea una zona basata sui simulatori standard.
     *
     * @param zone_id Identificatore univoco e non vuoto.
     * @param recipe Ricetta iniziale della zona.
     * @param actuator_config Configurazione degli attuatori.
     * @param environment_config Configurazione dell'ambiente.
     * @param sensor_config Configurazione dei sensori.
     * @param environment_seed Seed indipendente dell'ambiente.
     * @param sensor_seed Seed indipendente dei sensori.
     * @param state_policy Soglie della FSM operativa.
     * @param event_bus Bus condiviso opzionale.
     */
    ZoneController(
        std::string zone_id,
        Recipe recipe,
        ActuatorConfig actuator_config = {},
        EnvironmentConfig environment_config = {},
        SensorConfig sensor_config = {},
        std::uint32_t environment_seed = 0x53484D31U,
        std::uint32_t sensor_seed = 0x53484D32U,
        OperationalStatePolicy state_policy = {},
        std::shared_ptr<EventBus> event_bus = nullptr);

    /**
     * @brief Crea una zona con adapter iniettati.
     *
     * @param zone_id Identificatore univoco e non vuoto.
     * @param recipe Ricetta iniziale della zona.
     * @param sensors Adapter dei sei canali fisici.
     * @param actuators Driver aggregato degli attuatori.
     * @param environment Ambiente della zona.
     * @param state_policy Soglie della FSM operativa.
     * @param event_bus Bus condiviso opzionale.
     */
    ZoneController(
        std::string zone_id,
        Recipe recipe,
        SensorAdapterArray sensors,
        std::unique_ptr<IActuator> actuators,
        std::unique_ptr<IEnvironment> environment,
        OperationalStatePolicy state_policy = {},
        std::shared_ptr<EventBus> event_bus = nullptr);

    ZoneController(const ZoneController&) = delete;
    ZoneController& operator=(const ZoneController&) = delete;
    ZoneController(ZoneController&&) = delete;
    ZoneController& operator=(ZoneController&&) = delete;

    /** @brief Identificatore stabile della zona. */
    const std::string& id() const noexcept;
    /** @brief Stato corrente del lifecycle applicativo. */
    ZoneLifecycleState lifecycle_state() const noexcept;
    /** @brief True quando la zona possiede un runtime, anche se in pausa. */
    bool is_active() const noexcept;
    /** @brief True soltanto quando il runtime puo eseguire cicli. */
    bool is_running() const noexcept;
    /** @brief Identificativo della coltivazione attiva, oppure stringa vuota. */
    const std::string& cultivation_id() const noexcept;
    /** @brief Ultimo errore di lifecycle, oppure stringa vuota. */
    const std::string& last_error() const noexcept;
    /** @brief Rapporto corrente fra tempo simulato e tempo reale. */
    double time_scale() const noexcept;
    /**
     * @brief Imposta una velocita finita nell'intervallo [1, 60].
     *
     * La modifica e consentita soltanto a una coltivazione Running o Paused.
     * Il valore zero deve essere rappresentato con PauseCultivation.
     */
    void set_time_scale(double time_scale);
    /** @brief Durata simulata configurata, oppure modalita continua. */
    std::optional<double> simulation_duration_seconds() const noexcept;
    /** @brief Timestamp simulato di arrivo, oppure nessun limite. */
    std::optional<double> simulation_target_timestamp_seconds()
        const noexcept;
    /** @brief Secondi simulati ancora da eseguire, oppure nessun limite. */
    std::optional<double> remaining_simulation_seconds() const noexcept;
    /**
     * @brief Imposta una durata positiva e finita, o rimuove il limite.
     *
     * La finestra parte dal timestamp gia applicato al runtime. La modifica e
     * consentita in Running e Paused.
     */
    void set_simulation_duration(
        std::optional<double> duration_seconds);
    /**
     * @brief Completa la finestra corrente e mette in pausa la zona.
     *
     * Metodo destinato allo scheduler dopo l'applicazione dell'ultimo passo.
     */
    void complete_simulation_duration();
    /**
     * @brief Runtime della zona per ispezione o configurazione locale.
     * @throws std::logic_error Quando la zona e ancora inattiva.
     */
    EdgeRuntime& runtime();
    /** @copydoc runtime() */
    const EdgeRuntime& runtime() const;
    /** @brief Conferma tutte le configurazioni della ricetta locale. */
    void confirm_all_configurations();
    /** @brief Esegue un solo ciclo della zona. */
    EdgeStepResult step(double delta_time_seconds);
    /** @brief Esegue un comando usando la cache idempotente della zona. */
    RuntimeCommandResult execute_command(
        const RuntimeCommandEnvelope& envelope);
    /** @brief Collega la zona al bus indicato. */
    void attach_event_bus(std::shared_ptr<EventBus> event_bus);

private:
    static std::string require_zone_id(std::string zone_id);
    void transition_lifecycle(
        ZoneLifecycleState next_state,
        std::string reason) noexcept;
    RuntimeCommandResult execute_command_once(
        const RuntimeCommandEnvelope& envelope) noexcept;
    void activate_cultivation(
        std::string cultivation_id,
        Recipe recipe);
    void pause_cultivation();
    void resume_cultivation();
    void stop_cultivation();
    void publish_time_scale_changed(
        double previous_time_scale,
        double current_time_scale) noexcept;
    void publish_simulation_duration_changed() noexcept;
    void publish_simulation_duration_completed(
        double duration_seconds) noexcept;

    std::string zone_id_;
    std::string cultivation_id_;
    std::string last_error_;
    ZoneLifecycleState lifecycle_state_ = ZoneLifecycleState::IDLE;
    double time_scale_ = 1.0;
    std::optional<double> simulation_duration_seconds_;
    std::optional<double> simulation_target_timestamp_seconds_;
    std::unique_ptr<EdgeRuntime> runtime_;
    std::unique_ptr<RuntimeCommandProcessor> command_processor_;
    std::shared_ptr<EventBus> event_bus_;
    mutable std::mutex command_mutex_;
    std::unordered_map<std::string, RuntimeCommandResult> command_results_;
};

/** Risultati di un tick, indicizzati per identificatore di zona. */
using GreenhouseStepResults = std::map<std::string, EdgeStepResult>;

/**
 * @brief Registro e coordinatore di piu ZoneController indipendenti.
 *
 * Il manager instrada tick e comandi, ma non contiene stato agronomico
 * condiviso. Un fault o una modifica applicata a una zona non cambia le altre.
 */
class GreenhouseManager {
public:
    /**
     * @brief Crea il manager con un EventBus condiviso.
     *
     * Se event_bus e nullo viene creato automaticamente un nuovo bus.
     */
    explicit GreenhouseManager(
        std::shared_ptr<EventBus> event_bus = nullptr);

    /**
     * @brief Registra una zona inattiva in attesa di ActivateCultivation.
     * @throws std::invalid_argument Se l'identificatore e gia registrato.
     */
    ZoneController& add_inactive_zone(std::string zone_id);

    /**
     * @brief Crea e registra una zona simulata.
     * @throws std::invalid_argument Se l'identificatore e gia registrato.
     */
    ZoneController& add_simulated_zone(
        std::string zone_id,
        Recipe recipe,
        ActuatorConfig actuator_config = {},
        EnvironmentConfig environment_config = {},
        SensorConfig sensor_config = {},
        std::uint32_t environment_seed = 0x53484D31U,
        std::uint32_t sensor_seed = 0x53484D32U,
        OperationalStatePolicy state_policy = {});

    /**
     * @brief Registra una zona costruita esternamente.
     * @throws std::invalid_argument Se la zona e nulla o duplicata.
     */
    ZoneController& add_zone(std::unique_ptr<ZoneController> zone);

    /** @brief Indica se l'identificatore e registrato. */
    bool contains(const std::string& zone_id) const noexcept;
    /** @brief Numero di zone registrate. */
    std::size_t size() const noexcept;
    /** @brief Elenco ordinato degli identificatori registrati. */
    std::vector<std::string> zone_ids() const;
    /** @brief Restituisce una zona o solleva std::out_of_range. */
    ZoneController& zone(const std::string& zone_id);
    /** @copydoc zone() */
    const ZoneController& zone(const std::string& zone_id) const;
    /** @brief Esegue un ciclo esclusivamente nella zona indicata. */
    EdgeStepResult step_zone(
        const std::string& zone_id,
        double delta_time_seconds);
    /** @brief Esegue un ciclo indipendente in tutte le zone, in ordine di ID. */
    GreenhouseStepResults step_all(double delta_time_seconds);
    /** @brief Inoltra un comando esclusivamente alla zona indicata. */
    RuntimeCommandResult execute_command(
        const std::string& zone_id,
        const RuntimeCommandEnvelope& envelope);
    /** @brief Bus condiviso usato dalle zone registrate. */
    const std::shared_ptr<EventBus>& event_bus() const noexcept;

private:
    std::shared_ptr<EventBus> event_bus_;
    std::map<std::string, std::unique_ptr<ZoneController>> zones_;
};

}  // namespace smarthydro
