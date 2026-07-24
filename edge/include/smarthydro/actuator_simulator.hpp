#pragma once

/**
 * @file actuator_simulator.hpp
 * @brief Comandi, uscite fisiche e modello degli attuatori simulati.
 */

#include <optional>
#include <string>

namespace smarthydro {

/**
 * @brief Limiti fisici degli attuatori installati.
 *
 * Tutti i valori devono essere finiti e strettamente positivi. La
 * configurazione viene validata una sola volta dal costruttore.
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

/**
 * @brief Richieste logiche prodotte manualmente o da un controllore.
 *
 * I comandi descrivono cio che e stato richiesto; ActuatorOutput descrive
 * invece cio che l'attuatore sta erogando fisicamente. Per questo, durante
 * un'irrigazione, il volume richiesto rimane costante mentre il volume residuo
 * e la portata effettiva cambiano.
 */
struct ActuatorCommand {
    /**
     * Dose totale richiesta per l'irrigazione corrente, in litri. Non e una
     * portata e rimane memorizzata anche quando la pompa completa la dose,
     * finche una cancellazione o stop_all() non azzera il comando.
     */
    double requested_irrigation_volume_liters = 0.0;
    /** Comando del dosatore di concime, tra 0% e 100%. */
    double fertilizer_doser_percent = 0.0;
    /** Comando dell'illuminazione, tra 0% e 100%. */
    double lighting_percent = 0.0;
};

/**
 * @brief Stato e uscite fisiche effettivamente erogate dagli attuatori.
 *
 * Questa struttura e l'ingresso fisico di EnvironmentSimulator::step().
 * I campi `*_last_step` rappresentano quantita integrate nell'ultimo passo;
 * portata del concime e potenza luminosa rappresentano invece valori continui.
 */
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

    /**
     * Identificativo del concime selezionato, oppure nessun valore. La
     * selezione non garantisce che EnvironmentSimulator conosca il profilo.
     */
    std::optional<std::string> selected_fertilizer_id;

    /** Portata di concime concentrato, in millilitri all'ora. */
    double fertilizer_flow_milliliters_per_hour = 0.0;

    /** Potenza elettrica assorbita dalle lampade, in watt. */
    double lighting_power_watts = 0.0;
};

/**
 * @brief Simula una pompa ON/OFF a dose e attuatori continui ideali.
 *
 * @details Il controllore richiede alla pompa una dose in litri. La pompa si
 * accende immediatamente alla portata fissa configurata, mentre step() integra
 * la portata nel tempo e la spegne al completamento della dose.
 *
 * Dosatore e lampade sono attuatori continui ideali: un comando percentuale
 * viene trasformato immediatamente e linearmente in mL/h o watt. Il loro stato
 * non cambia durante step().
 *
 * @note Non vengono rappresentati ritardi di attuazione, guasti, isteresi
 * meccanica o errori di inseguimento.
 */
class ActuatorSimulator {
public:
    /**
     * @brief Costruisce tutti gli attuatori spenti con limiti predefiniti.
     *
     * Comandi e uscite sono inizializzati a zero e nessun concime e
     * selezionato.
     *
     * @param config Portata della pompa, dose massima e limiti degli attuatori.
     * @throws std::invalid_argument Se almeno un limite non e finito o non e
     * strettamente positivo.
     */
    explicit ActuatorSimulator(ActuatorConfig config = {});

    /**
     * @brief Restituisce i comandi correnti.
     * @return Riferimento costante valido per la vita del simulatore. Il
     * contenuto cambia quando viene impartito o annullato un comando.
     */
    const ActuatorCommand& command() const noexcept;

    /**
     * @brief Restituisce le uscite fisiche correnti.
     * @return Riferimento costante valido per la vita del simulatore. Dopo
     * step() contiene volume e tempo di accensione relativi solo a quel passo.
     */
    const ActuatorOutput& output() const noexcept;

    /**
     * @brief Restituisce i limiti fisici configurati.
     * @return Riferimento costante alla configurazione validata e immutabile.
     */
    const ActuatorConfig& config() const noexcept;

    /**
     * @brief Avvia una richiesta di irrigazione espressa come volume.
     *
     * La funzione accende immediatamente la pompa e inizializza il volume
     * residuo, ma non eroga ancora acqua: il volume consegnato viene calcolato
     * dalla successiva chiamata a step().
     *
     * @param volume_liters Volume positivo da erogare, in litri.
     * @throws std::invalid_argument Se il volume non e finito, positivo o
     *         supera ActuatorConfig::maximum_irrigation_volume_liters.
     * @throws std::logic_error Se una precedente irrigazione e ancora attiva.
     * @post output().water_pump_on e true, la portata e quella configurata e il
     * volume residuo coincide con volume_liters.
     */
    void request_irrigation_volume_liters(double volume_liters);

    /**
     * @brief Annulla l'irrigazione corrente e spegne immediatamente la pompa.
     *
     * Azzera richiesta, portata, volume residuo e contatori dell'ultimo passo.
     * Non modifica dosatore, selezione del concime o illuminazione.
     */
    void cancel_irrigation() noexcept;

    /**
     * @brief Stima il tempo necessario a completare l'irrigazione corrente.
     * @return `volume residuo / portata * 3600`, in secondi; zero se non esiste
     * un'irrigazione attiva.
     */
    double remaining_irrigation_time_seconds() const noexcept;

    /**
     * @brief Seleziona il concime utilizzabile dal dosatore.
     *
     * La funzione controlla soltanto che l'identificativo sia non vuoto.
     * L'appartenenza ai profili di EnvironmentConfig viene verificata
     * dall'ambiente quando la portata del dosatore e positiva.
     *
     * @param fertilizer_id Identificativo non vuoto del concime.
     * @throws std::invalid_argument Se fertilizer_id e vuoto.
     */
    void select_fertilizer(const std::string& fertilizer_id);

    /**
     * @brief Rimuove il concime selezionato e arresta immediatamente il dosaggio.
     *
     * Il comando percentuale e la portata tornano a zero. Irrigazione e
     * illuminazione non vengono modificate.
     */
    void clear_fertilizer_selection() noexcept;

    /**
     * @brief Imposta il comando del dosatore e aggiorna subito la portata.
     *
     * La conversione e lineare:
     * `portata = portata massima * value / 100`.
     * Il valore zero e sempre ammesso, anche senza concime selezionato.
     *
     * @param value Comando percentuale compreso tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     * @throws std::logic_error Se si richiede un valore positivo senza concime.
     */
    void set_fertilizer_doser_command_percent(double value);

    /**
     * @brief Imposta il comando delle lampade e aggiorna subito la potenza.
     *
     * La conversione e lineare:
     * `potenza = potenza massima * value / 100`.
     *
     * @param value Comando percentuale compreso tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_lighting_command_percent(double value);

    /**
     * @brief Fa avanzare la pompa ON/OFF e calcola il volume erogato nel passo.
     *
     * Se la pompa e spenta, azzera solamente i contatori `*_last_step`. Se la
     * dose termina prima della fine dell'intervallo, registra il tempo effettivo
     * di accensione, consegna solo il volume residuo e spegne la pompa.
     * Dosatore e lampade non richiedono integrazione e rimangono invariati.
     *
     * @param delta_time_seconds Durata positiva del passo, in secondi.
     * @throws std::invalid_argument Se la durata non e finita o positiva.
     * @post Il volume erogato non supera mai il volume residuo precedente.
     */
    void step(double delta_time_seconds);

    /**
     * @brief Riporta tutti gli attuatori alla condizione sicura.
     *
     * Azzera comandi e uscite, spegne pompa e lampade e rimuove la selezione
     * del concime. L'operazione e immediata e non puo generare eccezioni.
     */
    void stop_all() noexcept;

private:
    ActuatorConfig config_;
    ActuatorCommand command_;
    ActuatorOutput output_;
};

}  // namespace smarthydro
