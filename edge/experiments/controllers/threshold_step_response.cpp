#include "experiment_output.hpp"
#include "smarthydro/controllers.hpp"

#include <iostream>
#include <vector>

int main() {
    smarthydro::ThresholdController controller(40.0, 60.0);
    std::vector<double> steps;
    std::vector<double> measurements;
    std::vector<double> commands;

    for (int step = 0; step < 24; ++step) {
        double measurement = 50.0;
        if ((step >= 4 && step < 8) || step >= 20) {
            measurement = 35.0;
        } else if (step >= 12 && step < 16) {
            measurement = 65.0;
        }
        steps.push_back(step);
        measurements.push_back(measurement);
        commands.push_back(controller.update(measurement));
    }

    const auto artifacts = smarthydro::experiments::write_experiment_output(
        {
            "threshold_step_response",
            "Controllore a soglia: gradini e isteresi",
            "step",
            "Passo discreto",
            "Valore normalizzato (%)",
        },
        steps,
        {
            {"measurement_percent", "Ingresso", measurements},
            {"command_percent", "Comando attuatore", commands},
        });

    std::cout << "Soglie: 40% / 60%. La zona intermedia conserva lo stato.\n"
              << "CSV salvato in: " << artifacts.csv_path << '\n';
    if (artifacts.png_path.has_value()) {
        std::cout << "Grafico salvato in: " << *artifacts.png_path << '\n';
    }
    return 0;
}
