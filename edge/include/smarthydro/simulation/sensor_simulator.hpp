#pragma once

/**
 * @file sensor_simulator.hpp
 * @brief Modello delle sonde e degli errori strumentali applicati allo stato.
 */

#include <smarthydro/simulation/environment_simulator.hpp>

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
 * @brief Parametri di calibrazione delle sonde inserite nel terriccio.
 *
 * La sonda capacitiva viene calibrata direttamente in umidita percentuale.
 * La sonda resistiva misura invece la conducibilita elettrica apparente del
 * volume di terriccio attraversato dalla corrente. Quest'ultima viene corretta
 * con l'umidita stimata per ricavare la EC dell'acqua nei pori.
 */
struct SoilProbeModelConfig {
    /** Capacita elettrica equivalente del terriccio asciutto, in pF. */
    double dry_capacitance_pf = 25.0;
    /** Capacita elettrica equivalente a saturazione, in pF. */
    double saturated_capacitance_pf = 85.0;
    /** Esponente che lega contenuto d'acqua e conducibilita apparente. */
    double conductivity_moisture_exponent = 1.30;
    /** Frazione minima usata nella correzione EC per evitare instabilita. */
    double minimum_moisture_fraction = 0.05;
    /** EC di fondo non attribuita ai fertilizzanti, in mS/cm. */
    double background_ec_ms_cm = 0.0;
    /** Conversione empirica da EC netta a fertilizzante totale, in mg/L. */
    double fertilizer_mg_per_liter_per_ms_cm = 400.0 / 1.8;
};

/**
 * @brief Configurazione strumentale dei sei canali fisici simulati.
 *
 * I valori predefiniti rappresentano sensori con bias e correzione nulli,
 * dropout dello 0,1% e precisioni diverse per ogni grandezza.
 */
struct SensorConfig {
    /** Temperatura in gradi Celsius; uscita limitata a [-50, 80]. */
    SensorChannelConfig temperature{0.0, 0.10, 0.01, 0.001, 0.0};
    /** Umidita relativa in percentuale; uscita limitata a [0, 100]. */
    SensorChannelConfig air_humidity{0.0, 0.50, 0.10, 0.001, 0.0};
    /**
     * Errore sul segnale della sonda capacitiva, in pF; la stima finale viene
     * poi convertita nell'intervallo [0, 100] percento.
     */
    SensorChannelConfig soil_moisture{0.0, 1.0, 0.10, 0.001, 0.0};
    /**
     * Conducibilita apparente ottenuta dalla sonda resistiva, in mS/cm;
     * uscita limitata a [0, 8].
     */
    SensorChannelConfig soil_conductivity{0.0, 0.02, 0.01, 0.001, 0.0};
    /** Elettrodo di pH; uscita limitata a [0, 14]. */
    SensorChannelConfig ph{0.0, 0.01, 0.01, 0.001, 0.0};
    /**
     * Sensore PAR in umol/(m2 s); uscita limitata a [0, 3000]. Il buio fisico
     * esatto viene preservato quando bias e correzione sono nulli.
     */
    SensorChannelConfig light_ppfd{0.0, 5.0, 1.0, 0.001, 0.0};
    /** Parametri della fusione tra sonda capacitiva e resistiva. */
    SoilProbeModelConfig soil_probe_model{};
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
    /**
     * EC apparente del terriccio fornita dalla sonda resistiva dopo la
     * conversione resistenza-conducibilita, in mS/cm.
     */
    std::optional<double> soil_bulk_ec_ms_cm;
    /** EC stimata dell'acqua nei pori, corretta con l'umidita, in mS/cm. */
    std::optional<double> soil_ec_ms_cm;
    /** Concentrazione totale stimata dei fertilizzanti solubili, in mg/L. */
    std::optional<double> fertilizer_concentration_mg_per_liter;
    /** Quota stimata di azoto, ottenuta usando la composizione del modello. */
    std::optional<double> nitrogen_estimate_mg_per_liter;
    /** Quota stimata di fosforo, ottenuta usando la composizione del modello. */
    std::optional<double> phosphorus_estimate_mg_per_liter;
    /** Quota stimata di potassio, ottenuta usando la composizione del modello. */
    std::optional<double> potassium_estimate_mg_per_liter;
    /** pH dell'acqua nei pori del terriccio, o nessun valore per dropout. */
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
     * @param config Caratteristiche strumentali copiate nei sei canali fisici.
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
     * @return Campione col medesimo timestamp dello stato, sei letture fisiche
     * e le stime derivate disponibili.
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

/**
 * @brief Aggiorna EC, fertilizzante totale e quote N/P/K derivate dalle sonde.
 *
 * La funzione combina `soil_moisture_percent` e `soil_bulk_ec_ms_cm`. Le quote
 * N/P/K non sono tre misure: ripartiscono la concentrazione totale secondo le
 * proporzioni note al modello di bilancio radicale.
 */
void update_soil_probe_estimates(
    SensorReadings& readings,
    const EnvironmentState& environment_state,
    const SoilProbeModelConfig& config = {});

}  // namespace smarthydro
