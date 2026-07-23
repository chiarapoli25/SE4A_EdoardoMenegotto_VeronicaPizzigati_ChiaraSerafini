#include "sensor_experiment_runner.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    const smarthydro::experiments::SensorExperimentConfig config{
        "ph",
        "Simulazione del pH",
        "pH della soluzione",
        "pH",
        "ph"};

    return smarthydro::experiments::run_sensor_experiment(
        config,
        [](const smarthydro::SensorReadings& readings) { return readings.ph; });
}
