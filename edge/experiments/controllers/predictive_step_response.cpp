#include "experiment_output.hpp"
#include "smarthydro/controllers.hpp"

#include <iostream>
#include <vector>

int main() {
    smarthydro::PredictiveController controller({
        50.0,
        2.0,
        3.0,
        20.0,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    std::vector<double> steps;
    std::vector<double> measurements;
    std::vector<double> predictions;
    std::vector<double> commands;

    for (int step = 0; step < 25; ++step) {
        double measurement = 50.0;
        if (step >= 5 && step < 10) {
            measurement = 40.0;
        } else if (step >= 10 && step < 15) {
            measurement = 42.0 + 2.0 * (step - 10);
        } else if (step >= 15 && step < 20) {
            measurement = 60.0;
        }
        const auto result = controller.update(measurement);
        steps.push_back(step);
        measurements.push_back(measurement);
        predictions.push_back(result.predicted_value);
        commands.push_back(result.command);
    }

    const auto artifacts = smarthydro::experiments::write_experiment_output(
        {
            "predictive_step_response",
            "Controllore predittivo: gradini e trend",
            "step",
            "Passo discreto",
            "Valore normalizzato (%)",
        },
        steps,
        {
            {"measurement_percent", "Misura", measurements},
            {"predicted_percent", "Previsione", predictions},
            {"command_percent", "Comando attuatore", commands},
        });

    std::cout << "Predittivo: setpoint=50, orizzonte=2 passi, guadagno=3.\n"
              << "CSV salvato in: " << artifacts.csv_path << '\n';
    if (artifacts.png_path.has_value()) {
        std::cout << "Grafico salvato in: " << *artifacts.png_path << '\n';
    }
    return 0;
}
