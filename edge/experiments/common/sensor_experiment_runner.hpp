#pragma once

#include "smarthydro/sensor_simulator.hpp"

#include <functional>
#include <optional>
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
 * @brief Esegue un esperimento interattivo su un singolo sensore.
 * @param config Metadati della misura e dei file prodotti.
 * @param select_measurement Funzione che seleziona la misura dalle letture aggregate.
 * @return 0 in caso di simulazione completata, 1 in caso di errore.
 */
int run_sensor_experiment(
    const SensorExperimentConfig& config,
    const std::function<std::optional<double>(const SensorReadings&)>& select_measurement);

/**
 * @brief Registra tutte le misure di un ambiente con attuatori spenti.
 * @return 0 in caso di simulazione completata, 1 in caso di errore.
 */
int run_environment_experiment();

}  // namespace smarthydro::experiments
