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
#include <string>
#include <vector>

namespace smarthydro {

/**
 * @brief Controllore completo e indipendente di una singola zona.
 *
 * Ogni istanza possiede il proprio EdgeRuntime e quindi ambiente, sensori,
 * attuatori, ricetta, controllori, FSM, fault, storico e sequenze. Soltanto
 * l'EventBus puo essere condiviso con altre zone.
 */
class ZoneController {
public:
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
     * @param sensors Adapter dei cinque canali.
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
    /** @brief Runtime della zona per ispezione o configurazione locale. */
    EdgeRuntime& runtime() noexcept;
    /** @copydoc runtime() */
    const EdgeRuntime& runtime() const noexcept;
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

    std::string zone_id_;
    EdgeRuntime runtime_;
    RuntimeCommandProcessor command_processor_;
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
