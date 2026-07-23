#include "sensor_experiment_runner.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    const smarthydro::experiments::SensorExperimentConfig config{
        "humidity",
        "Simulazione dell'umidita dell'aria",
        "Umidita relativa dell'aria",
        "%",
        "air_humidity_percent"};

    return smarthydro::experiments::run_sensor_experiment(
        config,
        [](const smarthydro::SensorReadings& readings) {
            return readings.air_humidity_percent;
        });
}
