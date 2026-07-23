#include "sensor_experiment_runner.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    const smarthydro::experiments::SensorExperimentConfig config{
        "temperature",
        "Simulazione della temperatura",
        "Temperatura",
        "gradi Celsius",
        "temperature_c"};

    return smarthydro::experiments::run_sensor_experiment(
        config,
        [](const smarthydro::SensorReadings& readings) { return readings.temperature_c; });
}
