#include "experiment_utils.hpp"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace smarthydro::experiments {
namespace {

constexpr double kSampleIntervalHours = 0.25;

struct SimulationPlan {
    std::string label;
    std::string file_suffix;
    double duration_hours;
    double display_interval_hours;
};

struct Sample {
    double time_hours;
    double value;
};

std::string normalized_input(std::string value) {
    value.erase(
        value.begin(),
        std::find_if(value.begin(), value.end(), [](unsigned char character) {
            return !std::isspace(character);
        }));
    value.erase(
        std::find_if(value.rbegin(), value.rend(), [](unsigned char character) {
            return !std::isspace(character);
        }).base(),
        value.end());
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
        return static_cast<char>(std::tolower(character));
    });
    return value;
}

std::optional<SimulationPlan> request_plan() {
    while (true) {
        std::cout << "Scegli la durata della simulazione:\n"
                  << "  1) una giornata\n"
                  << "  2) una settimana\n"
                  << "Scelta: ";

        std::string input;
        if (!std::getline(std::cin, input)) {
            std::cerr << "Input terminato: simulazione annullata.\n";
            return std::nullopt;
        }

        input = normalized_input(input);
        if (input == "1" || input == "g" || input == "giorno" || input == "giornata") {
            return SimulationPlan{"una giornata", "day", 24.0, 6.0};
        }
        if (input == "2" || input == "s" || input == "settimana") {
            return SimulationPlan{"una settimana", "week", 24.0 * 7.0, 24.0};
        }

        std::cerr << "Scelta non valida. Inserisci 1 per una giornata o 2 per una settimana.\n\n";
    }
}

std::vector<Sample> collect_samples(
    const SimulationPlan& plan,
    const std::function<double()>& read_sample) {
    const auto sample_count =
        static_cast<std::size_t>(plan.duration_hours / kSampleIntervalHours) + 1;
    std::vector<Sample> samples;
    samples.reserve(sample_count);

    for (std::size_t index = 0; index < sample_count; ++index) {
        samples.push_back({index * kSampleIntervalHours, read_sample()});
    }
    return samples;
}

void print_significant_samples(
    const std::vector<Sample>& samples,
    const SimulationPlan& plan,
    const SensorExperimentConfig& config) {
    const auto display_stride =
        static_cast<std::size_t>(plan.display_interval_hours / kSampleIntervalHours);

    std::cout << "\nLetture significative (simulazione di " << plan.label << "):\n";
    for (std::size_t index = 0; index < samples.size(); ++index) {
        if (index % display_stride != 0 && index + 1 != samples.size()) {
            continue;
        }
        std::cout << "  t = " << std::setw(6) << std::fixed << std::setprecision(2)
                  << samples[index].time_hours << " h: " << std::setw(8)
                  << std::setprecision(3) << samples[index].value << ' '
                  << config.unit << '\n';
    }
}

std::filesystem::path write_csv(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const SensorExperimentConfig& config,
    const std::vector<Sample>& samples) {
    const auto csv_path = output_directory / (base_name + ".csv");
    std::ofstream csv(csv_path);
    if (!csv) {
        throw std::runtime_error("impossibile creare il file CSV: " + csv_path.string());
    }

    csv << "time_hours," << config.csv_value_column << '\n';
    csv << std::fixed << std::setprecision(6);
    for (const auto& sample : samples) {
        csv << sample.time_hours << ',' << sample.value << '\n';
    }
    if (!csv) {
        throw std::runtime_error("errore durante la scrittura del CSV: " + csv_path.string());
    }
    return std::filesystem::absolute(csv_path);
}

std::string escape_gnuplot_string(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size());
    for (const char character : value) {
        if (character == '\\' || character == '"') {
            escaped.push_back('\\');
        }
        escaped.push_back(character);
    }
    return escaped;
}

bool gnuplot_is_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

std::optional<std::filesystem::path> generate_plot(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const SensorExperimentConfig& config,
    const std::filesystem::path& csv_path) {
    if (!gnuplot_is_available()) {
        std::cerr << "\nAvviso: gnuplot non e installato o non e presente nel PATH.\n"
                  << "Il CSV e stato creato, ma il grafico PNG non e disponibile.\n";
        return std::nullopt;
    }

    const auto png_path = std::filesystem::absolute(output_directory / (base_name + ".png"));
    const auto script_path = output_directory / (base_name + ".gnuplot");
    std::ofstream script(script_path);
    if (!script) {
        throw std::runtime_error("impossibile creare lo script temporaneo di gnuplot");
    }

    script << "set encoding utf8\n"
           << "set terminal pngcairo size 1280,720\n"
           << "set output \"" << escape_gnuplot_string(png_path.string()) << "\"\n"
           << "set datafile separator ','\n"
           << "set title \"" << escape_gnuplot_string(config.title) << "\"\n"
           << "set xlabel \"Tempo simulato (ore)\"\n"
           << "set ylabel \"" << escape_gnuplot_string(config.measurement_name)
           << " (" << escape_gnuplot_string(config.unit) << ")\"\n"
           << "set grid\n"
           << "plot \"" << escape_gnuplot_string(csv_path.string())
           << "\" using 1:2 every ::1 with lines linewidth 2 title \""
           << escape_gnuplot_string(config.measurement_name) << "\"\n";
    script.close();

    const int result = std::system(("gnuplot " + script_path.string()).c_str());
    std::filesystem::remove(script_path);
    if (result != 0 || !std::filesystem::exists(png_path)) {
        std::cerr << "\nAvviso: gnuplot non ha generato correttamente il grafico PNG.\n";
        return std::nullopt;
    }
    return png_path;
}

}  // namespace

int run_sensor_experiment(
    const SensorExperimentConfig& config,
    const std::function<double()>& read_sample) {
    std::cout << "=== " << config.title << " ===\n";
    const auto plan = request_plan();
    if (!plan.has_value()) {
        return 1;
    }

    try {
        const auto samples = collect_samples(*plan, read_sample);
        print_significant_samples(samples, *plan, config);

        const std::filesystem::path output_directory = "experiment_results";
        std::filesystem::create_directories(output_directory);
        const std::string base_name = config.slug + "_" + plan->file_suffix;
        const auto csv_path = write_csv(output_directory, base_name, config, samples);
        const auto png_path = generate_plot(output_directory, base_name, config, csv_path);

        std::cout << "\nCSV salvato in: " << csv_path << '\n';
        if (png_path.has_value()) {
            std::cout << "Grafico salvato in: " << *png_path << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore durante l'esperimento: " << error.what() << '\n';
        return 1;
    }
}

}  // namespace smarthydro::experiments
