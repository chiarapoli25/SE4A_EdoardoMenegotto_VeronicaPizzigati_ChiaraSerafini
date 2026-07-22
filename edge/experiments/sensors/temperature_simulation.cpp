#include "experiment_utils.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    smarthydro::SensorSimulator simulator;
    const smarthydro::experiments::SensorExperimentConfig config{
        "temperature",
        "Simulazione della temperatura",
        "Temperatura",
        "gradi Celsius",
        "temperature_c"};

    return smarthydro::experiments::run_sensor_experiment(
        config, [&simulator] { return simulator.read().temperature_c; });
}
