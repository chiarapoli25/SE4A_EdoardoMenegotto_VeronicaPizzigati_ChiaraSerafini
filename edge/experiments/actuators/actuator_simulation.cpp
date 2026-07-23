#include "smarthydro/actuator_simulator.hpp"

#include <algorithm>
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
        std::cout << "\nScegli l'attuatore da modificare:\n"
                  << "  1 - Pompa di irrigazione\n"
                  << "  2 - Dosatore di concime\n"
                  << "  3 - Illuminazione\n"
                  << "  q - Termina\n"
                  << "Scelta: ";
        if (!std::getline(std::cin, line)) {
            return 'q';
        }
        if (line.size() == 1) {
            const char choice = static_cast<char>(
                std::tolower(static_cast<unsigned char>(line.front())));
            if (choice == '1' || choice == '2' || choice == '3' || choice == 'q') {
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
        const std::vector<double>& fertilizer_flow,
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
            "Portata d'acqua erogata",
            "#0072BD",
            config.water_pump_flow_liters_per_hour,
            time_minutes,
            irrigation_flow);
        draw_series(
            "Dosatore di concime",
            "Portata concime (mL/h)",
            "Portata di concime erogata",
            "#D95319",
            config.maximum_fertilizer_flow_milliliters_per_hour,
            time_minutes,
            fertilizer_flow);
        draw_series(
            "Illuminazione",
            "Potenza elettrica (W)",
            "Potenza elettrica erogata",
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
        for (std::size_t index = 0; index < steps.size(); ++index) {
            std::fprintf(pipe_, "%.6f %.6f\n", steps[index], values[index]);
        }
        std::fputs("e\n", pipe_);
    }

    FILE* pipe_ = nullptr;
};

void print_state(
    const smarthydro::ActuatorCommand& command,
    const smarthydro::ActuatorOutput& output) {
    std::cout << "\nComandi e uscite fisiche:\n"
              << "  Pompa: dose richiesta "
              << command.requested_irrigation_volume_liters << " L, "
              << (output.water_pump_on ? "ON" : "OFF")
              << ", portata " << output.water_pump_flow_liters_per_hour
              << " L/h, ultimo volume erogato "
              << output.irrigation_volume_liters_last_step << " L\n"
              << "  Dosatore: " << command.fertilizer_doser_percent << "% -> "
              << output.fertilizer_flow_milliliters_per_hour << " mL/h\n"
              << "  Illuminazione: " << command.lighting_percent << "% -> "
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
        actuators.select_fertilizer("tomato-growth");

        std::vector<double> time_minutes{0.0};
        std::vector<double> irrigation_flow{0.0};
        std::vector<double> fertilizer_flow{0.0};
        std::vector<double> lighting_power{0.0};
        LiveGnuplot graph;
        graph.redraw(
            time_minutes,
            irrigation_flow,
            fertilizer_flow,
            lighting_power,
            actuators.config());

        std::cout << "=== Simulazione live dei tre attuatori ===\n"
                  << "Il grafico rimane aperto e viene aggiornato dopo ogni comando.\n"
                  << "Per la pompa il controllore richiede una dose in litri; "
                     "la pompa ON/OFF calcola automaticamente la durata.\n"
                  << "Dosatore e lampade usano ancora comandi percentuali.\n";
        print_state(actuators.command(), actuators.output());

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

                // La pompa passa subito a ON alla portata fissa configurata.
                time_minutes.push_back(simulated_time_minutes);
                irrigation_flow.push_back(
                    actuators.output().water_pump_flow_liters_per_hour);
                fertilizer_flow.push_back(
                    actuators.output().fertilizer_flow_milliliters_per_hour);
                lighting_power.push_back(actuators.output().lighting_power_watts);

                const double duration_seconds =
                    actuators.remaining_irrigation_time_seconds();
                actuators.step(duration_seconds);
                simulated_time_minutes += duration_seconds / 60.0;

                // Due campioni allo stesso istante rappresentano lo spegnimento
                // netto della pompa ON/OFF al termine della dose.
                time_minutes.push_back(simulated_time_minutes);
                irrigation_flow.push_back(
                    actuators.config().water_pump_flow_liters_per_hour);
                fertilizer_flow.push_back(
                    actuators.output().fertilizer_flow_milliliters_per_hour);
                lighting_power.push_back(actuators.output().lighting_power_watts);
                time_minutes.push_back(simulated_time_minutes);
                irrigation_flow.push_back(0.0);
                fertilizer_flow.push_back(
                    actuators.output().fertilizer_flow_milliliters_per_hour);
                lighting_power.push_back(actuators.output().lighting_power_watts);

                std::cout << "Durata automatica dell'irrigazione: "
                          << duration_seconds << " s.\n";
            } else if (choice == '2') {
                const double value = read_value(
                    "Comando del dosatore [0-100%]: ", 0.0, 100.0);
                actuators.set_fertilizer_doser_command_percent(value);
            } else {
                const double value = read_value(
                    "Comando delle lampade [0-100%]: ", 0.0, 100.0);
                actuators.set_lighting_command_percent(value);
            }

            if (choice != '1') {
                time_minutes.push_back(simulated_time_minutes);
                irrigation_flow.push_back(
                    actuators.output().water_pump_flow_liters_per_hour);
                fertilizer_flow.push_back(
                    actuators.output().fertilizer_flow_milliliters_per_hour);
                lighting_power.push_back(actuators.output().lighting_power_watts);
            }
            graph.redraw(
                time_minutes,
                irrigation_flow,
                fertilizer_flow,
                lighting_power,
                actuators.config());
            print_state(actuators.command(), actuators.output());
        }

        std::cout << "Simulazione terminata.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore: " << error.what() << '\n';
        return 1;
    }
}
