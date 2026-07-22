#pragma once

#include <optional>
#include <string>

namespace smarthydro {

/**
 * @brief Stato istantaneo degli attuatori simulati.
 */
struct ActuatorState {
    /** Potenza della pompa dell'acqua, tra 0% e 100%. */
    double water_pump_percent = 0.0;

    /** Identificativo del concime selezionato, oppure nessun valore. */
    std::optional<std::string> selected_fertilizer_id;

    /** Potenza del dosatore di concime, tra 0% e 100%. */
    double fertilizer_dosing_percent = 0.0;

    /** Potenza dell'illuminazione, tra 0% e 100%. */
    double lighting_percent = 0.0;
};

/**
 * @brief Simula gli attuatori di SmartHydro e il loro avvicinamento ai target.
 *
 * I comandi impostano uno stato obiettivo. Il metodo step() fa avanzare lo
 * stato effettivo verso tale obiettivo rispettando la velocita di variazione
 * prevista per ciascun attuatore.
 */
class ActuatorSimulator {
public:
    /** @brief Costruisce tutti gli attuatori nello stato sicuro, spento. */
    ActuatorSimulator() = default;

    /**
     * @brief Restituisce lo stato effettivo corrente.
     * @return Riferimento allo stato posseduto dal simulatore.
     */
    const ActuatorState& state() const noexcept;

    /**
     * @brief Restituisce lo stato obiettivo richiesto dai comandi.
     * @return Riferimento allo stato obiettivo posseduto dal simulatore.
     */
    const ActuatorState& target_state() const noexcept;

    /**
     * @brief Imposta la potenza obiettivo della pompa dell'acqua.
     * @param value Percentuale richiesta, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_water_pump_target_percent(double value);

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
     * @brief Imposta la potenza obiettivo del dosatore di concime.
     * @param value Percentuale richiesta, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     * @throws std::logic_error Se si richiede un valore positivo senza concime.
     */
    void set_fertilizer_dosing_target_percent(double value);

    /**
     * @brief Imposta la potenza obiettivo dell'illuminazione.
     * @param value Percentuale richiesta, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_lighting_target_percent(double value);

    /** @brief Fa avanzare di un passo tutti gli attuatori verso i target. */
    void step() noexcept;

    /** @brief Arresta immediatamente gli attuatori e azzera tutti i target. */
    void stop_all() noexcept;

private:
    ActuatorState state_;
    ActuatorState target_state_;
};

}  // namespace smarthydro
