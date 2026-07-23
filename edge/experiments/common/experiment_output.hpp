#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace smarthydro::experiments {

/** @brief Serie numerica da salvare e rappresentare in un experiment. */
struct NumericSeries {
    /** Nome della colonna CSV. */
    std::string csv_name;
    /** Nome mostrato nella legenda del grafico. */
    std::string legend;
    /** Valori della serie, uno per ogni valore dell'asse orizzontale. */
    std::vector<double> values;
};

/** @brief Metadati di CSV e grafico condivisi dagli experiments. */
struct ExperimentOutputConfig {
    /** Nome base dei file, senza estensione. */
    std::string base_name;
    /** Titolo del grafico. */
    std::string title;
    /** Nome della prima colonna CSV. */
    std::string x_csv_name;
    /** Etichetta dell'asse orizzontale. */
    std::string x_label;
    /** Etichetta dell'asse verticale. */
    std::string y_label;
};

/** @brief Percorsi prodotti da un experiment. */
struct ExperimentArtifacts {
    /** Percorso assoluto del CSV. */
    std::filesystem::path csv_path;
    /** Percorso assoluto del PNG, assente quando gnuplot non e disponibile. */
    std::optional<std::filesystem::path> png_path;
};

/**
 * @brief Salva serie numeriche in CSV e genera, se possibile, un grafico PNG.
 * @param config Nomi dei file, titolo ed etichette degli assi.
 * @param x_values Valori dell'asse orizzontale.
 * @param series Serie da salvare; devono avere la stessa lunghezza di x_values.
 * @return Percorsi del CSV e dell'eventuale PNG.
 * @throws std::invalid_argument Se nomi, valori o lunghezze non sono validi.
 * @throws std::runtime_error Se non e possibile scrivere il CSV o lo script.
 */
ExperimentArtifacts write_experiment_output(
    const ExperimentOutputConfig& config,
    const std::vector<double>& x_values,
    const std::vector<NumericSeries>& series);

}  // namespace smarthydro::experiments
