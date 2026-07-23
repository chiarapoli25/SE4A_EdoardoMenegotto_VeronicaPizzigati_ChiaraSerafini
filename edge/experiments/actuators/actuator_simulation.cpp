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

double read_percentage() {
    std::string line;
    while (true) {
        std::cout << "Nuova potenza da erogare [0-100%]: ";
        if (!std::getline(std::cin, line)) {
            throw std::runtime_error("input terminato");
        }

        std::istringstream input(line);
        double value = 0.0;
        std::string extra;
        if ((input >> value) && !(input >> extra) && std::isfinite(value) &&
            value >= 0.0 && value <= 100.0) {
            return value;
        }
        std::cout << "Input non valido: inserisci un numero tra 0 e 100.\n";
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
        const std::vector<double>& steps,
        const std::vector<double>& pump,
        const std::vector<double>& fertilizer,
        const std::vector<double>& lighting) {
        const double x_max = std::max(5.0, steps.back() + 1.0);
        std::fprintf(
            pipe_,
            "clear\n"
            "set multiplot layout 3,1 rowsfirst "
            "title 'Erogazione live degli attuatori ideali'\n"
            "set grid\n"
            "set xrange [0:%.6f]\n"
            "set yrange [0:100]\n"
            "set xlabel 'Numero del comando'\n"
            "set ylabel 'Potenza (%%)'\n",
            x_max);

        draw_series("Pompa di irrigazione", steps, pump);
        draw_series("Dosatore di concime", steps, fertilizer);
        draw_series("Illuminazione", steps, lighting);

        std::fputs("unset multiplot\n", pipe_);
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    void draw_series(
        const char* title,
        const std::vector<double>& steps,
        const std::vector<double>& values) {
        std::fprintf(
            pipe_,
            "set title '%s'\n"
            "plot '-' using 1:2 with linespoints linewidth 2 "
            "title 'Erogazione effettiva'\n",
            title);
        for (std::size_t index = 0; index < steps.size(); ++index) {
            std::fprintf(pipe_, "%.6f %.6f\n", steps[index], values[index]);
        }
        std::fputs("e\n", pipe_);
    }

    FILE* pipe_ = nullptr;
};

void print_state(const smarthydro::ActuatorState& state) {
    std::cout << "\nValori attualmente erogati:\n"
              << "  Pompa: " << state.water_pump_percent << "%\n"
              << "  Dosatore: " << state.fertilizer_dosing_percent << "%\n"
              << "  Illuminazione: " << state.lighting_percent << "%\n";
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

        std::vector<double> steps{0.0};
        std::vector<double> pump{0.0};
        std::vector<double> fertilizer{0.0};
        std::vector<double> lighting{0.0};
        LiveGnuplot graph;
        graph.redraw(steps, pump, fertilizer, lighting);

        std::cout << "=== Simulazione live dei tre attuatori ===\n"
                  << "Il grafico rimane aperto e viene aggiornato dopo ogni comando.\n"
                  << "Gli attuatori sono ideali: la percentuale richiesta viene "
                     "erogata immediatamente.\n";
        print_state(actuators.state());

        std::size_t command_number = 0;
        while (true) {
            const char choice = read_actuator_choice();
            if (choice == 'q') {
                break;
            }

            const double value = read_percentage();
            if (choice == '1') {
                actuators.set_water_pump_percent(value);
            } else if (choice == '2') {
                actuators.set_fertilizer_dosing_percent(value);
            } else {
                actuators.set_lighting_percent(value);
            }

            ++command_number;
            steps.push_back(static_cast<double>(command_number));
            pump.push_back(actuators.state().water_pump_percent);
            fertilizer.push_back(actuators.state().fertilizer_dosing_percent);
            lighting.push_back(actuators.state().lighting_percent);
            graph.redraw(steps, pump, fertilizer, lighting);
            print_state(actuators.state());
        }

        std::cout << "Simulazione terminata.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Errore: " << error.what() << '\n';
        return 1;
    }
}