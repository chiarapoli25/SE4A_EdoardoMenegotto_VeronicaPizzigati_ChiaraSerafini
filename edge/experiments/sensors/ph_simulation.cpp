#include "experiment_utils.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    smarthydro::SensorSimulator simulator;
    const smarthydro::experiments::SensorExperimentConfig config{
        "ph",
        "Simulazione del pH",
        "pH della soluzione",
        "pH",
        "ph"};

    return smarthydro::experiments::run_sensor_experiment(
        config, [&simulator] { return simulator.read().ph; });
}
