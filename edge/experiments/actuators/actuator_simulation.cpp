#include "smarthydro/actuator_simulator.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr std::array<smarthydro::FertilizerType, 5> kFertilizerTypes{
    smarthydro::FertilizerType::NITROGEN,
    smarthydro::FertilizerType::PHOSPHORUS,
    smarthydro::FertilizerType::POTASSIUM,
    smarthydro::FertilizerType::PH_UP,
    smarthydro::FertilizerType::PH_DOWN,
};

using FertilizerHistory =
    smarthydro::FertilizerValues<std::vector<double>>;

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

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

double read_value(
    const std::string& prompt,
    double minimum,
    double maximum) {
    std::string line;
    while (true) {
        std::cout << prompt;
        if (!std::getline(std::cin, line)) {
            throw std::runtime_error("input terminato");
        }

        std::istringstream input(line);
        double value = 0.0;
        std::string extra;
        if ((input >> value) && !(input >> extra) && std::isfinite(value) &&
            value >= minimum && value <= maximum) {
            return value;
        }
        std::cout << "Input non valido: inserisci un numero tra "
                  << minimum << " e " << maximum << ".\n";
    }
}

char read_actuator_choice() {
    std::string line;
    while (true) {
        std::cout << "\nScegli l'azione:\n"
                  << "  1 - Irrigazione con miscela\n"
                  << "  2 - Illuminazione\n"
                  << "  3 - Arresto di sicurezza\n"
                  << "  q - Termina\n"
                  << "Scelta: ";
        if (!std::getline(std::cin, line)) {
            return 'q';
        }
        if (line.size() == 1) {
            const char choice = static_cast<char>(
                std::tolower(static_cast<unsigned char>(line.front())));
            if (choice == '1' || choice == '2' || choice == '3' ||
                choice == 'q') {
                return choice;
            }
        }
        std::cout << "Scelta non valida.\n";
    }
}

class LiveGnuplot {
public:
    LiveGnuplot() {
        pipe_ = open_pipe("gnuplot");
        if (pipe_ == nullptr) {
            throw std::runtime_error("impossibile avviare gnuplot");
        }
    }

    LiveGnuplot(const LiveGnuplot&) = delete;
    LiveGnuplot& operator=(const LiveGnuplot&) = delete;

    ~LiveGnuplot() {
        if (pipe_ != nullptr) {
            std::fputs("unset multiplot\nexit\n", pipe_);
            std::fflush(pipe_);
            close_pipe(pipe_);
        }
    }

    void redraw(
        const std::vector<double>& time_minutes,
        const std::vector<double>& irrigation_flow,
        const FertilizerHistory& fertilizer_flows,
        const std::vector<double>& lighting_power,
        const smarthydro::ActuatorConfig& config) {
        const double x_max = std::max(5.0, time_minutes.back() + 1.0);
        std::fprintf(
            pipe_,
            "clear\n"
            "set multiplot layout 3,1 rowsfirst "
            "title 'Uscite fisiche live degli attuatori'\n"
            "set grid\n"
            "set xrange [0:%.6f]\n"
            "set xlabel 'Tempo simulato (min)'\n"
            "set key top left\n",
            x_max);

        draw_series(
            "Pompa di irrigazione",
            "Portata acqua (L/h)",
            "Acqua",
            "#0072BD",
            config.water_pump_flow_liters_per_hour,
            time_minutes,
            irrigation_flow);
        draw_fertilizer_series(time_minutes, fertilizer_flows, config);
        draw_series(
            "Illuminazione",
            "Potenza elettrica (W)",
            "Lampade",
            "#EDB120",
            config.maximum_lighting_power_watts,
            time_minutes,
            lighting_power);

        std::fputs("unset multiplot\n", pipe_);
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    void draw_series(
        const char* title,
        const char* y_label,
        const char* legend,
        const char* color,
        double maximum_value,
        const std::vector<double>& steps,
        const std::vector<double>& values) {
        std::fprintf(
            pipe_,
            "set title '%s'\n"
            "set ylabel '%s'\n"
            "set yrange [0:%.6f]\n"
            "plot '-' using 1:2 with linespoints linewidth 2 "
            "linecolor rgb '%s' title '%s'\n",
            title,
            y_label,
            maximum_value * 1.10,
            color,
            legend);
        write_series(steps, values);
    }

    void draw_fertilizer_series(
        const std::vector<double>& steps,
        const FertilizerHistory& histories,
        const smarthydro::ActuatorConfig& config) {
        const double maximum_flow = *std::max_element(
            config.fertilizer_flow_milliliters_per_hour.begin(),
            config.fertilizer_flow_milliliters_per_hour.end());
        std::fprintf(
            pipe_,
            "set title 'Elettrovalvole dei concentrati'\n"
            "set ylabel 'Portata (mL/h)'\n"
            "set yrange [0:%.6f]\n"
            "plot '-' using 1:2 with lines linewidth 2 title 'N', "
            "'-' using 1:2 with lines linewidth 2 title 'P', "
            "'-' using 1:2 with lines linewidth 2 title 'K', "
            "'-' using 1:2 with lines linewidth 2 title 'pH+', "
            "'-' using 1:2 with lines linewidth 2 title 'pH-'\n",
            maximum_flow * 1.10);
        for (std::size_t index = 0; index < kFertilizerTypes.size(); ++index) {
            write_series(steps, histories[index]);
        }
    }

    void write_series(
        const std::vector<double>& steps,
        const std::vector<double>& values) {
        for (std::size_t index = 0; index < steps.size(); ++index) {
            std::fprintf(pipe_, "%.6f %.6f\n", steps[index], values[index]);
        }
        std::fputs("e\n", pipe_);
    }

    FILE* pipe_ = nullptr;
};

void append_sample(
    double time_minutes,
    double irrigation_flow,
    const smarthydro::FertilizerValues<double>& fertilizer_flows,
    double lighting_power,
    std::vector<double>& times,
    std::vector<double>& irrigation_history,
    FertilizerHistory& fertilizer_history,
    std::vector<double>& lighting_history) {
    times.push_back(time_minutes);
    irrigation_history.push_back(irrigation_flow);
    for (std::size_t index = 0; index < kFertilizerTypes.size(); ++index) {
        fertilizer_history[index].push_back(fertilizer_flows[index]);
    }
    lighting_history.push_back(lighting_power);
}

void print_state(const smarthydro::ActuatorSimulator& actuators) {
    const auto& command = actuators.command();
    const auto& output = actuators.output();
    std::cout << "\nComandi e uscite fisiche:\n"
              << "  Pompa: dose richiesta "
              << command.requested_irrigation_volume_liters << " L, "
              << (output.water_pump_on ? "ON" : "OFF")
              << ", portata " << output.water_pump_flow_liters_per_hour
              << " L/h, ultimo volume " << output.irrigation_volume_liters_last_step
              << " L\n";
    for (const auto type : kFertilizerTypes) {
        std::cout << "  Valvola " << smarthydro::to_string(type) << ": "
                  << (actuators.fertilizer_valve_open(type) ? "OPEN" : "CLOSED")
                  << ", portata "
                  << actuators.fertilizer_flow_milliliters_per_hour(type)
                  << " mL/h, ultimo volume "
                  << actuators.fertilizer_volume_milliliters_last_step(type)
                  << " mL\n";
    }
    std::cout << "  Illuminazione: " << command.lighting_percent << "% -> "
              << output.lighting_power_watts << " W\n";
}

}  // namespace

int main() {
    if (!gnuplot_available()) {
        std::cerr << "Errore: gnuplot non e disponibile. Installalo per usare "
                     "l'experiment interattivo.\n";
        return 1;
    }

    try {
        smarthydro::ActuatorSimulator actuators;
        std::vector<double> time_minutes{0.0};
        std::vector<double> irrigation_flow{0.0};
        FertilizerHistory fertilizer_flows;
        for (auto& history : fertilizer_flows) {
            history.push_back(0.0);
        }
        std::vector<double> lighting_power{0.0};
        LiveGnuplot graph;
        graph.redraw(
            time_minutes,
            irrigation_flow,
            fertilizer_flows,
            lighting_power,
            actuators.config());

        std::cout << "=== Simulazione live di pompa, valvole e lampade ===\n"
                  << "I concentrati possono fluire soltanto durante "
                     "l'irrigazione; pH+ e pH- sono interbloccati.\n";
        print_state(actuators);

        double simulated_time_minutes = 0.0;
        while (true) {
            const char choice = read_actuator_choice();
            if (choice == 'q') {
                break;
            }

            if (choice == '1') {
                const double volume_liters = read_value(
                    "Volume d'acqua richiesto [L]: ",
                    0.001,
                    actuators.config().maximum_irrigation_volume_liters);
                actuators.request_irrigation_volume_liters(volume_liters);

                for (const auto type : {
                         smarthydro::FertilizerType::NITROGEN,
                         smarthydro::FertilizerType::PHOSPHORUS,
                         smarthydro::FertilizerType::POTASSIUM}) {
                    const bool open = read_value(
                        std::string("Aprire ") + smarthydro::to_string(type) +
                            "? [0=no, 1=si]: ",
                        0.0,
                        1.0) == 1.0;
                    actuators.set_fertilizer_valve_open(type, open);
                }
                const int ph_choice = static_cast<int>(read_value(
                    "Correzione pH [0=nessuna, 1=pH+, 2=pH-]: ",
                    0.0,
                    2.0));
                if (ph_choice == 1) {
                    actuators.set_fertilizer_valve_open(
                        smarthydro::FertilizerType::PH_UP, true);
                } else if (ph_choice == 2) {
                    actuators.set_fertilizer_valve_open(
                        smarthydro::FertilizerType::PH_DOWN, true);
                }

                const auto active_fertilizer_flows =
                    actuators.output().fertilizer_flow_milliliters_per_hour;
                append_sample(
                    simulated_time_minutes,
                    actuators.output().water_pump_flow_liters_per_hour,
                    active_fertilizer_flows,
                    actuators.output().lighting_power_watts,
                    time_minutes,
                    irrigation_flow,
                    fertilizer_flows,
                    lighting_power);

                const double duration_seconds =
                    actuators.remaining_irrigation_time_seconds();
                actuators.step(duration_seconds);
                simulated_time_minutes += duration_seconds / 60.0;

                append_sample(
                    simulated_time_minutes,
                    actuators.config().water_pump_flow_liters_per_hour,
                    active_fertilizer_flows,
                    actuators.output().lighting_power_watts,
                    time_minutes,
                    irrigation_flow,
                    fertilizer_flows,
                    lighting_power);
                append_sample(
                    simulated_time_minutes,
                    0.0,
                    actuators.output().fertilizer_flow_milliliters_per_hour,
                    actuators.output().lighting_power_watts,
                    time_minutes,
                    irrigation_flow,
                    fertilizer_flows,
                    lighting_power);
                std::cout << "Durata automatica dell'irrigazione: "
                          << duration_seconds << " s.\n";
            } else if (choice == '2') {
                const double value = read_value(
                    "Comando delle lampade [0-100%]: ", 0.0, 100.0);
                actuators.set_lighting_command_percent(value);
                append_sample(
                    simulated_time_minutes,
                    actuators.output().water_pump_flow_liters_per_hour,
                    actuators.output().fertilizer_flow_milliliters_per_hour,
                    actuators.output().lighting_power_watts,
                    time_minutes,
                    irrigation_flow,
                    fertilizer_flows,
                    lighting_power);
            } else {
                actuators.stop_all();
                append_sample(
                    simulated_time_minutes,
                    0.0,
                    actuators.output().fertilizer_flow_milliliters_per_hour,
                    0.0,
                    time_minutes,
                    irrigation_flow,
                    fertilizer_flows,
                    lighting_power);
            }

            graph.redraw(
                time_minutes,
                irrigation_flow,
                fertilizer_flows,
                lighting_power,
                actuators.config());
            print_state(actuators);
        }

        std::cout << "Simulazione terminata.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore: " << error.what() << '\n';
        return 1;
    }
}
