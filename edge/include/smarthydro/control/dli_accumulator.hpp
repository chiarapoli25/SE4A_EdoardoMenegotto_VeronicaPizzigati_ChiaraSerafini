#pragma once

/**
 * @file dli_accumulator.hpp
 * @brief Integratore del DLI (Daily Light Integral), separato dal resto.
 *
 * @details Il modulo possiede SOLO l'integrale nel tempo del PPFD ricevuto
 * dalla coltura, non la fisica che produce quel PPFD (EnvironmentSimulator,
 * modello della luce naturale + lampada), non la lettura strumentale
 * (SensorSimulator, canale LIGHT), non la decisione di accendere o spegnere
 * la lampada (RecipeControlSystem::execute(), ramo LIGHT) e non l'attuatore
 * stesso (ActuatorSimulator). Questa separazione e' deliberata: ciascuno di
 * quei componenti resta testabile ed evolvibile senza toccare gli altri —
 * per esempio, sostituire il modello della luce naturale con uno piu'
 * dettagliato non cambia una riga di questo file, e viceversa cambiare come
 * si azzera "un giorno" (oggi: confine assoluto ogni 86400s di tempo
 * simulato, vedi EdgeRuntime::reset_histories_if_needed()) non cambia una
 * riga del modello fisico.
 */

#include <cstdint>

namespace smarthydro {

/**
 * @brief Accumula PPFD nel tempo in DLI (mol/m^2), azzerandosi a comando.
 *
 * @details Implementa esattamente
 * \f[
 * \mathrm{DLI}_{k+1} = \mathrm{DLI}_k + \mathrm{PPFD}_k \, \Delta t_k \cdot 10^{-6},
 * \f]
 * con \f$\mathrm{PPFD}_k\f$ in umol/(m^2 s) e \f$\Delta t_k\f$ in secondi —
 * la stessa conversione umol->mol gia' usata altrove in questo progetto
 * per il target di fase (mol/m^2/giorno). Non sa cosa sia "un giorno": chi
 * lo usa chiama reset() al confine che ritiene giusto (per l'Edge, lo
 * stesso confine assoluto di giornata gia' usato per
 * daily_dose_milliliters_ — vedi EdgeRuntime::reset_histories_if_needed()).
 * Il valore accumulato non e' mai negativo: integrate() rifiuta un PPFD o
 * una durata negativi invece di lasciarli corrompere silenziosamente
 * l'accumulo.
 */
class DliAccumulator {
public:
    /**
     * @brief Integra un passo di PPFD nel DLI accumulato.
     *
     * @param ppfd_umol_m2_s PPFD istantaneo non negativo, in umol/(m^2 s).
     * @param delta_time_seconds Durata positiva del passo, in secondi.
     * @throws std::invalid_argument Se un valore non e' finito, se
     *     ppfd_umol_m2_s e' negativo o se delta_time_seconds non e'
     *     positivo.
     */
    void integrate(double ppfd_umol_m2_s, double delta_time_seconds);

    /**
     * @brief Riporta l'accumulo a zero — l'inizio di un nuovo giorno solare.
     */
    void reset() noexcept;

    /**
     * @brief DLI accumulato dall'ultimo reset() (o dalla costruzione), in
     * mol/m^2. Mai negativo.
     */
    double value_mol_m2() const noexcept;

private:
    double value_mol_m2_ = 0.0;
};

}  // namespace smarthydro
