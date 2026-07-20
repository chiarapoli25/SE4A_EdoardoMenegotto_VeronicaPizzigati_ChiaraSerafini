#pragma once

#include <cstdint>
#include <random>

namespace smarthydro {

struct SensorReadings {
    double temperature_c;
    double air_humidity_percent;
    double ph;
    double light_percent;
};

class SensorSimulator {
public:
    SensorSimulator();
    explicit SensorSimulator(std::uint32_t seed);

    SensorReadings read();

private:
    std::mt19937 generator_;
    SensorReadings current_readings_;
};

}  // namespace smarthydro
