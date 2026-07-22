#pragma once

#include <functional>
#include <string>

namespace smarthydro::experiments {

/** @brief Descrizione testuale e tecnica di una misura da simulare. */
struct SensorExperimentConfig {
    /** Nome breve usato per i file prodotti. */
    std::string slug;
    /** Titolo mostrato nel terminale e nel grafico. */
    std::string title;
    /** Nome della misura mostrato sull'asse verticale. */
    std::string measurement_name;
    /** Unita della misura mostrata sull'asse verticale. */
    std::string unit;
    /** Nome della colonna dei valori nel file CSV. */
    std::string csv_value_column;
};

/**
 * @brief Esegue il flusso comune di una dimostrazione di un sensore.
 * @param config Metadati della misura e dei file prodotti.
 * @param read_sample Funzione che ottiene una nuova lettura dalla libreria.
 * @return 0 se CSV e simulazione sono stati completati, 1 in caso di errore.
 *
 * L'assenza di gnuplot produce un avviso, ma non rende inutilizzabile il CSV e
 * non viene pertanto considerata un errore della simulazione.
 */
int run_sensor_experiment(
    const SensorExperimentConfig& config,
    const std::function<double()>& read_sample);

}  // namespace smarthydro::experiments
