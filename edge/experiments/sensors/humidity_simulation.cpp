#include "experiment_utils.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    smarthydro::SensorSimulator simulator;
    const smarthydro::experiments::SensorExperimentConfig config{
        "humidity",
        "Simulazione dell'umidita dell'aria",
        "Umidita relativa dell'aria",
        "%",
        "air_humidity_percent"};

    return smarthydro::experiments::run_sensor_experiment(
        config, [&simulator] { return simulator.read().air_humidity_percent; });
}
