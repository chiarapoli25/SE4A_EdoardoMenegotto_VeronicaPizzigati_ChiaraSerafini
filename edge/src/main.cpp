#include "smarthydro/actuator_manager.hpp"
#include "smarthydro/sensor_simulator.hpp"

#include <iomanip>
#include <iostream>

int main() {
    std::cout << "SmartHydro Edge Controller\n"
              << "Version: 0.1.0\n"
              << "Status: ready\n";

    smarthydro::SensorSimulator sensors;
    const auto readings = sensors.read();

    const smarthydro::ActuatorManager actuators;
    const auto& actuator_state = actuators.state();

    std::cout << std::fixed << std::setprecision(1)
              << "Sensor sample:\n"
              << "Temperature: " << readings.temperature_c << " C\n"
              << "Air humidity: " << readings.air_humidity_percent << " %\n"
              << "pH: " << readings.ph << "\n"
              << "Light: " << readings.light_percent << " %\n"
              << "Actuator safe state:\n"
              << "Water pump: " << actuator_state.water_pump_percent << " %\n"
              << "Fertilizer: none selected\n"
              << "Fertilizer dosing: " << actuator_state.fertilizer_dosing_percent << " %\n"
              << "Lighting: " << actuator_state.lighting_percent << " %\n";

    return 0;
}
