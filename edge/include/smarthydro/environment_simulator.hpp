#pragma once

#include "smarthydro/actuator_simulator.hpp"

#include <cstdint>
#include <random>
#include <string>
#include <vector>

namespace smarthydro {

/** @brief Tipo di terriccio usato nella zona radicale. */
enum class SoilType {
    /** Terriccio universale alleggerito e aerato, con comportamento bilanciato. */
    AERATED_UNIVERSAL,
    /** Terriccio ad alta capacita di drenaggio e asciugatura rapida. */
    DRAINING,
    /** Terriccio ricco di sostanza organica e ad elevata ritenzione idrica. */
    ORGANIC_RETENTIVE,
};

/** @brief Effetti semplificati di un concime sulla soluzione nel terriccio. */
struct FertilizerProfile {
    /** Identificativo usato da ActuatorOutput::selected_fertilizer_id. */
    std::string id;
    /** Incremento di EC, in mS/cm, per millilitro di concime miscelato. */
    double ec_increase_ms_cm_per_milliliter = 0.0;
    /** Variazione di pH per millilitro di concime miscelato. */
    double ph_change_per_milliliter = 0.0;
};

/** @brief Parametri del modello didattico della coltivazione in terriccio. */
struct EnvironmentConfig {
    /** Nome descrittivo della coltura e della fase fenologica. */
    std::string crop_name = "tomato-vegetative";
    /** Terriccio simulato. */
    SoilType soil_type = SoilType::AERATED_UNIVERSAL;
    /** Ora solare dell'alba, nell'intervallo [0, 24). */
    double sunrise_hour = 6.0;
    /** Durata del fotoperiodo naturale, in ore. */
    double photoperiod_hours = 14.0;
    /** Temperatura esterna caratteristica della notte, in gradi Celsius. */
    double night_temperature_c = 18.0;
    /** Temperatura esterna caratteristica del giorno, in gradi Celsius. */
    double day_temperature_c = 24.0;
    /** Umidita relativa esterna caratteristica della notte. */
    double night_relative_humidity_percent = 80.0;
    /** Umidita relativa esterna caratteristica del giorno. */
    double day_relative_humidity_percent = 65.0;
    /** pH iniziale della soluzione presente nei pori del terriccio. */
    double initial_ph = 6.3;
    /** EC iniziale della soluzione presente nei pori, in mS/cm. */
    double initial_ec_ms_cm = 1.8;
    /** Picco della luce naturale, in micromoli per metro quadrato al secondo. */
    double natural_light_peak_ppfd = 700.0;
    /** Trasmissione atmosferica media in assenza di lampade, tra 0 e 1. */
    double mean_cloud_transmission = 0.82;
    /** Deviazione standard del regime nuvoloso estratto per ogni giorno. */
    double daily_cloud_transmission_stddev = 0.16;
    /** Deviazione standard delle oscillazioni nuvolose nell'arco di alcune ore. */
    double hourly_cloud_transmission_stddev = 0.10;
    /** Tempo caratteristico delle variazioni orarie della nuvolosita. */
    double cloud_persistence_hours = 1.5;
    /** PPFD sul piano di coltivazione per watt elettrico delle lampade. */
    double lamp_ppfd_umol_m2_s_per_watt = 2.0;
    /** Incremento termico di equilibrio per watt elettrico delle lampade. */
    double lamp_heating_c_per_watt = 0.015;
    /** Profili di concime riconosciuti dal modello. */
    std::vector<FertilizerProfile> fertilizer_profiles{
        {"tomato-growth", 0.04, -0.003},
    };
};

/** @brief Stato fisico istantaneo della serra simulata. */
struct EnvironmentState {
    /** Tempo trascorso dall'inizio della simulazione, in secondi. */
    double simulation_time_seconds = 0.0;
    /** Temperatura dell'aria, in gradi Celsius. */
    double temperature_c = 18.0;
    /** Umidita relativa dell'aria, in percentuale. */
    double air_humidity_percent = 80.0;
    /** pH della soluzione presente nei pori del terriccio. */
    double ph = 6.3;
    /** Conducibilita elettrica della soluzione nei pori, in mS/cm. */
    double ec_ms_cm = 1.8;
    /** Acqua disponibile rispetto alla capacita utile del terriccio, in percentuale. */
    double soil_moisture_percent = 75.0;
    /** Densita di flusso fotonico fotosintetico (PPFD). */
    double light_ppfd_umol_m2_s = 0.0;
};

/**
 * @brief Simula le dinamiche fisiche condivise da sensori e attuatori.
 *
 * Il modello e didattico e plausibile, ma non e calibrato per prendere
 * decisioni agronomiche reali. Lo stato avanza solo tramite step().
 */
class EnvironmentSimulator {
public:
    /**
     * @brief Costruisce un ambiente usando un seme casuale.
     * @param config Configurazione della serra e del terriccio.
     * @throws std::invalid_argument Se la configurazione non e valida.
     */
    explicit EnvironmentSimulator(EnvironmentConfig config = {});

    /**
     * @brief Costruisce un ambiente con dinamiche riproducibili.
     * @param config Configurazione della serra e del terriccio.
     * @param seed Seme del rumore fisico lento.
     * @throws std::invalid_argument Se la configurazione non e valida.
     */
    EnvironmentSimulator(EnvironmentConfig config, std::uint32_t seed);

    /**
     * @brief Fa avanzare ambiente e coltura sotto l'effetto degli attuatori.
     * @param delta_time_seconds Durata positiva del passo, in secondi.
     * @param actuator_output Volume d'acqua erogato nel passo, portata del
     *         dosatore e potenza delle lampade.
     * @throws std::invalid_argument Se il passo non e finito/positivo o se un
     *         concime dosato non appartiene ai profili configurati.
     */
    void step(double delta_time_seconds, const ActuatorOutput& actuator_output);

    /**
     * @brief Restituisce lo stato fisico corrente senza modificarlo.
     * @return Riferimento allo stato posseduto dal simulatore.
     */
    const EnvironmentState& state() const noexcept;

    /**
     * @brief Restituisce la configurazione validata del modello.
     * @return Riferimento alla configurazione posseduta dal simulatore.
     */
    const EnvironmentConfig& config() const noexcept;

private:
    void integrate_substep(double delta_time_seconds, const ActuatorOutput& actuator_output);

    EnvironmentConfig config_;
    EnvironmentState state_;
    std::mt19937 generator_;
    double daily_cloud_transmission_ = 0.82;
    double hourly_cloud_deviation_ = 0.0;
    std::uint64_t cloud_regime_day_ = 0;
    double temperature_disturbance_c_ = 0.0;
    double vapor_density_g_m3_ = 0.0;
};

/**
 * @brief Crea esplicitamente la configurazione predefinita per pomodoro.
 * @return Configurazione per fase vegetativa e terriccio universale aerato.
 */
EnvironmentConfig make_default_tomato_environment_config();

/**
 * @brief Restituisce un nome breve e stabile del tipo di terriccio.
 * @param soil_type Terriccio da convertire.
 * @return Stringa statica adatta anche ai nomi dei file.
 */
const char* to_string(SoilType soil_type) noexcept;

}  // namespace smarthydro
