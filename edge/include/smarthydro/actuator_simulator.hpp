#pragma once

#include <optional>
#include <string>

namespace smarthydro {

/**
 * @brief Stato istantaneo degli attuatori simulati.
 */
struct ActuatorState {
    /** Potenza della pompa di irrigazione, tra 0% e 100%. */
    double water_pump_percent = 0.0;

    /** Identificativo del concime selezionato, oppure nessun valore. */
    std::optional<std::string> selected_fertilizer_id;

    /** Potenza del dosatore di concime, tra 0% e 100%. */
    double fertilizer_dosing_percent = 0.0;

    /** Potenza dell'illuminazione, tra 0% e 100%. */
    double lighting_percent = 0.0;
};

/**
 * @brief Simula attuatori ideali che erogano immediatamente il comando ricevuto.
 *
 * In questo modello didattico non vengono rappresentati ritardi, guasti o
 * differenze tra comando richiesto e valore effettivamente erogato.
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
     * @brief Imposta immediatamente la potenza erogata dalla pompa.
     * @param value Percentuale da erogare, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_water_pump_percent(double value);

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
     * @brief Imposta immediatamente la potenza erogata dal dosatore.
     * @param value Percentuale da erogare, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     * @throws std::logic_error Se si richiede un valore positivo senza concime.
     */
    void set_fertilizer_dosing_percent(double value);

    /**
     * @brief Imposta immediatamente la potenza erogata dall'illuminazione.
     * @param value Percentuale da erogare, compresa tra 0 e 100.
     * @throws std::invalid_argument Se value non e finito o non e nell'intervallo.
     */
    void set_lighting_percent(double value);

    /** @brief Arresta immediatamente tutti gli attuatori. */
    void stop_all() noexcept;

private:
    ActuatorState state_;
};

}  // namespace smarthydro
