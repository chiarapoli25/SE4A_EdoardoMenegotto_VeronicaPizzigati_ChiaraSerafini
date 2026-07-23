#include "sensor_experiment_runner.hpp"

#include "smarthydro/actuator_simulator.hpp"
#include "smarthydro/environment_simulator.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace smarthydro::experiments {
namespace {

constexpr double kSampleIntervalSeconds = 900.0;
constexpr double kSampleIntervalHours = kSampleIntervalSeconds / 3600.0;

struct SimulationPlan {
    std::string label;
    std::string file_suffix;
    double duration_hours;
    double display_interval_hours;
};

struct InteractiveSelection {
    std::optional<SimulationPlan> plan;
    std::optional<SoilType> soil_type;
    std::optional<std::uint32_t> seed;
    std::filesystem::path output_directory = "experiment_results";
};

struct Sample {
    double time_hours;
    std::optional<double> value;
};

struct EnvironmentSample {
    double time_hours;
    SensorReadings readings;
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

SimulationPlan parse_plan(const std::string& value) {
    const std::string input = normalized_input(value);
    if (input == "day" || input == "giorno" || input == "giornata" || input == "1") {
        return {"una giornata", "day", 24.0, 6.0};
    }
    if (input == "week" || input == "settimana" || input == "2") {
        return {"una settimana", "week", 24.0 * 7.0, 24.0};
    }
    throw std::invalid_argument("duration must be day or week");
}

SoilType parse_soil_type(const std::string& value) {
    const std::string input = normalized_input(value);
    if (input == "aerated-universal" || input == "universale" || input == "1") {
        return SoilType::AERATED_UNIVERSAL;
    }
    if (input == "draining" || input == "drenante" || input == "2") {
        return SoilType::DRAINING;
    }
    if (input == "organic-retentive" || input == "organico" || input == "ritentivo" ||
        input == "3") {
        return SoilType::ORGANIC_RETENTIVE;
    }
    throw std::invalid_argument(
        "soil must be aerated-universal, draining or organic-retentive");
}

std::uint32_t parse_seed(const std::string& value) {
    std::size_t consumed = 0;
    const auto parsed = std::stoull(value, &consumed);
    if (consumed != value.size() || parsed > std::numeric_limits<std::uint32_t>::max()) {
        throw std::invalid_argument("seed must be an unsigned 32-bit integer");
    }
    return static_cast<std::uint32_t>(parsed);
}

template <typename T, typename Parser>
std::optional<T> request_choice(const std::string& prompt, Parser parser) {
    while (true) {
        std::cout << prompt << "Scelta: ";
        std::string input;
        if (!std::getline(std::cin, input)) {
            std::cerr << "Input terminato: simulazione annullata.\n";
            return std::nullopt;
        }
        try {
            return parser(input);
        } catch (const std::invalid_argument&) {
            std::cerr << "Scelta non valida, riprova.\n\n";
        }
    }
}

std::optional<std::uint32_t> request_seed() {
    while (true) {
        std::cout << "Seed riproducibile (invio per generarne uno casuale): ";
        std::string input;
        if (!std::getline(std::cin, input)) {
            std::cerr << "Input terminato: simulazione annullata.\n";
            return std::nullopt;
        }
        input = normalized_input(input);
        if (input.empty()) {
            const std::uint32_t seed = std::random_device{}();
            std::cout << "Seed generato: " << seed << '\n';
            return seed;
        }
        try {
            const std::uint32_t seed = parse_seed(input);
            std::cout << "Seed usato: " << seed << '\n';
            return seed;
        } catch (const std::exception&) {
            std::cerr << "Seed non valido: inserisci un intero tra 0 e 4294967295.\n\n";
        }
    }
}

bool complete_interactive_selection(InteractiveSelection& options) {
    if (!options.plan.has_value()) {
        options.plan = request_choice<SimulationPlan>(
            "Scegli la durata della simulazione:\n  1) una giornata\n  2) una settimana\n",
            parse_plan);
        if (!options.plan.has_value()) {
            return false;
        }
    }
    if (!options.soil_type.has_value()) {
        options.soil_type = request_choice<SoilType>(
            "Scegli il tipo di terriccio:\n"
            "  1) substrato universale aerato\n"
            "  2) substrato drenante\n"
            "  3) substrato organico ritentivo\n",
            parse_soil_type);
        if (!options.soil_type.has_value()) {
            return false;
        }
    }
    if (!options.seed.has_value()) {
        options.seed = request_seed();
        if (!options.seed.has_value()) {
            return false;
        }
    }
    return true;
}

EnvironmentConfig environment_config(const InteractiveSelection& options) {
    auto config = make_default_tomato_environment_config();
    config.soil_type = *options.soil_type;
    return config;
}

std::string run_suffix(
    const InteractiveSelection& options) {
    return options.plan->file_suffix + "_natural_soil-" +
           to_string(*options.soil_type) + "_seed-" +
           std::to_string(*options.seed);
}

std::string available_base_name(
    const std::filesystem::path& output_directory,
    const std::string& desired_name) {
    auto is_available = [&](const std::string& candidate) {
        return !std::filesystem::exists(output_directory / (candidate + ".csv")) &&
               !std::filesystem::exists(output_directory / (candidate + ".png"));
    };
    if (is_available(desired_name)) {
        return desired_name;
    }
    for (std::size_t run = 2;; ++run) {
        const std::string candidate = desired_name + "_run-" + std::to_string(run);
        if (is_available(candidate)) {
            return candidate;
        }
    }
}

SensorReadings advance_and_read(
    EnvironmentSimulator& environment,
    SensorSimulator& sensors,
    std::size_t step_index) {
    if (step_index > 0) {
        environment.step(kSampleIntervalSeconds, ActuatorState{});
    }
    return sensors.read(environment.state());
}

void write_optional(std::ostream& output, const std::optional<double>& value) {
    if (value.has_value()) {
        output << *value;
    }
}

std::filesystem::path write_sensor_csv(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const SensorExperimentConfig& config,
    const std::vector<Sample>& samples) {
    const auto path = output_directory / (base_name + ".csv");
    std::ofstream csv(path);
    if (!csv) {
        throw std::runtime_error("impossibile creare il file CSV: " + path.string());
    }
    csv << "time_hours," << config.csv_value_column << '\n' << std::fixed
        << std::setprecision(6);
    for (const auto& sample : samples) {
        csv << sample.time_hours << ',';
        write_optional(csv, sample.value);
        csv << '\n';
    }
    return std::filesystem::absolute(path);
}

std::filesystem::path write_environment_csv(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const std::vector<EnvironmentSample>& samples) {
    const auto path = output_directory / (base_name + ".csv");
    std::ofstream csv(path);
    if (!csv) {
        throw std::runtime_error("impossibile creare il file CSV: " + path.string());
    }
    csv << "time_hours,temperature_c,air_humidity_percent,ph,"
           "light_ppfd_umol_m2_s\n"
        << std::fixed << std::setprecision(6);
    for (const auto& sample : samples) {
        csv << sample.time_hours << ',';
        write_optional(csv, sample.readings.temperature_c);
        csv << ',';
        write_optional(csv, sample.readings.air_humidity_percent);
        csv << ',';
        write_optional(csv, sample.readings.ph);
        csv << ',';
        write_optional(csv, sample.readings.light_ppfd_umol_m2_s);
        csv << '\n';
    }
    return std::filesystem::absolute(path);
}

std::string escape_gnuplot_string(const std::string& value) {
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

bool gnuplot_is_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

std::optional<std::filesystem::path> execute_gnuplot(
    const std::filesystem::path& script_path,
    const std::filesystem::path& png_path) {
    if (!gnuplot_is_available()) {
        std::filesystem::remove(script_path);
        std::cerr << "\nAvviso: gnuplot non e installato o non e presente nel PATH.\n"
                  << "Il CSV e stato creato, ma il grafico PNG non e disponibile.\n";
        return std::nullopt;
    }
    const int result = std::system(("gnuplot " + shell_quote(script_path.string())).c_str());
    std::filesystem::remove(script_path);
    if (result != 0 || !std::filesystem::exists(png_path)) {
        std::cerr << "\nAvviso: gnuplot non ha generato correttamente il grafico PNG.\n";
        return std::nullopt;
    }
    return std::filesystem::absolute(png_path);
}

std::optional<std::filesystem::path> generate_sensor_plot(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const SensorExperimentConfig& config,
    const std::filesystem::path& csv_path) {
    const auto png_path = output_directory / (base_name + ".png");
    const auto script_path = output_directory / (base_name + ".gnuplot");
    std::ofstream script(script_path);
    if (!script) {
        throw std::runtime_error("impossibile creare lo script temporaneo di gnuplot");
    }
    script << "set encoding utf8\nset terminal pngcairo size 1280,720\n"
           << "set output \"" << escape_gnuplot_string(std::filesystem::absolute(png_path).string()) << "\"\n"
           << "set datafile separator ','\nset datafile missing \"\"\n"
           << "set title \"" << escape_gnuplot_string(config.title) << "\"\n"
           << "set xlabel \"Tempo simulato (ore)\"\nset ylabel \""
           << escape_gnuplot_string(config.measurement_name) << " ("
           << escape_gnuplot_string(config.unit) << ")\"\nset grid\n"
           << "plot \"" << escape_gnuplot_string(csv_path.string())
           << "\" using 1:2 every ::1 with lines linewidth 2 title \""
           << escape_gnuplot_string(config.measurement_name) << "\"\n";
    script.close();
    return execute_gnuplot(script_path, png_path);
}

std::optional<std::filesystem::path> generate_environment_plot(
    const std::filesystem::path& output_directory,
    const std::string& base_name,
    const std::filesystem::path& csv_path) {
    const auto png_path = output_directory / (base_name + ".png");
    const auto script_path = output_directory / (base_name + ".gnuplot");
    std::ofstream script(script_path);
    if (!script) {
        throw std::runtime_error("impossibile creare lo script temporaneo di gnuplot");
    }
    script << "set encoding utf8\nset terminal pngcairo size 1400,900\n"
           << "set output \"" << escape_gnuplot_string(std::filesystem::absolute(png_path).string()) << "\"\n"
           << "set datafile separator ','\nset datafile missing \"\"\nset grid\n"
           << "set multiplot layout 2,2 title \"Ambiente naturale senza attuatori\"\n"
           << "set xlabel \"Ore\"\nset ylabel \"Temperatura (C)\"\n"
           << "plot \"" << escape_gnuplot_string(csv_path.string()) << "\" using 1:2 every ::1 with lines notitle\n"
           << "set ylabel \"Umidita relativa (%)\"\nplot '' using 1:3 every ::1 with lines notitle\n"
           << "set ylabel \"pH\"\nplot '' using 1:4 every ::1 with lines notitle\n"
           << "set ylabel \"PPFD (umol/(m2 s))\"\nplot '' using 1:5 every ::1 with lines notitle\n"
           << "unset multiplot\n";
    script.close();
    return execute_gnuplot(script_path, png_path);
}

void print_sensor_samples(
    const std::vector<Sample>& samples,
    const SimulationPlan& plan,
    const SensorExperimentConfig& config) {
    const auto stride = static_cast<std::size_t>(plan.display_interval_hours / kSampleIntervalHours);
    std::cout << "\nLetture significative (simulazione di " << plan.label << "):\n";
    for (std::size_t index = 0; index < samples.size(); ++index) {
        if (index % stride != 0 && index + 1 != samples.size()) {
            continue;
        }
        std::cout << "  t = " << std::setw(6) << std::fixed << std::setprecision(2)
                  << samples[index].time_hours << " h: ";
        if (samples[index].value.has_value()) {
            std::cout << std::setw(8) << std::setprecision(3) << *samples[index].value
                      << ' ' << config.unit;
        } else {
            std::cout << "lettura mancante";
        }
        std::cout << '\n';
    }
}

}  // namespace

int run_sensor_experiment(
    const SensorExperimentConfig& config,
    const std::function<std::optional<double>(const SensorReadings&)>& select_measurement) {
    std::cout << "=== " << config.title << " ===\n";
    try {
        InteractiveSelection options;
        if (!complete_interactive_selection(options)) {
            return 1;
        }
        const auto env_config = environment_config(options);
        EnvironmentSimulator environment(env_config, *options.seed);
        SensorSimulator sensors(*options.seed ^ 0x9E3779B9U);
        const std::size_t sample_count =
            static_cast<std::size_t>(options.plan->duration_hours / kSampleIntervalHours) + 1;
        std::vector<Sample> samples;
        samples.reserve(sample_count);
        for (std::size_t index = 0; index < sample_count; ++index) {
            const auto readings = advance_and_read(environment, sensors, index);
            samples.push_back({index * kSampleIntervalHours, select_measurement(readings)});
        }

        print_sensor_samples(samples, *options.plan, config);
        std::filesystem::create_directories(options.output_directory);
        const std::string base_name = available_base_name(
            options.output_directory,
            config.slug + "_" + run_suffix(options));
        const auto csv_path = write_sensor_csv(
            options.output_directory, base_name, config, samples);
        const auto png_path = generate_sensor_plot(
            options.output_directory, base_name, config, csv_path);
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

int run_environment_experiment() {
    std::cout << "=== Simulazione multivariata senza attuatori ===\n";
    try {
        InteractiveSelection options;
        if (!complete_interactive_selection(options)) {
            return 1;
        }
        const auto config = environment_config(options);
        EnvironmentSimulator environment(config, *options.seed);
        SensorSimulator sensors(*options.seed ^ 0xA341316CU);
        const std::size_t sample_count =
            static_cast<std::size_t>(options.plan->duration_hours / kSampleIntervalHours) + 1;
        std::vector<EnvironmentSample> samples;
        samples.reserve(sample_count);
        for (std::size_t index = 0; index < sample_count; ++index) {
            samples.push_back({
                index * kSampleIntervalHours,
                advance_and_read(environment, sensors, index),
            });
        }

        std::filesystem::create_directories(options.output_directory);
        const std::string base_name = available_base_name(
            options.output_directory,
            "environment_" + run_suffix(options));
        const auto csv_path = write_environment_csv(
            options.output_directory, base_name, samples);
        const auto png_path = generate_environment_plot(
            options.output_directory, base_name, csv_path);
        std::cout << "Campioni sincronizzati: " << samples.size() << '\n'
                  << "CSV salvato in: " << csv_path << '\n';
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
