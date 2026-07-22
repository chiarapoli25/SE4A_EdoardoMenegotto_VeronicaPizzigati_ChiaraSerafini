#include "experiment_utils.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    smarthydro::SensorSimulator simulator;
    const smarthydro::experiments::SensorExperimentConfig config{
        "light",
        "Simulazione della luce",
        "Intensita luminosa",
        "%",
        "light_percent"};

    return smarthydro::experiments::run_sensor_experiment(
        config, [&simulator] { return simulator.read().light_percent; });
}
