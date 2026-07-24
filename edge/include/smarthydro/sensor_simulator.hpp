#pragma once

/**
 * @file sensor_simulator.hpp
 * @brief Modello degli errori strumentali applicati allo stato ambientale.
 */

#include "smarthydro/environment_simulator.hpp"

#include <cstdint>
#include <optional>
#include <random>

namespace smarthydro {

/**
 * @brief Errori e caratteristiche di misura di un singolo canale.
 *
 * Una misura valida viene calcolata come:
 * `valore fisico + bias + correzione di calibrazione + rumore`.
 * Il risultato viene quindi quantizzato, se richiesto, e limitato
 * all'intervallo fisico previsto per quel canale.
 */
struct SensorChannelConfig {
    /** Errore sistematico additivo, espresso nell'unita del canale. */
    double bias = 0.0;
    /**
     * Deviazione standard non negativa del rumore gaussiano, nell'unita del
     * canale. Zero disabilita il rumore casuale.
     */
    double noise_standard_deviation = 0.0;
    /**
     * Passo di quantizzazione non negativo, nell'unita del canale. Il valore
     * viene arrotondato al multiplo piu vicino; zero disabilita l'operazione.
     */
    double resolution = 0.0;
    /**
     * Probabilita nell'intervallo [0, 1] che ogni singola lettura restituisca
     * std::nullopt. L'estrazione precede tutte le altre trasformazioni.
     */
    double dropout_probability = 0.001;
    /**
     * Correzione additiva di calibrazione, nell'unita del canale. Puo essere
     * usata per compensare un bias noto senza modificare il valore fisico.
     */
    double calibration_correction = 0.0;
};

/**
 * @brief Configurazione strumentale dei cinque canali simulati.
 *
 * I valori predefiniti rappresentano sensori con bias e correzione nulli,
 * dropout dello 0,1% e precisioni diverse per ogni grandezza.
 */
struct SensorConfig {
    /** Temperatura in gradi Celsius; uscita limitata a [-50, 80]. */
    SensorChannelConfig temperature{0.0, 0.10, 0.01, 0.001, 0.0};
    /** Umidita relativa in percentuale; uscita limitata a [0, 100]. */
    SensorChannelConfig air_humidity{0.0, 0.50, 0.10, 0.001, 0.0};
    /** Umidita del terriccio in percentuale; uscita limitata a [0, 100]. */
    SensorChannelConfig soil_moisture{0.0, 1.0, 0.10, 0.001, 0.0};
    /** Elettrodo di pH; uscita limitata a [0, 14]. */
    SensorChannelConfig ph{0.0, 0.01, 0.01, 0.001, 0.0};
    /**
     * Sensore PAR in umol/(m2 s); uscita limitata a [0, 3000]. Il buio fisico
     * esatto viene preservato quando bias e correzione sono nulli.
     */
    SensorChannelConfig light_ppfd{0.0, 5.0, 1.0, 0.001, 0.0};
};

/**
 * @brief Campione sincronizzato dei sensori simulati.
 *
 * Il timestamp e sempre disponibile; ciascun canale e invece indipendentemente
 * opzionale per rappresentare un dropout strumentale.
 */
struct SensorReadings {
    /** Timestamp della misura, in secondi simulati. */
    double timestamp_seconds = 0.0;
    /** Temperatura in gradi Celsius, oppure nessun valore in caso di dropout. */
    std::optional<double> temperature_c;
    /** Umidita relativa in percentuale, oppure nessun valore in caso di dropout. */
    std::optional<double> air_humidity_percent;
    /** Umidita del terriccio in percentuale, oppure nessun valore per dropout. */
    std::optional<double> soil_moisture_percent;
    /** pH della soluzione nei pori del terriccio, o nessun valore per dropout. */
    std::optional<double> ph;
    /** PPFD in umol/(m2 s), oppure nessun valore in caso di dropout. */
    std::optional<double> light_ppfd_umol_m2_s;
};

/**
 * @brief Simula esclusivamente il comportamento strumentale dei sensori.
 *
 * @details Il simulatore non possiede dinamiche ambientali e non fa avanzare
 * il tempo. read() osserva un EnvironmentState gia calcolato e, per ogni
 * canale, applica nell'ordine:
 *
 * 1. estrazione dell'eventuale dropout;
 * 2. bias e correzione di calibrazione;
 * 3. rumore gaussiano;
 * 4. quantizzazione alla risoluzione configurata;
 * 5. saturazione nell'intervallo fisico del sensore.
 *
 * Il generatore casuale strumentale e separato da quello di
 * EnvironmentSimulator: leggere i sensori non altera nuvole, temperatura o
 * altre dinamiche fisiche.
 */
class SensorSimulator {
public:
    /**
     * @brief Costruisce sensori predefiniti usando un seme casuale.
     *
     * @param config Caratteristiche strumentali copiate nei cinque canali.
     * @throws std::invalid_argument Se un valore non e finito, se rumore o
     * risoluzione sono negativi, oppure se il dropout non appartiene a [0, 1].
     * @note Il seed casuale rende non riproducibili due istanze indipendenti.
     */
    explicit SensorSimulator(SensorConfig config = {});

    /**
     * @brief Costruisce sensori predefiniti con rumore riproducibile.
     *
     * @param seed Seme del generatore di dropout e rumore gaussiano.
     * @throws std::invalid_argument Se la configurazione predefinita non e valida.
     * @note La riproducibilita richiede anche la stessa sequenza e lo stesso
     * numero di chiamate a read().
     */
    explicit SensorSimulator(std::uint32_t seed);

    /**
     * @brief Costruisce sensori configurabili con rumore riproducibile.
     * @param config Caratteristiche strumentali dei canali.
     * @param seed Seme del generatore di dropout e rumore gaussiano.
     * @throws std::invalid_argument Se bias, rumore, risoluzione, dropout o
     * calibrazione non sono finiti o non rispettano i limiti documentati.
     */
    SensorSimulator(SensorConfig config, std::uint32_t seed);

    /**
     * @brief Misura uno stato ambientale senza modificarlo.
     *
     * La funzione non cambia environment_state e non incrementa il tempo
     * simulato, ma avanza il generatore casuale interno. Due letture consecutive
     * dello stesso stato possono quindi differire.
     *
     * @param environment_state Stato fisico da osservare.
     * @return Campione col medesimo timestamp dello stato e cinque letture;
     * ciascuna lettura puo essere std::nullopt per dropout.
     */
    SensorReadings read(const EnvironmentState& environment_state);

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
