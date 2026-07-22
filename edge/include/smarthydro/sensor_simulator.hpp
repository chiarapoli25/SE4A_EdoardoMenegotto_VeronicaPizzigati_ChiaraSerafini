#pragma once

#include <cstdint>
#include <random>

namespace smarthydro {

/**
 * @brief Insieme delle misure restituite dal simulatore dei sensori.
 */
struct SensorReadings {
    /** Temperatura dell'ambiente in gradi Celsius. */
    double temperature_c;

    /** Umidita relativa dell'aria espressa in percentuale. */
    double air_humidity_percent;

    /** Valore di pH della soluzione nutritiva. */
    double ph;

    /** Intensita luminosa normalizzata tra 0% e 100%. */
    double light_percent;
};

/**
 * @brief Simula l'evoluzione temporale dei sensori ambientali di SmartHydro.
 *
 * Ogni chiamata a read() fa avanzare di un passo discreto temperatura,
 * umidita dell'aria, pH e luce, mantenendo le misure negli intervalli previsti
 * dal simulatore.
 */
class SensorSimulator {
public:
    /**
     * @brief Costruisce un simulatore con un seme casuale.
     *
     * Esecuzioni diverse producono normalmente sequenze diverse.
     */
    SensorSimulator();

    /**
     * @brief Costruisce un simulatore con una sequenza riproducibile.
     * @param seed Seme del generatore pseudocasuale.
     */
    explicit SensorSimulator(std::uint32_t seed);

    /**
     * @brief Fa avanzare la simulazione di un passo e legge tutti i sensori.
     * @return Le nuove letture di temperatura, umidita, pH e luce.
     */
    SensorReadings read();

private:
    std::mt19937 generator_;
    SensorReadings current_readings_;
};

}  // namespace smarthydro
