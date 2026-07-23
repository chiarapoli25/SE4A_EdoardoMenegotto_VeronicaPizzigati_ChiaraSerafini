#include "sensor_experiment_runner.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    const smarthydro::experiments::SensorExperimentConfig config{
        "soil_moisture",
        "Simulazione dell'umidita del terriccio",
        "Umidita del terriccio",
        "%",
        "soil_moisture_percent"};

    return smarthydro::experiments::run_sensor_experiment(
        config,
        [](const smarthydro::SensorReadings& readings) {
            return readings.soil_moisture_percent;
        });
}
