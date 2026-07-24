#include "smarthydro/actuator_simulator.hpp"
#include "smarthydro/environment_simulator.hpp"
#include "smarthydro/sensor_simulator.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr double kSampleIntervalSeconds = 900.0;
constexpr double kSampleIntervalHours = kSampleIntervalSeconds / 3600.0;

#ifdef _WIN32
FILE* open_pipe(const char* command) {
    return _popen(command, "w");
}

int close_pipe(FILE* pipe) {
    return _pclose(pipe);
}
#else
FILE* open_pipe(const char* command) {
    return popen(command, "w");
}

int close_pipe(FILE* pipe) {
    return pclose(pipe);
}
#endif

struct SimulationPlan {
    std::string label;
    std::string file_suffix;
    double duration_hours;
    double terminal_interval_hours;
    double x_tick_hours;
};

struct Sample {
    double time_hours;
    smarthydro::SensorReadings readings;
};

using ReadingMember =
    std::optional<double> smarthydro::SensorReadings::*;

struct PlotRange {
    double minimum;
    double maximum;
};

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

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
    std::transform(
        value.begin(),
        value.end(),
        value.begin(),
        [](unsigned char character) {
            return static_cast<char>(std::tolower(character));
        });
    return value;
}

std::string read_line(const std::string& prompt) {
    std::cout << prompt;
    std::string line;
    if (!std::getline(std::cin, line)) {
        throw std::runtime_error("input terminato");
    }
    return normalized_input(line);
}

SimulationPlan request_plan() {
    while (true) {
        const std::string choice = read_line(
            "Scegli la durata della simulazione:\n"
            "  1 - Una giornata\n"
            "  2 - Una settimana\n"
            "Scelta: ");
        if (choice == "1" || choice == "giorno" || choice == "giornata") {
            return {"una giornata", "day", 24.0, 6.0, 3.0};
        }
        if (choice == "2" || choice == "settimana") {
            return {"una settimana", "week", 24.0 * 7.0, 24.0, 24.0};
        }
        std::cout << "Scelta non valida.\n\n";
    }
}

smarthydro::SoilType request_soil_type() {
    while (true) {
        const std::string choice = read_line(
            "Scegli il tipo di terriccio:\n"
            "  1 - Substrato universale aerato\n"
            "  2 - Substrato drenante\n"
            "  3 - Substrato organico ritentivo\n"
            "Scelta: ");
        if (choice == "1" || choice == "universale" ||
            choice == "aerated-universal") {
            return smarthydro::SoilType::AERATED_UNIVERSAL;
        }
        if (choice == "2" || choice == "drenante" || choice == "draining") {
            return smarthydro::SoilType::DRAINING;
        }
        if (choice == "3" || choice == "organico" || choice == "ritentivo" ||
            choice == "organic-retentive") {
            return smarthydro::SoilType::ORGANIC_RETENTIVE;
        }
        std::cout << "Scelta non valida.\n\n";
    }
}

std::uint32_t request_seed() {
    while (true) {
        const std::string value = read_line(
            "Seed riproducibile (invio per generarne uno casuale): ");
        if (value.empty()) {
            const std::uint32_t seed = std::random_device{}();
            std::cout << "Seed generato: " << seed << '\n';
            return seed;
        }
        try {
            std::size_t consumed = 0;
            const auto parsed = std::stoull(value, &consumed);
            if (consumed == value.size() &&
                parsed <= std::numeric_limits<std::uint32_t>::max()) {
                const auto seed = static_cast<std::uint32_t>(parsed);
                std::cout << "Seed usato: " << seed << '\n';
                return seed;
            }
        } catch (const std::exception&) {
        }
        std::cout << "Seed non valido: inserisci un intero tra 0 e 4294967295.\n";
    }
}

PlotRange measured_range(
    const std::vector<Sample>& samples,
    ReadingMember member,
    double minimum_padding) {
    double minimum = std::numeric_limits<double>::infinity();
    double maximum = -std::numeric_limits<double>::infinity();
    for (const auto& sample : samples) {
        const auto& value = sample.readings.*member;
        if (value.has_value()) {
            minimum = std::min(minimum, *value);
            maximum = std::max(maximum, *value);
        }
    }
    if (!std::isfinite(minimum) || !std::isfinite(maximum)) {
        return {0.0, 1.0};
    }
    const double padding =
        std::max(minimum_padding, (maximum - minimum) * 0.10);
    return {minimum - padding, maximum + padding};
}

double measured_maximum(
    const std::vector<Sample>& samples,
    ReadingMember member) {
    double maximum = 0.0;
    for (const auto& sample : samples) {
        const auto& value = sample.readings.*member;
        if (value.has_value()) {
            maximum = std::max(maximum, *value);
        }
    }
    return maximum;
}

void print_value(
    const std::optional<double>& value,
    const char* unit) {
    if (value.has_value()) {
        std::cout << std::fixed << std::setprecision(2) << *value << ' ' << unit;
    } else {
        std::cout << "lettura mancante";
    }
}

void print_significant_samples(
    const std::vector<Sample>& samples,
    const SimulationPlan& plan) {
    const auto stride = static_cast<std::size_t>(
        plan.terminal_interval_hours / kSampleIntervalHours);
    std::cout << "\nLetture significative:\n";
    for (std::size_t index = 0; index < samples.size(); ++index) {
        if (index % stride != 0 && index + 1 != samples.size()) {
            continue;
        }
        const auto& sample = samples[index];
        std::cout << "  t=" << std::setw(6) << std::fixed
                  << std::setprecision(2) << sample.time_hours << " h | T=";
        print_value(sample.readings.temperature_c, "C");
        std::cout << " | RH=";
        print_value(sample.readings.air_humidity_percent, "%");
        std::cout << " | Terriccio=";
        print_value(sample.readings.soil_moisture_percent, "%");
        std::cout << " | pH=";
        print_value(sample.readings.ph, "");
        std::cout << " | PPFD=";
        print_value(sample.readings.light_ppfd_umol_m2_s, "umol/(m2 s)");
        std::cout << '\n';
    }
}

void write_optional(
    std::ostream& output,
    const std::optional<double>& value) {
    if (value.has_value()) {
        output << *value;
    }
}

std::filesystem::path write_samples_csv(
    const std::vector<Sample>& samples,
    const SimulationPlan& plan,
    smarthydro::SoilType soil_type,
    std::uint32_t seed) {
    const std::filesystem::path output_directory = "experiment_results";
    std::filesystem::create_directories(output_directory);
    const std::string desired_name =
        "sensors_" + plan.file_suffix + "_natural_soil-" +
        smarthydro::to_string(soil_type) + "_seed-" + std::to_string(seed);

    std::string base_name = desired_name;
    for (std::size_t run = 2;
         std::filesystem::exists(output_directory / (base_name + ".csv"));
         ++run) {
        base_name = desired_name + "_run-" + std::to_string(run);
    }

    const auto csv_path = output_directory / (base_name + ".csv");
    std::ofstream csv(csv_path);
    if (!csv) {
        throw std::runtime_error(
            "impossibile creare il file CSV: " + csv_path.string());
    }
    csv << "time_hours,temperature_c,air_humidity_percent,"
           "soil_moisture_percent,ph,light_ppfd_umol_m2_s\n"
        << std::fixed << std::setprecision(6);
    for (const auto& sample : samples) {
        csv << sample.time_hours << ',';
        write_optional(csv, sample.readings.temperature_c);
        csv << ',';
        write_optional(csv, sample.readings.air_humidity_percent);
        csv << ',';
        write_optional(csv, sample.readings.soil_moisture_percent);
        csv << ',';
        write_optional(csv, sample.readings.ph);
        csv << ',';
        write_optional(csv, sample.readings.light_ppfd_umol_m2_s);
        csv << '\n';
    }
    return std::filesystem::absolute(csv_path);
}

class LiveSensorPlot {
public:
    LiveSensorPlot() {
        pipe_ = open_pipe("gnuplot");
        if (pipe_ == nullptr) {
            throw std::runtime_error("impossibile avviare gnuplot");
        }
    }

    LiveSensorPlot(const LiveSensorPlot&) = delete;
    LiveSensorPlot& operator=(const LiveSensorPlot&) = delete;

    ~LiveSensorPlot() {
        if (pipe_ != nullptr) {
            std::fputs("unset multiplot\nexit\n", pipe_);
            std::fflush(pipe_);
            close_pipe(pipe_);
        }
    }

    void show(
        const std::vector<Sample>& samples,
        const SimulationPlan& plan,
        smarthydro::SoilType soil_type,
        std::uint32_t seed) {
        const PlotRange temperature_range = measured_range(
            samples, &smarthydro::SensorReadings::temperature_c, 1.0);
        const PlotRange ph_range = measured_range(
            samples, &smarthydro::SensorReadings::ph, 0.10);
        const double ppfd_maximum = std::max(
            100.0,
            measured_maximum(
                samples,
                &smarthydro::SensorReadings::light_ppfd_umol_m2_s) *
                1.10);

        // I datablock restano nella memoria di gnuplot: non vengono creati
        // file temporanei, CSV o immagini.
        send_datablock(
            "$temperature",
            samples,
            &smarthydro::SensorReadings::temperature_c);
        send_datablock(
            "$air_humidity",
            samples,
            &smarthydro::SensorReadings::air_humidity_percent);
        send_datablock(
            "$soil_moisture",
            samples,
            &smarthydro::SensorReadings::soil_moisture_percent);
        send_datablock("$ph", samples, &smarthydro::SensorReadings::ph);
        send_datablock(
            "$light",
            samples,
            &smarthydro::SensorReadings::light_ppfd_umol_m2_s);

        std::fprintf(
            pipe_,
            "set encoding utf8\n"
            "set datafile missing '?'\n"
            "set grid\n"
            "set border linewidth 1\n"
            "set xrange [0:%.6f]\n"
            "set xtics %.6f\n"
            "set xlabel 'Tempo simulato (h)'\n"
            "set multiplot layout 2,2 rowsfirst "
            "title 'Sensori - ambiente naturale - terriccio %s - seed %u'\n",
            plan.duration_hours,
            plan.x_tick_hours,
            smarthydro::to_string(soil_type),
            seed);

        draw_single_series(
            "Temperatura dell'aria",
            "Temperatura (gradi C)",
            "Temperatura",
            "#D95319",
            temperature_range,
            "$temperature");

        std::fputs(
            "set title \"Umidita dell'aria e del terriccio\"\n"
            "set ylabel \"Umidita (%)\"\n"
            "set yrange [0:100]\n"
            "set key top right\n"
            "plot $air_humidity using 1:2 with lines linewidth 2 "
            "linecolor rgb \"#0072BD\" title \"Aria\", "
            "$soil_moisture using 1:2 with lines linewidth 2 "
            "linecolor rgb \"#2CA02C\" title \"Terriccio\"\n",
            pipe_);

        draw_single_series(
            "pH del terriccio",
            "pH",
            "pH",
            "#7E2F8E",
            ph_range,
            "$ph");

        draw_single_series(
            "Luce fotosinteticamente attiva",
            "PPFD (umol/(m2 s))",
            "PPFD",
            "#EDB120",
            {0.0, ppfd_maximum},
            "$light");

        std::fputs("unset multiplot\n", pipe_);
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    void draw_single_series(
        const char* title,
        const char* y_label,
        const char* legend,
        const char* color,
        PlotRange range,
        const char* datablock) {
        std::fprintf(
            pipe_,
            "set title \"%s\"\n"
            "set ylabel \"%s\"\n"
            "set yrange [%.6f:%.6f]\n"
            "set key top right\n"
            "plot %s using 1:2 with lines linewidth 2 "
            "linecolor rgb \"%s\" title \"%s\"\n",
            title,
            y_label,
            range.minimum,
            range.maximum,
            datablock,
            color,
            legend);
    }

    void send_datablock(
        const char* name,
        const std::vector<Sample>& samples,
        ReadingMember member) {
        std::fprintf(pipe_, "%s << EOD\n", name);
        for (const auto& sample : samples) {
            std::fprintf(pipe_, "%.6f ", sample.time_hours);
            const auto& value = sample.readings.*member;
            if (value.has_value()) {
                std::fprintf(pipe_, "%.6f\n", *value);
            } else {
                std::fputs("?\n", pipe_);
            }
        }
        std::fputs("EOD\n", pipe_);
    }

    FILE* pipe_ = nullptr;
};

}  // namespace

int main() {
    if (!gnuplot_available()) {
        std::cerr << "Errore: gnuplot non e disponibile. Installalo per usare "
                     "l'experiment interattivo dei sensori.\n";
        return 1;
    }

    try {
        std::cout << "=== Simulazione live di tutti i sensori ===\n"
                  << "Gli attuatori rimangono spenti per tutta la simulazione.\n\n";
        const SimulationPlan plan = request_plan();
        const smarthydro::SoilType soil_type = request_soil_type();
        const std::uint32_t seed = request_seed();

        smarthydro::EnvironmentConfig config;
        config.soil_type = soil_type;
        smarthydro::EnvironmentSimulator environment(config, seed);
        smarthydro::SensorSimulator sensors(seed ^ 0x9E3779B9U);

        const std::size_t sample_count =
            static_cast<std::size_t>(
                plan.duration_hours / kSampleIntervalHours) +
            1;
        std::vector<Sample> samples;
        samples.reserve(sample_count);
        for (std::size_t index = 0; index < sample_count; ++index) {
            if (index > 0) {
                environment.step(
                    kSampleIntervalSeconds,
                    smarthydro::ActuatorOutput{});
            }
            samples.push_back({
                index * kSampleIntervalHours,
                sensors.read(environment.state()),
            });
        }

        print_significant_samples(samples, plan);
        const auto csv_path =
            write_samples_csv(samples, plan, soil_type, seed);
        LiveSensorPlot graph;
        graph.show(samples, plan, soil_type, seed);

        std::cout << "\nCampioni simulati: " << samples.size() << '\n'
                  << "Attuatori: spenti\n"
                  << "CSV salvato in: " << csv_path << '\n'
                  << "Nessun PNG e stato creato.\n"
                  << "Grafico aperto: premi Invio nel terminale per terminare.";
        std::string line;
        std::getline(std::cin, line);
        std::cout << "\nSimulazione terminata.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore durante l'esperimento: " << error.what() << '\n';
        return 1;
    }
}
