#pragma once

/**
 * @file edge_runtime.hpp
 * @brief Ciclo operativo che collega ricetta, sensori, controllori e attuatori.
 */

#include <smarthydro/runtime/edge_runtime_types.hpp>
#include <smarthydro/control/dli_accumulator.hpp>
#include <smarthydro/faults/fault_detector.hpp>
#include <smarthydro/faults/fault_injector.hpp>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>

namespace smarthydro {

class EventBus;

/**
 * @brief Runtime locale dell'Edge Controller guidato da una ricetta.
 *
 * La classe realizza il ciclo:
 *
 * 1. lettura dei sensori e dei modelli N/P/K;
 * 2. esecuzione delle sei Strategy tramite RecipeControlSystem;
 * 3. applicazione dei comandi sicuri agli attuatori;
 * 4. avanzamento dell'ambiente;
 * 5. aggiornamento dello storico di dose giornaliero e di fase.
 *
 * Non dipende dal backend e puo quindi continuare a usare una ricetta JSON
 * locale quando la rete non e disponibile.
 */
class EdgeRuntime {
public:
    /**
     * @brief Costruisce il runtime nello stato fisico iniziale.
     *
     * Il tipo di substrato dell'ambiente viene sempre preso dalla ricetta,
     * sostituendo l'eventuale valore presente in environment_config.
     *
     * @param recipe Ricetta validata da acquisire.
     * @param actuator_config Configurazione fisica degli attuatori.
     * @param environment_config Configurazione del modello ambientale.
     * @param sensor_config Configurazione degli errori strumentali.
     * @param environment_seed Seed riproducibile dell'ambiente.
     * @param sensor_seed Seed riproducibile dei sensori.
     * @param state_policy Soglie della macchina a stati operativa.
     * @param detector_config Regole e soglie del FaultDetector.
     */
    explicit EdgeRuntime(
        Recipe recipe,
        ActuatorConfig actuator_config = {},
        EnvironmentConfig environment_config = {},
        SensorConfig sensor_config = {},
        std::uint32_t environment_seed = 0x53484D31U,
        std::uint32_t sensor_seed = 0x53484D32U,
        OperationalStatePolicy state_policy = {},
        FaultDetectorConfig detector_config = {});

    /**
     * @brief Costruisce il runtime con dipendenze conformi agli Adapter.
     *
     * Consente di sostituire singolarmente sensori, driver degli attuatori e
     * ambiente senza modificare il ciclo di controllo.
     *
     * @param recipe Ricetta validata da acquisire.
     * @param sensors Sei adapter non nulli, ordinati per SensorChannel.
     * @param actuators Driver aggregato non nullo degli attuatori.
     * @param environment Ambiente non nullo osservato e aggiornato dal runtime.
     * @param state_policy Soglie della macchina a stati operativa.
     * @param soil_probe_model Calibrazione usata per fondere le due sonde.
     * @param detector_config Regole e soglie del FaultDetector.
     * @throws std::invalid_argument Se una dipendenza manca o un sensore si
     * trova in una posizione diversa dal proprio canale.
     */
    EdgeRuntime(
        Recipe recipe,
        SensorAdapterArray sensors,
        std::unique_ptr<IActuator> actuators,
        std::unique_ptr<IEnvironment> environment,
        OperationalStatePolicy state_policy = {},
        SoilProbeModelConfig soil_probe_model = {},
        FaultDetectorConfig detector_config = {});

    /**
     * @brief Valida e conferma localmente tutte le configurazioni della ricetta.
     *
     * @throws std::runtime_error Se almeno una configurazione non puo essere
     * confermata.
     */
    void confirm_all_configurations();

    /**
     * @brief Esegue un ciclo completo di controllo e simulazione.
     *
     * @param delta_time_seconds Durata positiva e finita del ciclo.
     * @return Letture, decisioni, volumi erogati e stato finale.
     * @throws std::invalid_argument Se la durata non e positiva e finita.
     */
    EdgeStepResult step(double delta_time_seconds);

    /** @brief Espone l'orchestratore della ricetta per ispezione. */
    const RecipeControlSystem& control_system() const noexcept;
    /** @brief Espone lo stato ambientale corrente. */
    const EnvironmentState& environment_state() const noexcept;
    /** @brief Espone lo stato fisico corrente degli attuatori. */
    const ActuatorOutput& actuator_output() const noexcept;
    /** @brief Restituisce lo stato operativo corrente della zona. */
    OperationalState operational_state() const noexcept;
    /** @brief Restituisce il tempo trascorso nella ricetta corrente. */
    double elapsed_recipe_hours() const noexcept;
    /** @brief Indica che la durata dell'ultima fase e stata completata. */
    bool recipe_completed() const noexcept;
    /** @brief Restituisce il nome della fase attualmente selezionata. */
    const std::string& active_phase_name() const;
    /**
     * @brief Cambia la Strategy di una variabile e invalida le conferme.
     *
     * Pubblica StrategyChanged quando e collegato un EventBus.
     */
    void change_strategy(
        ControlledVariable variable,
        StrategyType strategy,
        ControllerParameters parameters);
    /**
     * @brief Sostituisce la ricetta e ne riavvia il tempo dalla prima fase.
     *
     * Il substrato non puo cambiare a runtime perche appartiene alla
     * configurazione fisica dell'ambiente.
     */
    void replace_recipe(Recipe recipe);
    /** @brief Conferma la configurazione della variabile indicata. */
    ConfirmationResult confirm_configuration(ControlledVariable variable);
    /** @brief Rifiuta la configurazione della variabile indicata. */
    void reject_configuration(ControlledVariable variable);
    /**
     * @brief Avanza immediatamente alla fase successiva della ricetta.
     * @return false quando la ricetta si trova gia nell'ultima fase.
     */
    bool advance_recipe_phase();
    /**
     * @brief Applica un'anomalia tipizzata a sensore o attuatore simulato.
     *
     * La FSM non viene modificata immediatamente: reagisce soltanto quando il
     * detector osserva il sintomo prodotto dal fault.
     */
    void inject_fault(FaultSpecification specification);
    /**
     * @brief Rimuove un guasto sintetico precedentemente iniettato.
     * @return true se il fault era attivo.
     */
    bool reset_injected_fault(const std::string& fault_id) noexcept;
    /** @brief Indica se il fault sintetico specificato e attivo. */
    bool has_injected_fault(const std::string& fault_id) const noexcept;
    /**
     * @brief Arresta subito gli attuatori ed entra in EmergencyLockdown.
     * @return true se il comando ha prodotto una nuova transizione.
     */
    bool trigger_emergency_stop(const std::string& reason);
    /**
     * @brief Richiede l'uscita manuale da EmergencyLockdown.
     *
     * La richiesta non riattiva direttamente gli attuatori. Il runtime attende
     * un ciclo privo di guasti, passa a Degraded e applica poi il normale
     * periodo di verifica prima di tornare Nominal.
     *
     * @return true se la richiesta e stata acquisita; false se il runtime non
     * si trova in EmergencyLockdown.
     */
    bool request_manual_reset() noexcept;
    /**
     * @brief Porta immediatamente tutti gli attuatori nello stato sicuro.
     *
     * Non modifica la FSM operativa: serve al lifecycle esterno della zona
     * per sospendere o terminare una coltivazione senza simulare un guasto.
     */
    void stop_all_actuators() noexcept;
    /**
     * @brief Collega un EventBus alla pubblicazione automatica del runtime.
     * @param event_bus Bus non nullo, condiviso con gli observer.
     * @param zone_id Identificatore non vuoto della zona.
     * @throws std::invalid_argument Se bus o identificatore non sono validi.
     */
    void attach_event_bus(
        std::shared_ptr<EventBus> event_bus,
        std::string zone_id = "zone-1");
    /** @brief Disattiva la pubblicazione senza modificare gli observer. */
    void detach_event_bus() noexcept;
    /** @brief Restituisce l'identificatore usato negli eventi di dominio. */
    const std::string& zone_id() const noexcept;
    /**
     * @brief Aggiorna il contesto esterno incluso nello snapshot telemetrico.
     *
     * Il lifecycle appartiene a ZoneController, mentre il runtime conosce la
     * FSM operativa. Il metodo mantiene separate le due responsabilita e
     * consente di produrre un unico campione atomico per il backend.
     */
    void set_snapshot_context(
        std::string lifecycle_state,
        double time_scale);
    /** @brief Dose cumulativa realmente erogata nella fase corrente. */
    double cumulative_phase_dose_milliliters(
        ControlledVariable variable) const;
    /** @brief Dose cumulativa realmente erogata nel giorno corrente. */
    double daily_dose_milliliters(ControlledVariable variable) const;

private:
    ControlRequest base_request(double delta_time_seconds) const;
    double elapsed_recipe_seconds() const noexcept;
    std::size_t active_phase_index(double elapsed_recipe_hours) const;
    double total_recipe_duration_hours() const noexcept;
    ControlledValues<ValueRange> active_safety_ranges() const;
    void reset_histories_if_needed();
    SensorReadings read_sensors();
    void reset_actuator_observation() noexcept;
    void record_actuator_observation(
        const ActuatorCommand& command,
        const ActuatorOutput& output) noexcept;
    void publish_detected_faults(
        const std::vector<DetectedFault>& faults,
        double timestamp_seconds);
    void apply_degraded_isolation(
        EdgeStepResult& result,
        const std::vector<DetectedFault>& faults);
    bool update_operational_state(
        EdgeStepResult& result,
        const std::vector<DetectedFault>& faults,
        bool count_recoverable_cycle = true);
    void transition_operational_state(
        OperationalState next_state,
        const std::string& reason,
        EdgeStepResult& result);
    void apply_safe_fallback(
        double delta_time_seconds,
        EdgeStepResult& result,
        bool replace_decisions);
    void apply_decisions(
        double delta_time_seconds,
        EdgeStepResult& result);
    void advance_physics(
        double delta_time_seconds,
        EdgeStepResult& result);
    void update_dose_histories(
        double delta_time_seconds,
        const EdgeStepResult& result);
    void publish_telemetry(const EdgeStepResult& result) noexcept;
    void publish_command_executed(
        const EdgeStepResult& result) noexcept;
    void publish_command_failed(
        double timestamp_seconds,
        const std::string& diagnostic) noexcept;

    RecipeControlSystem control_system_;
    std::unique_ptr<IActuator> actuators_;
    std::unique_ptr<IEnvironment> environment_;
    SoilProbeModelConfig soil_probe_model_;
    SensorAdapterArray sensors_;
    WaterPumpAdapter water_pump_;
    LightingAdapter lighting_;
    FertilizerValveAdapter fertilizer_valves_;
    OperationalStatePolicy state_policy_;
    FaultInjector fault_injector_;
    FaultDetector fault_detector_;
    ControlledValues<double> cumulative_phase_dose_milliliters_{};
    ControlledValues<double> daily_dose_milliliters_{};
    ControlledValues<double> seconds_since_last_dose_{};
    std::size_t history_phase_index_ = kControlledVariableCount;
    std::optional<std::size_t> reported_phase_index_;
    std::uint64_t history_day_index_ = 0;
    // Integratore del DLI (Daily Light Integral) maturato dall'inizio del
    // giorno solare corrente — componente separato apposta (vedi
    // dli_accumulator.hpp): EdgeRuntime lo alimenta con il PPFD combinato
    // naturale+lampada osservato ad ogni ciclo, senza possedere lui stesso
    // la matematica dell'integrazione. Azzerato nello stesso punto in cui
    // si azzera daily_dose_milliliters_ (reset_histories_if_needed(), sullo
    // stesso confine di giorno current_day != history_day_index_): la luce
    // non ha una fase di riferimento come le dosi, il target e' sempre
    // "oggi", quindi non serve un secondo indice di reset dedicato.
    DliAccumulator daily_light_accumulator_;
    // Ore di illuminazione SUPPLEMENTARE (lampada comandata accesa FUORI
    // dalla finestra di luce naturale — vedi Photoperiod) gia' erogate
    // oggi, in secondi per la stessa precisione di calcolo di
    // delta_time_seconds. Azzerato insieme a daily_light_accumulator_.
    // Non e' un DliAccumulator (misura ore comandate, non mol/m^2): resta
    // un contatore dedicato, piu' semplice di quanto giustifichi
    // condividere la stessa classe per una grandezza diversa.
    double daily_supplemental_lighting_seconds_ = 0.0;
    std::uint64_t next_sequence_number_ = 0;
    OperationalState operational_state_ = OperationalState::NOMINAL;
    std::size_t consecutive_recoverable_faults_ = 0;
    std::size_t consecutive_healthy_steps_ = 0;
    bool manual_reset_requested_ = false;
    double recipe_start_time_seconds_ = 0.0;
    double recipe_time_offset_seconds_ = 0.0;
    bool recipe_completed_ = false;
    SoilType active_substrate_ = SoilType::AERATED_UNIVERSAL;
    std::unordered_set<std::string> reported_runtime_faults_;
    ActuatorCommand detector_command_observation_;
    ActuatorOutput detector_output_observation_;
    std::vector<DetectedFault> previous_actuator_faults_;
    ActuatorOutput effective_actuator_output_;
    std::shared_ptr<EventBus> event_bus_;
    std::string zone_id_ = "zone-1";
    std::string snapshot_lifecycle_state_ = "Running";
    double snapshot_time_scale_ = 1.0;
};

}  // namespace smarthydro
