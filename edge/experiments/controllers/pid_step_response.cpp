#include "experiment_output.hpp"
#include "smarthydro/controllers.hpp"

#include <iostream>
#include <vector>

int main() {
    smarthydro::PidController controller({
        50.0,
        3.0,
        0.08,
        0.5,
        {0.0, 100.0},
        smarthydro::ControlDirection::INCREASES_PROCESS_VALUE,
    });
    std::vector<double> seconds;
    std::vector<double> setpoints;
    std::vector<double> measurements;
    std::vector<double> commands;

    for (int second = 0; second < 35; ++second) {
        double measurement = 50.0;
        if (second >= 5 && second < 15) {
            measurement = 40.0;
        } else if (second >= 25) {
            measurement = 60.0;
        }
        seconds.push_back(second);
        setpoints.push_back(50.0);
        measurements.push_back(measurement);
        commands.push_back(controller.update(measurement, 1.0));
    }

    const auto artifacts = smarthydro::experiments::write_experiment_output(
        {
            "pid_step_response",
            "Controllore PID: risposta a gradini della misura",
            "time_seconds",
            "Tempo (s)",
            "Valore normalizzato (%)",
        },
        seconds,
        {
            {"setpoint_percent", "Setpoint", setpoints},
            {"measurement_percent", "Misura", measurements},
            {"command_percent", "Comando PID", commands},
        });

    std::cout << "PID: setpoint=50, Kp=3, Ki=0.08, Kd=0.5.\n"
              << "CSV salvato in: " << artifacts.csv_path << '\n';
    if (artifacts.png_path.has_value()) {
        std::cout << "Grafico salvato in: " << *artifacts.png_path << '\n';
    }
    return 0;
}
