#include "sensor_experiment_runner.hpp"
#include "smarthydro/sensor_simulator.hpp"

int main() {
    const smarthydro::experiments::SensorExperimentConfig config{
        "light",
        "Simulazione della luce",
        "PPFD",
        "umol/(m2 s)",
        "light_ppfd_umol_m2_s"};

    return smarthydro::experiments::run_sensor_experiment(
        config,
        [](const smarthydro::SensorReadings& readings) {
            return readings.light_ppfd_umol_m2_s;
        });
}
