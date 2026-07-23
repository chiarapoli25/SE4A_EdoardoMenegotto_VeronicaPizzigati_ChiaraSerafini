#include "experiment_output.hpp"

#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>

namespace smarthydro::experiments {
namespace {

std::string available_base_name(
    const std::filesystem::path& directory,
    const std::string& requested) {
    auto available = [&](const std::string& name) {
        return !std::filesystem::exists(directory / (name + ".csv")) &&
               !std::filesystem::exists(directory / (name + ".png"));
    };
    if (available(requested)) {
        return requested;
    }
    for (std::size_t run = 2;; ++run) {
        const std::string candidate = requested + "_run-" + std::to_string(run);
        if (available(candidate)) {
            return candidate;
        }
    }
}

std::string escape_gnuplot(const std::string& value) {
    std::string escaped;
    for (const char character : value) {
        if (character == '\\' || character == '"') {
            escaped.push_back('\\');
        }
        escaped.push_back(character);
    }
    return escaped;
}

std::string shell_quote(const std::string& value) {
    std::string quoted = "'";
    for (const char character : value) {
        if (character == '\'') {
            quoted += "'\\''";
        } else {
            quoted.push_back(character);
        }
    }
    return quoted + "'";
}

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

void validate(
    const ExperimentOutputConfig& config,
    const std::vector<double>& x_values,
    const std::vector<NumericSeries>& series) {
    if (config.base_name.empty() || config.title.empty() || config.x_csv_name.empty() ||
        config.x_label.empty() || config.y_label.empty() || x_values.empty() ||
        series.empty()) {
        throw std::invalid_argument("experiment output configuration must not be empty");
    }
    for (const double value : x_values) {
        if (!std::isfinite(value)) {
            throw std::invalid_argument("experiment x values must be finite");
        }
    }
    for (const auto& item : series) {
        if (item.csv_name.empty() || item.legend.empty() ||
            item.values.size() != x_values.size()) {
            throw std::invalid_argument("experiment series has invalid metadata or length");
        }
        for (const double value : item.values) {
            if (!std::isfinite(value)) {
                throw std::invalid_argument("experiment series values must be finite");
            }
        }
    }
}

}  // namespace

ExperimentArtifacts write_experiment_output(
    const ExperimentOutputConfig& config,
    const std::vector<double>& x_values,
    const std::vector<NumericSeries>& series) {
    validate(config, x_values, series);
    const std::filesystem::path output_directory = "experiment_results";
    std::filesystem::create_directories(output_directory);
    const std::string base_name = available_base_name(output_directory, config.base_name);
    const auto csv_path = output_directory / (base_name + ".csv");
    std::ofstream csv(csv_path);
    if (!csv) {
        throw std::runtime_error("impossibile creare il CSV: " + csv_path.string());
    }
    csv << config.x_csv_name;
    for (const auto& item : series) {
        csv << ',' << item.csv_name;
    }
    csv << '\n' << std::fixed << std::setprecision(6);
    for (std::size_t row = 0; row < x_values.size(); ++row) {
        csv << x_values[row];
        for (const auto& item : series) {
            csv << ',' << item.values[row];
        }
        csv << '\n';
    }
    csv.close();
    const auto absolute_csv = std::filesystem::absolute(csv_path);

    if (!gnuplot_available()) {
        std::cerr << "Avviso: gnuplot non disponibile; il CSV e stato comunque creato.\n";
        return {absolute_csv, std::nullopt};
    }

    const auto png_path = output_directory / (base_name + ".png");
    const auto script_path = output_directory / (base_name + ".gnuplot");
    std::ofstream script(script_path);
    if (!script) {
        throw std::runtime_error("impossibile creare lo script gnuplot");
    }
    script << "set encoding utf8\nset terminal pngcairo size 1280,720\n"
           << "set output \"" << escape_gnuplot(std::filesystem::absolute(png_path).string()) << "\"\n"
           << "set datafile separator ','\nset grid\n"
           << "set title \"" << escape_gnuplot(config.title) << "\"\n"
           << "set xlabel \"" << escape_gnuplot(config.x_label) << "\"\n"
           << "set ylabel \"" << escape_gnuplot(config.y_label) << "\"\nplot ";
    for (std::size_t index = 0; index < series.size(); ++index) {
        if (index > 0) {
            script << ", ";
        }
        script << (index == 0 ? "\"" + escape_gnuplot(absolute_csv.string()) + "\"" : "''")
               << " using 1:" << index + 2
               << " every ::1 with linespoints linewidth 2 title \""
               << escape_gnuplot(series[index].legend) << "\"";
    }
    script << '\n';
    script.close();

    const int result = std::system(("gnuplot " + shell_quote(script_path.string())).c_str());
    std::filesystem::remove(script_path);
    if (result != 0 || !std::filesystem::exists(png_path)) {
        std::cerr << "Avviso: gnuplot non ha generato correttamente il PNG.\n";
        return {absolute_csv, std::nullopt};
    }
    return {absolute_csv, std::filesystem::absolute(png_path)};
}

}  // namespace smarthydro::experiments
