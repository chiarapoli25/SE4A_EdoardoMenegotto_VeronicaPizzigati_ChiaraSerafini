#pragma once

#include <optional>
#include <string>

namespace smarthydro {

/**
 * @brief Limiti fisici degli attuatori installati.
 */
struct ActuatorConfig {
    /** Portata fissa della pompa ON/OFF, in litri all'ora. */
    double water_pump_flow_liters_per_hour = 2.0;
    /** Volume massimo accettato per una singola irrigazione, in litri. */
    double maximum_irrigation_volume_liters = 5.0;
    /** Portata massima del dosatore, in millilitri all'ora. */
    double maximum_fertilizer_flow_milliliters_per_hour = 20.0;
    /** Potenza elettrica massima delle lampade, in watt. */
    double maximum_lighting_power_watts = 200.0;
};

/** @brief Comandi prodotti manualmente o da un controllore. */
struct ActuatorCommand {
    /** Volume d'acqua richiesto per l'irrigazione corrente, in litri. */
    double requested_irrigation_volume_liters = 0.0;
    /** Comando del dosatore di concime, tra 0% e 100%. */
    double fertilizer_doser_percent = 0.0;
    /** Comando dell'illuminazione, tra 0% e 100%. */
    double lighting_percent = 0.0;
};

/** @brief Stato e uscite fisiche effettivamente erogate dagli attuatori. */
struct ActuatorOutput {
    /** Indica se la pompa ON/OFF e attualmente accesa. */
    bool water_pump_on = false;
    /** Portata corrente della pompa, in litri all'ora; zero quando e spenta. */
    double water_pump_flow_liters_per_hour = 0.0;
    /** Volume realmente erogato nell'ultima chiamata a step(), in litri. */
    double irrigation_volume_liters_last_step = 0.0;
    /** Tempo di accensione della pompa nell'ultima step(), in secondi. */
    double water_pump_on_time_seconds_last_step = 0.0;
    /** Volume ancora da erogare per completare la richiesta, in litri. */
    double remaining_irrigation_volume_liters = 0.0;

    /** Identificativo del concime selezionato, oppure nessun valore. */
    std::optional<std::string> selected_fertilizer_id;

    /** Portata di concime concentrato, in millilitri all'ora. */
    double fertilizer_flow_milliliters_per_hour = 0.0;

    /** Potenza elettrica assorbita dalle lampade, in watt. */
    double lighting_power_watts = 0.0;
};

/**
 * @brief Simula una pompa ON/OFF a dose e attuatori continui ideali.
 *
 * Il controllore richiede alla pompa un volume in litri; il simulatore calcola
 * per quanto tempo mantenerla accesa alla portata configurata. Dosatore e
 * lampade convertono invece immediatamente comandi percentuali in portata e
 * potenza. Non vengono rappresentati guasti o errori di inseguimento.
 */
class ActuatorSimulator {
public:
    /**
     * @brief Costruisce tutti gli attuatori spenti con limiti predefiniti.
     * @param config Portata della pompa, dose massima e limiti degli attuatori.
     * @throws std::invalid_argument Se un limite non e finito o non e positivo.
     */
    explicit ActuatorSimulator(ActuatorConfig config = {});

    /**
     * @brief Restituisce i comandi correnti.
     * @return Riferimento ai comandi posseduti dal simulatore.
     */
    const ActuatorCommand& command() const noexcept;

    /**
     * @brief Restituisce le uscite fisiche correnti.
     * @return Riferimento alle uscite possedute dal simulatore.
     */
    const ActuatorOutput& output() const noexcept;

    /**
     * @brief Restituisce i limiti fisici configurati.
     * @return Riferimento alla configurazione posseduta dal simulatore.
     */
    const ActuatorConfig& config() const noexcept;

    /**
     * @brief Avvia una richiesta di irrigazione espressa come volume.
     * @param volume_liters Volume positivo da erogare, in litri.
     * @throws std::invalid_argument Se il volume non e finito, positivo o
     *         supera ActuatorConfig::maximum_irrigation_volume_liters.
     * @throws std::logic_error Se una precedente irrigazione e ancora attiva.
     */
    void request_irrigation_volume_liters(double volume_liters);

    /** @brief Annulla l'irrigazione corrente e spegne immediatamente la pompa. */
    void cancel_irrigation() noexcept;

    /**
     * @brief Stima il tempo necessario a completare l'irrigazione corrente.
     * @return Secondi rimanenti alla portata fissa configurata.
     */
    double remaining_irrigation_time_seconds() const noexcept;

    /**
     * @brief Seleziona il concime utilizzabile dal dosatore.
     * @param fertilizer_id Identificativo non vuoto del concime.
     * @throws std::invalid_argument Se fertilizer_id e vuoto.
     */
    void select_fertilizer(const std::string& fertilizer_id);

    /**
     * @brief Rimuove il concime selezionato e arresta immediatamente il dosaggio.
     */
    void clear_fertilizer_selection() noexcept;

    /**
     * @brief Imposta il comando del dosatore e aggiorna subito la portata.
     * @param value Comando percentuale compreso tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     * @throws std::logic_error Se si richiede un valore positivo senza concime.
     */
    void set_fertilizer_doser_command_percent(double value);

    /**
     * @brief Imposta il comando delle lampade e aggiorna subito la potenza.
     * @param value Comando percentuale compreso tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_lighting_command_percent(double value);

    /**
     * @brief Fa avanzare la pompa ON/OFF e calcola il volume erogato nel passo.
     * @param delta_time_seconds Durata positiva del passo, in secondi.
     * @throws std::invalid_argument Se la durata non e finita o positiva.
     */
    void step(double delta_time_seconds);

    /** @brief Azzera comandi, uscite fisiche e selezione del concime. */
    void stop_all() noexcept;

private:
    ActuatorConfig config_;
    ActuatorCommand command_;
    ActuatorOutput output_;
};

}  // namespace smarthydro
