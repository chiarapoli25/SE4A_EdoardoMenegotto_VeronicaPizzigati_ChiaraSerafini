#pragma once

#include "smarthydro/environment_simulator.hpp"

#include <cstdint>
#include <optional>
#include <random>

namespace smarthydro {

/** @brief Errori e caratteristiche di misura di un singolo canale. */
struct SensorChannelConfig {
    /** Errore sistematico aggiunto alla grandezza fisica. */
    double bias = 0.0;
    /** Deviazione standard del rumore gaussiano di misura. */
    double noise_standard_deviation = 0.0;
    /** Passo di quantizzazione; zero disabilita la quantizzazione. */
    double resolution = 0.0;
    /** Probabilita, tra 0 e 1, che una lettura risulti mancante. */
    double dropout_probability = 0.001;
    /** Correzione additiva applicata durante la calibrazione. */
    double calibration_correction = 0.0;
};

/** @brief Configurazione dei quattro sensori pubblici di SmartHydro. */
struct SensorConfig {
    /** Configurazione del sensore di temperatura. */
    SensorChannelConfig temperature{0.0, 0.10, 0.01, 0.001, 0.0};
    /** Configurazione del sensore di umidita relativa. */
    SensorChannelConfig air_humidity{0.0, 0.50, 0.10, 0.001, 0.0};
    /** Configurazione dell'elettrodo di pH. */
    SensorChannelConfig ph{0.0, 0.01, 0.01, 0.001, 0.0};
    /** Configurazione del sensore PAR, espressa in PPFD. */
    SensorChannelConfig light_ppfd{0.0, 5.0, 1.0, 0.001, 0.0};
};

/** @brief Misure osservate nello stesso istante della simulazione. */
struct SensorReadings {
    /** Timestamp della misura, in secondi simulati. */
    double timestamp_seconds = 0.0;
    /** Temperatura in gradi Celsius, oppure nessun valore in caso di dropout. */
    std::optional<double> temperature_c;
    /** Umidita relativa in percentuale, oppure nessun valore in caso di dropout. */
    std::optional<double> air_humidity_percent;
    /** pH della soluzione nei pori del terriccio, o nessun valore per dropout. */
    std::optional<double> ph;
    /** PPFD in umol/(m2 s), oppure nessun valore in caso di dropout. */
    std::optional<double> light_ppfd_umol_m2_s;
};

/**
 * @brief Simula esclusivamente il comportamento strumentale dei sensori.
 *
 * Il sensore non possiede la dinamica fisica e non fa avanzare il tempo:
 * read() osserva un EnvironmentState gia calcolato.
 */
class SensorSimulator {
public:
    /**
     * @brief Costruisce sensori predefiniti usando un seme casuale.
     * @param config Caratteristiche strumentali dei canali.
     * @throws std::invalid_argument Se un parametro del sensore non e valido.
     */
    explicit SensorSimulator(SensorConfig config = {});

    /**
     * @brief Costruisce sensori predefiniti con rumore riproducibile.
     * @param seed Seme del generatore del rumore strumentale.
     * @throws std::invalid_argument Se la configurazione predefinita non e valida.
     */
    explicit SensorSimulator(std::uint32_t seed);

    /**
     * @brief Costruisce sensori configurabili con rumore riproducibile.
     * @param config Caratteristiche strumentali dei canali.
     * @param seed Seme del generatore del rumore strumentale.
     * @throws std::invalid_argument Se bias, rumore, risoluzione, dropout o
     *         calibrazione non sono validi.
     */
    SensorSimulator(SensorConfig config, std::uint32_t seed);

    /**
     * @brief Misura uno stato ambientale senza modificarlo.
     * @param environment_state Stato fisico da osservare.
     * @return Timestamp e quattro letture, eventualmente mancanti.
     */
    SensorReadings read(const EnvironmentState& environment_state);

    /**
     * @brief Restituisce la configurazione strumentale validata.
     * @return Riferimento alla configurazione posseduta dal simulatore.
     */
    const SensorConfig& config() const noexcept;

private:
    std::optional<double> measure(
        double physical_value,
        const SensorChannelConfig& channel,
        double minimum_value,
        double maximum_value,
        bool preserve_physical_zero = false);

    SensorConfig config_;
    std::mt19937 generator_;
};

}  // namespace smarthydro
