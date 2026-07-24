#include "smarthydro/actuator_simulator.hpp"
#include "smarthydro/environment_simulator.hpp"
#include "smarthydro/sensor_simulator.hpp"

#include <iomanip>
#include <iostream>

int main() {
    std::cout << "SmartHydro Edge Controller\n"
              << "Version: 0.1.0\n"
              << "Status: ready\n";

    smarthydro::ActuatorSimulator actuators;
    smarthydro::EnvironmentSimulator environment;
    smarthydro::SensorSimulator sensors;

    // La pompa calcola il volume erogato nel passo; l'ambiente lo assorbe.
    actuators.step(900.0);
    environment.step(900.0, actuators.output());
    const auto readings = sensors.read(environment.state());
    const auto& actuator_command = actuators.command();
    const auto& actuator_output = actuators.output();

    std::cout << std::fixed << std::setprecision(1)
              << "Sensor sample:\n"
              << "Temperature: " << readings.temperature_c.value_or(0.0) << " C\n"
              << "Air humidity: " << readings.air_humidity_percent.value_or(0.0) << " %\n"
              << "Soil moisture: " << readings.soil_moisture_percent.value_or(0.0)
              << " %\n"
              << "pH: " << readings.ph.value_or(0.0) << "\n"
              << "Light: " << readings.light_ppfd_umol_m2_s.value_or(0.0)
              << " umol/(m2 s)\n"
              << "Physical nutrients (not directly sensed): N "
              << environment.state().nitrogen_mg_per_liter << ", P "
              << environment.state().phosphorus_mg_per_liter << ", K "
              << environment.state().potassium_mg_per_liter << " mg/L\n"
              << "Actuator safe state (command -> physical output):\n"
              << "Water pump request: "
              << actuator_command.requested_irrigation_volume_liters << " L\n"
              << "Water delivered in step: "
              << actuator_output.irrigation_volume_liters_last_step << " L\n"
              << "Lighting: " << actuator_command.lighting_percent << " % -> "
              << actuator_output.lighting_power_watts << " W\n";

    std::cout << "Fertilizer valves (command -> flow -> last volume):\n";
    for (const auto type : {
             smarthydro::FertilizerType::NITROGEN,
             smarthydro::FertilizerType::PHOSPHORUS,
             smarthydro::FertilizerType::POTASSIUM,
             smarthydro::FertilizerType::PH_UP,
             smarthydro::FertilizerType::PH_DOWN}) {
        std::cout << "  " << smarthydro::to_string(type) << ": "
                  << (actuator_command.fertilizer_valves_open[
                          smarthydro::fertilizer_index(type)]
                          ? "OPEN"
                          : "CLOSED")
                  << " -> "
                  << actuators.fertilizer_flow_milliliters_per_hour(type)
                  << " mL/h -> "
                  << actuators.fertilizer_volume_milliliters_last_step(type)
                  << " mL\n";
    }

    return 0;
}
