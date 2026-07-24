#include "smarthydro/actuator_simulator.hpp"
#include "smarthydro/controllers.hpp"
#include "smarthydro/environment_simulator.hpp"
#include "smarthydro/sensor_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr double kSampleIntervalSeconds = 15.0 * 60.0;
constexpr double kSampleIntervalHours = kSampleIntervalSeconds / 3600.0;
constexpr double kExperimentDurationHours = 96.0;
constexpr double kInitialMoisturePercent = 50.0;
constexpr double kSetpointPercent = 60.0;
constexpr double kThresholdLowerPercent = 55.0;
constexpr double kThresholdUpperPercent = 65.0;
constexpr double kMaximumDoseLiters = 0.12;
constexpr std::uint32_t kEnvironmentSeed = 20260723U;
constexpr std::uint32_t kSensorSeed = 0x5EED1234U;

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

struct Sample {
    double time_hours;
    double soil_moisture_percent;
    double command_liters;
    double error_percent;
    double cumulative_water_liters;
};

struct Metrics {
    double total_water_liters = 0.0;
    std::size_t irrigation_count = 0;
};

struct ExperimentResult {
    std::string name;
    std::string datablock_name;
    std::string color;
    std::vector<Sample> samples;
    Metrics metrics;
};

using ControlStep = std::function<double(double, double)>;

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

smarthydro::SensorConfig comparison_sensor_config() {
    smarthydro::SensorConfig config;
    // Il confronto riguarda i controllori, non la gestione dei guasti:
    // niente dropout e rumore moderato ma identico per i tre anelli.
    config.temperature.dropout_probability = 0.0;
    config.air_humidity.dropout_probability = 0.0;
    config.soil_moisture.noise_standard_deviation = 0.40;
    config.soil_moisture.dropout_probability = 0.0;
    config.ph.dropout_probability = 0.0;
    config.light_ppfd.dropout_probability = 0.0;
    return config;
}

void dry_environment_to_common_initial_state(
    smarthydro::EnvironmentSimulator& environment) {
    constexpr double kMaximumWarmupHours = 7.0 * 24.0;
    double warmup_hours = 0.0;
    while (environment.state().soil_moisture_percent >
               kInitialMoisturePercent &&
           warmup_hours < kMaximumWarmupHours) {
        environment.step(
            kSampleIntervalSeconds,
            smarthydro::ActuatorOutput{});
        warmup_hours += kSampleIntervalHours;
    }
    if (environment.state().soil_moisture_percent >
        kInitialMoisturePercent) {
        throw std::runtime_error(
            "impossibile raggiungere lo stato iniziale comune");
    }
}

ExperimentResult run_closed_loop(
    std::string name,
    std::string datablock_name,
    std::string color,
    ControlStep control_step) {
    auto environment_config =
        smarthydro::make_default_tomato_environment_config();
    environment_config.soil_type =
        smarthydro::SoilType::AERATED_UNIVERSAL;

    smarthydro::EnvironmentSimulator environment(
        environment_config,
        kEnvironmentSeed);
    smarthydro::SensorSimulator sensors(
        comparison_sensor_config(),
        kSensorSeed);
    smarthydro::ActuatorSimulator actuators;

    dry_environment_to_common_initial_state(environment);

    const std::size_t interval_count = static_cast<std::size_t>(
        kExperimentDurationHours / kSampleIntervalHours);
    ExperimentResult result{
        std::move(name),
        std::move(datablock_name),
        std::move(color),
        {},
        {},
    };
    result.samples.reserve(interval_count + 1);

    double cumulative_water = 0.0;

    const double initial_moisture =
        environment.state().soil_moisture_percent;
    result.samples.push_back({
        0.0,
        initial_moisture,
        0.0,
        kSetpointPercent - initial_moisture,
        0.0,
    });

    for (std::size_t interval = 0; interval < interval_count; ++interval) {
        const auto readings = sensors.read(environment.state());
        if (!readings.soil_moisture_percent.has_value()) {
            throw std::runtime_error(
                "dropout inatteso del sensore di umidita");
        }

        const double requested_dose = std::clamp(
            control_step(
                *readings.soil_moisture_percent,
                kSampleIntervalSeconds),
            0.0,
            kMaximumDoseLiters);
        if (requested_dose > 0.0) {
            actuators.request_irrigation_volume_liters(requested_dose);
            ++result.metrics.irrigation_count;
        }

        actuators.step(kSampleIntervalSeconds);
        cumulative_water +=
            actuators.output().irrigation_volume_liters_last_step;
        environment.step(
            kSampleIntervalSeconds,
            actuators.output());

        const double moisture =
            environment.state().soil_moisture_percent;
        const double error = kSetpointPercent - moisture;

        result.samples.push_back({
            (interval + 1) * kSampleIntervalHours,
            moisture,
            requested_dose,
            error,
            cumulative_water,
        });
    }

    result.metrics.total_water_liters = cumulative_water;
    return result;
}

void validate_results(const std::vector<ExperimentResult>& results) {
    if (results.size() != 3 || results.front().samples.empty()) {
        throw std::runtime_error(
            "il confronto deve contenere esattamente tre risultati");
    }

    const std::size_t expected_samples = results.front().samples.size();
    for (const auto& result : results) {
        if (result.samples.size() != expected_samples ||
            !std::isfinite(result.metrics.total_water_liters) ||
            result.metrics.total_water_liters <= 0.0 ||
            result.metrics.irrigation_count == 0) {
            throw std::runtime_error(
                "risultato del confronto non valido per " + result.name);
        }
        for (std::size_t index = 0; index < expected_samples; ++index) {
            const auto& sample = result.samples[index];
            if (!std::isfinite(sample.time_hours) ||
                !std::isfinite(sample.soil_moisture_percent) ||
                !std::isfinite(sample.command_liters) ||
                !std::isfinite(sample.error_percent) ||
                !std::isfinite(sample.cumulative_water_liters) ||
                sample.soil_moisture_percent < 0.0 ||
                sample.soil_moisture_percent > 100.0 ||
                sample.command_liters < 0.0 ||
                sample.command_liters > kMaximumDoseLiters) {
                throw std::runtime_error(
                    "campione non valido per " + result.name);
            }
            if (index > 0 &&
                sample.time_hours !=
                    results.front().samples[index].time_hours) {
                throw std::runtime_error(
                    "assi temporali non confrontabili");
            }
        }
    }
}

class LiveComparisonPlot {
public:
    LiveComparisonPlot() {
        pipe_ = open_pipe("gnuplot");
        if (pipe_ == nullptr) {
            throw std::runtime_error("impossibile avviare gnuplot");
        }
    }

    LiveComparisonPlot(const LiveComparisonPlot&) = delete;
    LiveComparisonPlot& operator=(const LiveComparisonPlot&) = delete;

    ~LiveComparisonPlot() {
        if (pipe_ != nullptr) {
            std::fputs("unset multiplot\nexit\n", pipe_);
            std::fflush(pipe_);
            close_pipe(pipe_);
        }
    }

    void show(const std::vector<ExperimentResult>& results) {
        for (const auto& result : results) {
            send_datablock(result);
        }

        std::fprintf(
            pipe_,
            "set encoding utf8\n"
            "set grid\n"
            "set border linewidth 1\n"
            "set xrange [0:%.6f]\n"
            "set xtics 12\n"
            "set xlabel 'Tempo dal controllo (h)'\n"
            "set key top right\n"
            "set multiplot layout 2,2 rowsfirst "
            "title 'Confronto in anello chiuso - irrigazione - "
            "setpoint %.1f%% - stesso ambiente e rumore'\n",
            kExperimentDurationHours,
            kSetpointPercent);

        draw_response(results);
        draw_commands(results);
        draw_error(results);
        draw_cumulative_water(results);

        std::fputs("unset multiplot\n", pipe_);
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    void send_datablock(const ExperimentResult& result) {
        std::fprintf(pipe_, "%s << EOD\n", result.datablock_name.c_str());
        for (const auto& sample : result.samples) {
            std::fprintf(
                pipe_,
                "%.6f %.6f %.6f %.6f %.6f\n",
                sample.time_hours,
                sample.soil_moisture_percent,
                sample.command_liters,
                sample.error_percent,
                sample.cumulative_water_liters);
        }
        std::fputs("EOD\n", pipe_);
    }

    void draw_response(const std::vector<ExperimentResult>& results) {
        std::fputs(
            "set title 'Risposta del processo'\n"
            "set ylabel 'Umidita terriccio (%)'\n"
            "set yrange [45:70]\n"
            "plot 60 with lines dashtype 2 linewidth 2 "
            "linecolor rgb '#333333' title 'Setpoint', ",
            pipe_);
        draw_three_series(results, 2, "lines", false);
    }

    void draw_commands(const std::vector<ExperimentResult>& results) {
        std::fprintf(
            pipe_,
            "set title 'Azione di controllo'\n"
            "set ylabel 'Dose richiesta (L)'\n"
            "set yrange [0:%.6f]\n"
            "plot ",
            kMaximumDoseLiters * 1.10);
        draw_three_series(
            results,
            3,
            "steps",
            false);
    }

    void draw_error(
        const std::vector<ExperimentResult>& results) {
        std::fprintf(
            pipe_,
            "set title 'Errore di controllo e = r - y'\n"
            "set ylabel 'Errore (punti percentuali)'\n"
            "set yrange [-12:12]\n"
            "plot 0 with lines dashtype 2 linewidth 2 "
            "linecolor rgb '#333333' title 'Errore nullo', "
            "%.6f with lines dashtype 3 linewidth 1 "
            "linecolor rgb '#888888' title 'Banda ammessa', "
            "%.6f with lines dashtype 3 linewidth 1 "
            "linecolor rgb '#888888' notitle, ",
            kSetpointPercent - kThresholdLowerPercent,
            kSetpointPercent - kThresholdUpperPercent);
        draw_three_series(
            results,
            4,
            "lines",
            false);
    }

    void draw_cumulative_water(
        const std::vector<ExperimentResult>& results) {
        std::fputs(
            "set title 'Consumo d acqua cumulativo'\n"
            "set ylabel 'Acqua (L)'\n"
            "set yrange [0:*]\n"
            "plot ",
            pipe_);
        draw_three_series(
            results,
            5,
            "lines",
            true);
    }

    void draw_three_series(
        const std::vector<ExperimentResult>& results,
        int column,
        const char* style,
        bool include_metric) {
        for (std::size_t index = 0; index < results.size(); ++index) {
            const auto& result = results[index];
            if (index > 0) {
                std::fputs(", ", pipe_);
            }
            std::fprintf(
                pipe_,
                "%s using 1:%d with %s linewidth 2 "
                "linecolor rgb '%s' title '",
                result.datablock_name.c_str(),
                column,
                style,
                result.color.c_str());
            std::fputs(result.name.c_str(), pipe_);
            if (include_metric) {
                if (column == 5) {
                    std::fprintf(
                        pipe_,
                        " (totale %.2f L)",
                        result.metrics.total_water_liters);
                }
            }
            std::fputc('\'', pipe_);
        }
        std::fputc('\n', pipe_);
    }

    FILE* pipe_ = nullptr;
};

}  // namespace

int main() {
    if (!gnuplot_available()) {
        std::cerr
            << "Errore: gnuplot non e disponibile. Installalo per usare "
               "l'experiment comparativo.\n";
        return 1;
    }

    try {
        smarthydro::ThresholdController threshold(
            kThresholdLowerPercent,
            kThresholdUpperPercent,
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
            kMaximumDoseLiters,
            0.0);
        smarthydro::PidController pid({
            kSetpointPercent,
            0.012,
            0.0000008,
            2.0,
            {0.0, kMaximumDoseLiters},
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
        });
        smarthydro::PredictiveController predictive({
            kSetpointPercent,
            2.0,
            0.012,
            0.0,
            {0.0, kMaximumDoseLiters},
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
        });

        std::vector<ExperimentResult> results;
        results.reserve(3);
        results.push_back(run_closed_loop(
            "Threshold",
            "$threshold",
            "#0072BD",
            [&threshold](double measurement, double) {
                return threshold.update(measurement);
            }));
        results.push_back(run_closed_loop(
            "PID",
            "$pid",
            "#D95319",
            [&pid](double measurement, double delta_time_seconds) {
                return pid.update(measurement, delta_time_seconds);
            }));
        results.push_back(run_closed_loop(
            "Predictive",
            "$predictive",
            "#7E2F8E",
            [&predictive](double measurement, double) {
                return predictive.update(measurement).command;
            }));

        validate_results(results);
        LiveComparisonPlot plot;
        plot.show(results);

        std::cout
            << "=== Confronto dei controllori di irrigazione ===\n"
            << "Condizioni comuni: umidita iniziale circa "
            << kInitialMoisturePercent << "%, setpoint "
            << kSetpointPercent << "%, passo "
            << kSampleIntervalHours * 60.0 << " min, durata "
            << kExperimentDurationHours << " h.\n"
            << "Il grafico usa soltanto datablock in memoria: "
               "nessun CSV o PNG viene creato.\n"
            << "Premi Invio nel terminale per chiudere il grafico.";
        std::string line;
        std::getline(std::cin, line);
        std::cout << "\nExperiment terminato.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore durante l'experiment: "
                  << error.what() << '\n';
        return 1;
    }
}
