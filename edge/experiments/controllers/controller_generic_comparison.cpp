#include "smarthydro/controllers.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr double kSampleIntervalSeconds = 60.0;
constexpr double kSampleIntervalMinutes = 1.0;
constexpr double kExperimentDurationMinutes = 240.0;
constexpr double kSetpoint = 60.0;
constexpr double kTargetBandHalfWidth = 3.0;
constexpr double kMinimumCommand = 0.0;
constexpr double kMaximumCommand = 100.0;
constexpr double kProcessInitialValue = 30.0;
constexpr double kProcessBaseline = 20.0;
constexpr double kProcessGain = 0.80;
constexpr double kProcessTimeConstantSeconds = 12.0 * 60.0;
constexpr double kActuatorTimeConstantSeconds = 2.0 * 60.0;
constexpr std::size_t kTransportDelaySteps = 3;
constexpr double kDisturbanceStartMinutes = 120.0;
constexpr double kDisturbanceEndMinutes = 165.0;
constexpr double kDisturbanceValue = -12.0;
constexpr double kMeasurementNoiseStandardDeviation = 0.35;
constexpr std::uint32_t kNoiseSeed = 0xC0172026U;

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
    double time_minutes;
    double process_value;
    double measured_value;
    double command_percent;
    double disturbance;
    double error;
    double cumulative_control_variation;
};

struct Metrics {
    double total_control_variation = 0.0;
};

struct ExperimentResult {
    std::string name;
    std::string datablock_name;
    std::string color;
    std::vector<Sample> samples;
    Metrics metrics;
};

struct ProcessStep {
    double value;
    double disturbance;
};

using ControlStep = std::function<double(double, double)>;

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

class GenericFirstOrderProcess {
public:
    GenericFirstOrderProcess()
        : delayed_commands_(kTransportDelaySteps + 1, 0.0) {}

    double value() const noexcept {
        return value_;
    }

    ProcessStep step(double command_percent, double time_minutes) {
        command_percent = std::clamp(
            command_percent,
            kMinimumCommand,
            kMaximumCommand);

        const double actuator_response =
            1.0 - std::exp(
                      -kSampleIntervalSeconds /
                      kActuatorTimeConstantSeconds);
        actuator_output_percent_ +=
            (command_percent - actuator_output_percent_) *
            actuator_response;

        delayed_commands_.push_back(actuator_output_percent_);
        const double delayed_command = delayed_commands_.front();
        delayed_commands_.erase(delayed_commands_.begin());

        const double disturbance =
            time_minutes >= kDisturbanceStartMinutes &&
                    time_minutes < kDisturbanceEndMinutes
                ? kDisturbanceValue
                : 0.0;
        const double equilibrium =
            kProcessBaseline +
            kProcessGain * delayed_command +
            disturbance;
        const double process_response =
            1.0 - std::exp(
                      -kSampleIntervalSeconds /
                      kProcessTimeConstantSeconds);
        value_ += (equilibrium - value_) * process_response;
        value_ = std::clamp(value_, 0.0, 100.0);
        return {value_, disturbance};
    }

private:
    double value_ = kProcessInitialValue;
    double actuator_output_percent_ = 0.0;
    std::vector<double> delayed_commands_;
};

ExperimentResult run_closed_loop(
    std::string name,
    std::string datablock_name,
    std::string color,
    ControlStep control_step) {
    GenericFirstOrderProcess process;
    std::mt19937 generator(kNoiseSeed);
    std::normal_distribution<double> measurement_noise(
        0.0,
        kMeasurementNoiseStandardDeviation);

    const std::size_t interval_count = static_cast<std::size_t>(
        kExperimentDurationMinutes / kSampleIntervalMinutes);
    ExperimentResult result{
        std::move(name),
        std::move(datablock_name),
        std::move(color),
        {},
        {},
    };
    result.samples.reserve(interval_count + 1);
    result.samples.push_back({
        0.0,
        process.value(),
        process.value(),
        0.0,
        0.0,
        kSetpoint - process.value(),
        0.0,
    });

    double cumulative_control_variation = 0.0;
    double previous_command = 0.0;

    for (std::size_t interval = 0; interval < interval_count; ++interval) {
        const double measurement = std::clamp(
            process.value() + measurement_noise(generator),
            0.0,
            100.0);
        const double command = std::clamp(
            control_step(measurement, kSampleIntervalSeconds),
            kMinimumCommand,
            kMaximumCommand);
        const double time_minutes =
            interval * kSampleIntervalMinutes;
        const ProcessStep process_step =
            process.step(command, time_minutes);

        const double error = kSetpoint - process_step.value;
        cumulative_control_variation +=
            std::abs(command - previous_command);
        previous_command = command;

        result.samples.push_back({
            (interval + 1) * kSampleIntervalMinutes,
            process_step.value,
            measurement,
            command,
            process_step.disturbance,
            error,
            cumulative_control_variation,
        });
    }

    result.metrics.total_control_variation =
        cumulative_control_variation;
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
            !std::isfinite(result.metrics.total_control_variation) ||
            result.metrics.total_control_variation <= 0.0) {
            throw std::runtime_error(
                "risultato generico non valido per " + result.name);
        }
        for (std::size_t index = 0; index < expected_samples; ++index) {
            const auto& sample = result.samples[index];
            if (!std::isfinite(sample.time_minutes) ||
                !std::isfinite(sample.process_value) ||
                !std::isfinite(sample.measured_value) ||
                !std::isfinite(sample.command_percent) ||
                !std::isfinite(sample.disturbance) ||
                !std::isfinite(sample.error) ||
                !std::isfinite(
                    sample.cumulative_control_variation) ||
                sample.process_value < 0.0 ||
                sample.process_value > 100.0 ||
                sample.command_percent < kMinimumCommand ||
                sample.command_percent > kMaximumCommand) {
                throw std::runtime_error(
                    "campione generico non valido per " +
                    result.name);
            }
            if (sample.time_minutes !=
                results.front().samples[index].time_minutes) {
                throw std::runtime_error(
                    "assi temporali generici non confrontabili");
            }
        }
    }
}

enum class LegendMetric {
    NONE,
    CONTROL_VARIATION,
};

class LiveGenericComparisonPlot {
public:
    LiveGenericComparisonPlot() {
        pipe_ = open_pipe("gnuplot");
        if (pipe_ == nullptr) {
            throw std::runtime_error("impossibile avviare gnuplot");
        }
    }

    LiveGenericComparisonPlot(
        const LiveGenericComparisonPlot&) = delete;
    LiveGenericComparisonPlot& operator=(
        const LiveGenericComparisonPlot&) = delete;

    ~LiveGenericComparisonPlot() {
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
            "set xtics 30\n"
            "set xlabel 'Tempo (min)'\n"
            "set key top right\n"
            "set multiplot layout 2,2 rowsfirst "
            "title 'Confronto generico - processo del primo ordine - "
            "setpoint %.1f - disturbo tra %.0f e %.0f min'\n",
            kExperimentDurationMinutes,
            kSetpoint,
            kDisturbanceStartMinutes,
            kDisturbanceEndMinutes);

        draw_response(results);
        draw_commands(results);
        draw_error(results);
        draw_control_variation(results);

        std::fputs("unset multiplot\n", pipe_);
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    void send_datablock(const ExperimentResult& result) {
        std::fprintf(
            pipe_,
            "%s << EOD\n",
            result.datablock_name.c_str());
        for (const auto& sample : result.samples) {
            std::fprintf(
                pipe_,
                "%.6f %.6f %.6f %.6f %.6f %.6f %.6f\n",
                sample.time_minutes,
                sample.process_value,
                sample.measured_value,
                sample.command_percent,
                sample.disturbance,
                sample.error,
                sample.cumulative_control_variation);
        }
        std::fputs("EOD\n", pipe_);
    }

    void draw_response(
        const std::vector<ExperimentResult>& results) {
        std::fprintf(
            pipe_,
            "set title 'Risposta del processo'\n"
            "set ylabel 'Uscita normalizzata'\n"
            "set yrange [20:75]\n"
            "plot %.6f with lines dashtype 2 linewidth 2 "
            "linecolor rgb '#333333' title 'Setpoint', ",
            kSetpoint);
        draw_three_series(
            results,
            2,
            "lines",
            LegendMetric::NONE);
    }

    void draw_commands(
        const std::vector<ExperimentResult>& results) {
        std::fputs(
            "set title 'Comando richiesto'\n"
            "set ylabel 'Comando (%)'\n"
            "set yrange [0:105]\n"
            "plot ",
            pipe_);
        draw_three_series(
            results,
            4,
            "steps",
            LegendMetric::NONE);
    }

    void draw_error(
        const std::vector<ExperimentResult>& results) {
        std::fprintf(
            pipe_,
            "set title 'Errore di controllo e = r - y'\n"
            "set ylabel 'Errore normalizzato'\n"
            "set yrange [-20:35]\n"
            "plot 0 with lines dashtype 2 linewidth 2 "
            "linecolor rgb '#333333' title 'Errore nullo', "
            "%.6f with lines dashtype 3 linewidth 1 "
            "linecolor rgb '#888888' title 'Banda ammessa', "
            "%.6f with lines dashtype 3 linewidth 1 "
            "linecolor rgb '#888888' notitle, ",
            kTargetBandHalfWidth,
            -kTargetBandHalfWidth);
        draw_three_series(
            results,
            6,
            "lines",
            LegendMetric::NONE);
    }

    void draw_control_variation(
        const std::vector<ExperimentResult>& results) {
        std::fputs(
            "set title 'Variazione cumulativa del comando'\n"
            "set ylabel 'Somma |delta u| (%)'\n"
            "set yrange [0:*]\n"
            "plot ",
            pipe_);
        draw_three_series(
            results,
            7,
            "lines",
            LegendMetric::CONTROL_VARIATION);
    }

    void draw_three_series(
        const std::vector<ExperimentResult>& results,
        int column,
        const char* style,
        LegendMetric legend_metric) {
        for (std::size_t index = 0; index < results.size(); ++index) {
            const auto& result = results[index];
            if (index > 0) {
                std::fputs(", ", pipe_);
            }
            std::fprintf(
                pipe_,
                "%s using 1:%d with %s linewidth 2 "
                "linecolor rgb '%s' title '%s",
                result.datablock_name.c_str(),
                column,
                style,
                result.color.c_str(),
                result.name.c_str());
            switch (legend_metric) {
                case LegendMetric::NONE:
                    break;
                case LegendMetric::CONTROL_VARIATION:
                    std::fprintf(
                        pipe_,
                        " (TV %.0f%%)",
                        result.metrics.total_control_variation);
                    break;
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
               "l'experiment generico.\n";
        return 1;
    }

    try {
        smarthydro::ThresholdController threshold(
            kSetpoint - kTargetBandHalfWidth,
            kSetpoint + kTargetBandHalfWidth,
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
            kMaximumCommand,
            kMinimumCommand);
        smarthydro::PidController pid({
            kSetpoint,
            2.0,
            0.0025,
            15.0,
            {kMinimumCommand, kMaximumCommand},
            smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
        });
        smarthydro::PredictiveController predictive({
            kSetpoint,
            3.0,
            2.0,
            50.0,
            {kMinimumCommand, kMaximumCommand},
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
                return pid.update(
                    measurement,
                    delta_time_seconds);
            }));
        results.push_back(run_closed_loop(
            "Predictive",
            "$predictive",
            "#7E2F8E",
            [&predictive](double measurement, double) {
                return predictive.update(measurement).command;
            }));

        validate_results(results);
        LiveGenericComparisonPlot plot;
        plot.show(results);

        std::cout
            << "=== Confronto generico dei controllori ===\n"
            << "Processo comune: primo ordine, attuatore con inerzia, "
               "ritardo di "
            << kTransportDelaySteps << " min, rumore comune e disturbo.\n"
            << "Il grafico usa soltanto datablock in memoria: "
               "nessun CSV o PNG viene creato.\n"
            << "Premi Invio nel terminale per chiudere il grafico.";
        std::string line;
        std::getline(std::cin, line);
        std::cout << "\nExperiment terminato.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore durante l'experiment generico: "
                  << error.what() << '\n';
        return 1;
    }
}
