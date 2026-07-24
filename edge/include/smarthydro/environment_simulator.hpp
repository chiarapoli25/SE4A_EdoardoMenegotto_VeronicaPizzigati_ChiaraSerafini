#pragma once

/**
 * @file environment_simulator.hpp
 * @brief Stato, configurazione e modello dinamico dell'ambiente simulato.
 *
 * Questo modulo possiede le grandezze fisiche della serra. Gli attuatori
 * forniscono gli ingressi a EnvironmentSimulator::step(); i sensori leggono
 * successivamente EnvironmentState senza modificarlo.
 */

#include "smarthydro/actuator_simulator.hpp"

#include <array>
#include <cstdint>
#include <random>

namespace smarthydro {

/**
 * @brief Tipo di terriccio usato nella zona radicale.
 *
 * La scelta determina capacita idrica, ritenzione dell'irrigazione,
 * drenaggio, lisciviazione dell'EC ed equilibrio lento del pH. Non rappresenta
 * invece una specie vegetale: le dinamiche sono indipendenti dalla coltura.
 */
enum class SoilType {
    /** Terriccio universale alleggerito e aerato, con comportamento bilanciato. */
    AERATED_UNIVERSAL,
    /** Terriccio ad alta capacita di drenaggio e asciugatura rapida. */
    DRAINING,
    /** Terriccio ricco di sostanza organica e ad elevata ritenzione idrica. */
    ORGANIC_RETENTIVE,
};

/**
 * @brief Composizione ed effetti didattici di un concentrato liquido.
 */
struct FertilizerProfile {
    /** Serbatoio al quale si applica il profilo. */
    FertilizerType type = FertilizerType::NITROGEN;
    /** Massa di azoto aggiunta da un millilitro di prodotto. */
    double nitrogen_milligrams_per_milliliter = 0.0;
    /** Massa di fosforo aggiunta da un millilitro di prodotto. */
    double phosphorus_milligrams_per_milliliter = 0.0;
    /** Massa di potassio aggiunta da un millilitro di prodotto. */
    double potassium_milligrams_per_milliliter = 0.0;
    /** Incremento didattico di EC, in mS/cm per mL. */
    double ec_increase_ms_cm_per_milliliter = 0.0;
    /** Variazione di pH per mL; negativa acidifica, positiva alcalinizza. */
    double ph_change_per_milliliter = 0.0;
};

/**
 * @brief Parametri del modello dinamico dell'ambiente in terriccio.
 *
 * I parametri descrivono condizioni esterne, illuminazione, stato iniziale e
 * prodotti dosabili. Non contengono informazioni sulla specie coltivata.
 * Tutti i valori vengono validati alla costruzione di EnvironmentSimulator.
 */
struct EnvironmentConfig {
    /** Terriccio simulato e insieme di coefficienti fisici associati. */
    SoilType soil_type = SoilType::AERATED_UNIVERSAL;
    /**
     * Ora solare dell'alba nell'intervallo [0, 24). Il fattore solare vale
     * zero prima di quest'ora e segue una semionda sinusoidale dopo l'alba.
     */
    double sunrise_hour = 6.0;
    /** Durata positiva del fotoperiodo naturale, al massimo 24 ore. */
    double photoperiod_hours = 14.0;
    /** Temperatura esterna di riferimento notturna, in gradi Celsius. */
    double night_temperature_c = 18.0;
    /**
     * Temperatura esterna di riferimento diurna, in gradi Celsius. Il profilo
     * istantaneo interpola tra valore notturno e diurno col fattore solare.
     */
    double day_temperature_c = 24.0;
    /** Umidita relativa esterna notturna, nell'intervallo [0, 100]%. */
    double night_relative_humidity_percent = 80.0;
    /** Umidita relativa esterna diurna, nell'intervallo [0, 100]%. */
    double day_relative_humidity_percent = 65.0;
    /**
     * pH iniziale della soluzione nei pori, nell'intervallo [0, 14].
     * Funge anche da riferimento per l'equilibrio lento corretto dal terriccio.
     */
    double initial_ph = 6.3;
    /** EC iniziale non negativa della soluzione nei pori, in mS/cm. */
    double initial_ec_ms_cm = 1.8;
    /** Concentrazione iniziale di azoto nella soluzione radicale, in mg/L. */
    double initial_nitrogen_mg_per_liter = 150.0;
    /** Concentrazione iniziale di fosforo nella soluzione radicale, in mg/L. */
    double initial_phosphorus_mg_per_liter = 50.0;
    /** Concentrazione iniziale di potassio nella soluzione radicale, in mg/L. */
    double initial_potassium_mg_per_liter = 200.0;
    /** Assorbimento nominale di azoto ad attivita unitaria, in mg/h. */
    double nitrogen_uptake_milligrams_per_hour = 1.5;
    /** Assorbimento nominale di fosforo ad attivita unitaria, in mg/h. */
    double phosphorus_uptake_milligrams_per_hour = 0.3;
    /** Assorbimento nominale di potassio ad attivita unitaria, in mg/h. */
    double potassium_uptake_milligrams_per_hour = 1.8;
    /**
     * Volume minimo usato per rendere definita la concentrazione quando il
     * terriccio e quasi asciutto, in litri.
     */
    double minimum_effective_root_water_liters = 0.1;
    /** Limite numerico delle concentrazioni pubblicate, in mg/L. */
    double maximum_nutrient_concentration_mg_per_liter = 2000.0;
    /**
     * PPFD naturale non negativo al picco della semionda solare prima
     * dell'attenuazione dovuta alle nuvole, in umol/(m2 s).
     */
    double natural_light_peak_ppfd = 700.0;
    /**
     * Trasmissione atmosferica media, tra 0 e 1. Costituisce il centro della
     * distribuzione del regime nuvoloso giornaliero.
     */
    double mean_cloud_transmission = 0.82;
    /**
     * Deviazione standard non negativa del regime nuvoloso estratto all'inizio
     * di ogni giorno simulato.
     */
    double daily_cloud_transmission_stddev = 0.16;
    /**
     * Deviazione standard non negativa delle oscillazioni nuvolose correlate
     * su scala oraria.
     */
    double hourly_cloud_transmission_stddev = 0.10;
    /**
     * Tempo di persistenza positivo delle variazioni orarie, in ore. Valori
     * maggiori producono nuvole che cambiano piu lentamente.
     */
    double cloud_persistence_hours = 1.5;
    /**
     * Incremento non negativo di PPFD sul piano di coltivazione per watt
     * elettrico delle lampade, in umol/(m2 s W).
     */
    double lamp_ppfd_umol_m2_s_per_watt = 2.0;
    /**
     * Incremento non negativo della temperatura di equilibrio per watt
     * elettrico delle lampade, in gradi Celsius per watt.
     */
    double lamp_heating_c_per_watt = 0.015;
    /** Un profilo configurabile per ciascuno dei cinque serbatoi. */
    FertilizerValues<FertilizerProfile> fertilizer_profiles{{
        {FertilizerType::NITROGEN, 50.0, 0.0, 0.0, 0.040, -0.001},
        {FertilizerType::PHOSPHORUS, 0.0, 20.0, 0.0, 0.030, -0.002},
        {FertilizerType::POTASSIUM, 0.0, 0.0, 50.0, 0.035, 0.0},
        {FertilizerType::PH_UP, 0.0, 0.0, 0.0, 0.010, 0.020},
        {FertilizerType::PH_DOWN, 0.0, 0.0, 0.0, 0.010, -0.020},
    }};
};

/**
 * @brief Stato fisico istantaneo condiviso da ambiente e sensori.
 *
 * EnvironmentSimulator e l'unico componente che modifica questi valori.
 * SensorSimulator li osserva e produce misure affette da errori strumentali.
 */
struct EnvironmentState {
    /** Tempo trascorso dall'inizio della simulazione, in secondi. */
    double simulation_time_seconds = 0.0;
    /** Temperatura dell'aria, in gradi Celsius. */
    double temperature_c = 18.0;
    /** Umidita relativa dell'aria, limitata dal modello tra 20% e 99%. */
    double air_humidity_percent = 80.0;
    /** pH della soluzione nei pori, limitato dal modello tra 3 e 9. */
    double ph = 6.3;
    /** EC della soluzione nei pori, limitata tra 0 e 8 mS/cm. */
    double ec_ms_cm = 1.8;
    /** Concentrazione disponibile di azoto, in mg/L. */
    double nitrogen_mg_per_liter = 150.0;
    /** Concentrazione disponibile di fosforo, in mg/L. */
    double phosphorus_mg_per_liter = 50.0;
    /** Concentrazione disponibile di potassio, in mg/L. */
    double potassium_mg_per_liter = 200.0;
    /** Acqua disponibile rispetto alla capacita utile, nell'intervallo [0, 100]%. */
    double soil_moisture_percent = 75.0;
    /** PPFD naturale e artificiale totale, in umol/(m2 s), mai negativo. */
    double light_ppfd_umol_m2_s = 0.0;
};

/**
 * @brief Simula le dinamiche fisiche condivise da sensori e attuatori.
 *
 * @details Ogni step aggiorna, nell'ordine:
 *
 * 1. ora del giorno, regime nuvoloso e disturbo termico;
 * 2. PPFD naturale e artificiale;
 * 3. temperatura con una risposta del primo ordine;
 * 4. bilancio idrico del terriccio;
 * 5. densita di vapore e umidita relativa;
 * 6. pH, EC e bilancio di massa N/P/K.
 *
 * Nelle formule seguenti \f$\Delta t_h\f$ e la durata del sotto-passo in ore
 * e \f$\xi_k \sim \mathcal{N}(0,1)\f$ e un campione gaussiano standard.
 *
 * @par Ciclo solare e nuvolosita
 *
 * Posti \f$h_s\f$ ora dell'alba e \f$P\f$ durata del fotoperiodo, il fattore
 * solare e una semionda:
 *
 * \f[
 * s(h)=
 * \begin{cases}
 * \sin\!\left(\pi\dfrac{h-h_s}{P}\right),
 *     & 0 \le h-h_s \le P,\\
 * 0,  & \text{altrimenti}.
 * \end{cases}
 * \f]
 *
 * Il regime giornaliero \f$C_d\f$ viene estratto una volta al giorno:
 *
 * \f[
 * C_d=\mathrm{clamp}\!\left(\mu_C+\sigma_d\xi_k,\ 0.20,\ 1.0\right).
 * \f]
 *
 * La deviazione oraria \f$q_k\f$ segue una discretizzazione esatta di un
 * processo di Ornstein-Uhlenbeck con persistenza \f$\tau_C\f$:
 *
 * \f[
 * a=e^{-\Delta t_h/\tau_C}, \qquad
 * q_{k+1}=a q_k+\sigma_h\sqrt{1-a^2}\,\xi_k,
 * \f]
 *
 * \f[
 * C_k=\mathrm{clamp}\!\left(C_d+q_{k+1},\ 0.20,\ 1.0\right).
 * \f]
 *
 * @par Illuminazione
 *
 * Il PPFD totale somma luce naturale e lampade:
 *
 * \f[
 * L_k=\max\!\left(0,\ L_{\max}s(h)C_k+k_L P_{\mathrm{lamp}}\right).
 * \f]
 *
 * Qui \f$L_{\max}\f$ e il picco naturale, \f$k_L\f$ e il PPFD prodotto per
 * watt e \f$P_{\mathrm{lamp}}\f$ e la potenza elettrica corrente.
 *
 * @par Temperatura
 *
 * Il riferimento esterno interpola fra notte e giorno e include il disturbo
 * lento \f$d_T\f$:
 *
 * \f[
 * T_{\mathrm{ext}}=T_n+(T_d-T_n)s(h)+d_T.
 * \f]
 *
 * Sole e lampade spostano la temperatura di equilibrio:
 *
 * \f[
 * T_{\mathrm{eq}}=
 * T_{\mathrm{ext}}+0.0025\,L_{\mathrm{naturale}}
 * +k_T P_{\mathrm{lamp}}.
 * \f]
 *
 * La temperatura converge a tale equilibrio con costante temporale di
 * 1.7 ore:
 *
 * \f[
 * T_{k+1}=T_k+
 * \left(T_{\mathrm{eq}}-T_k\right)
 * \left(1-e^{-\Delta t_h/1.7}\right).
 * \f]
 *
 * @par Bilancio idrico del terriccio
 *
 * Si definiscono attivita luminosa \f$A_L\f$ e frazione idrica \f$\theta\f$:
 *
 * \f[
 * A_L=\mathrm{clamp}\!\left(\dfrac{L_k}{700},0,1.5\right),
 * \qquad \theta_k=\dfrac{M_k}{100}.
 * \f]
 *
 * Con consumo base \f$d\f$, efficienza di ritenzione \f$\eta\f$, capacita
 * idrica \f$W\f$, volume irrigato \f$V_w\f$, drenaggio \f$g\f$ e capacita di
 * campo \f$\theta_{fc}\f$:
 *
 * \f[
 * D=d(0.20+0.80A_L), \qquad
 * R=\dfrac{\eta V_w}{W},
 * \f]
 *
 * \f[
 * \theta_p=\theta_k+R-D\Delta t_h, \qquad
 * G=g\max(0,\theta_p-\theta_{fc}),
 * \f]
 *
 * \f[
 * \theta_{k+1}=
 * \mathrm{clamp}\!\left(\theta_p-G\Delta t_h,0,1\right),
 * \qquad M_{k+1}=100\theta_{k+1}.
 * \f]
 *
 * @par Umidita dell'aria
 *
 * Il modello integra la densita assoluta di vapore \f$\rho_v\f$. La densita
 * di saturazione usata per convertire temperatura e umidita relativa e:
 *
 * \f[
 * e_s(T)=6.112\exp\!\left(\dfrac{17.67T}{T+243.5}\right), \qquad
 * \rho_{\mathrm{sat}}(T)=216.7\dfrac{e_s(T)}{T+273.15}.
 * \f]
 *
 * Con umidita esterna \f$RH_{\mathrm{ext}}\f$:
 *
 * \f[
 * \rho_{\mathrm{ext}}=
 * \rho_{\mathrm{sat}}(T_{\mathrm{ext}})
 * \dfrac{RH_{\mathrm{ext}}}{100},
 * \f]
 *
 * \f[
 * \rho_{v,k+1}=\rho_{v,k}
 * +(\rho_{\mathrm{ext}}-\rho_{v,k})\dfrac{\Delta t_h}{2}
 * +0.30A_L\theta_{k+1}\Delta t_h+0.03V_w,
 * \f]
 *
 * \f[
 * RH_{k+1}=100
 * \dfrac{\rho_{v,k+1}}{\rho_{\mathrm{sat}}(T_{k+1})}.
 * \f]
 *
 * @par pH ed EC
 *
 * L'attivita di assorbimento e
 * \f$U=(0.20+0.80A_L)\theta_{k+1}\f$. Prima del concime:
 *
 * \f[
 * pH_{k+1}=pH_k+
 * (pH_{\mathrm{eq}}-pH_k)\dfrac{\Delta t_h}{24D_b}
 * +0.004U\dfrac{\Delta t_h}{24},
 * \f]
 *
 * \f[
 * EC_{k+1}=EC_k-0.025U\dfrac{\Delta t_h}{24}.
 * \f]
 *
 * Se il terriccio si asciuga, l'EC viene concentrata del fattore
 * \f$1+0.08(\theta_k-\theta_{k+1})\f$. L'irrigazione la porta invece verso
 * l'EC dell'acqua, fissata a 0.60 mS/cm:
 *
 * \f[
 * EC \leftarrow EC+(0.60-EC)k_{\mathrm{leach}}V_w.
 * \f]
 *
 * I millilitri consegnati da ogni serbatoio aggiungono la massa N/P/K definita
 * dal relativo FertilizerProfile. L'irrigazione diluisce, l'evaporazione
 * concentra, il drenaggio rimuove soluzione miscelata e l'assorbimento
 * sottrae massa senza poterla rendere negativa. Gli intervalli superiori a
 * cinque minuti vengono suddivisi in sotto-passi; acqua e cinque concentrati
 * sono ripartiti proporzionalmente.
 */
class EnvironmentSimulator {
public:
    /**
     * @brief Costruisce un ambiente usando un seme casuale.
     *
     * Inizializza temperatura, umidita, pH ed EC dai valori configurati;
     * l'umidita iniziale del terriccio dipende da EnvironmentConfig::soil_type.
     * Il regime nuvoloso iniziale viene estratto con std::random_device, quindi
     * due istanze con la stessa configurazione possono evolvere diversamente.
     *
     * @param config Configurazione fisica da copiare nel simulatore.
     * @throws std::invalid_argument Se un valore non e finito, viola il proprio
     * intervallo o identifica un tipo di terriccio non supportato.
     */
    explicit EnvironmentSimulator(EnvironmentConfig config = {});

    /**
     * @brief Costruisce un ambiente con dinamiche riproducibili.
     *
     * A parita di configurazione, seed, sequenza di step e ingressi degli
     * attuatori, due istanze producono lo stesso stato a ogni passo.
     *
     * @param config Configurazione fisica da copiare nel simulatore.
     * @param seed Seme del regime nuvoloso e del disturbo termico.
     * @throws std::invalid_argument Se la configurazione non e valida.
     */
    EnvironmentSimulator(EnvironmentConfig config, std::uint32_t seed);

    /**
     * @brief Fa avanzare l'ambiente sotto l'effetto degli attuatori.
     *
     * Il campo ActuatorOutput::irrigation_volume_liters_last_step rappresenta
     * una quantita totale gia erogata. Anche i cinque volumi di concentrato
     * sono quantita integrate; la potenza luminosa e un valore continuo.
     *
     * L'ordine di chiamata previsto e:
     * @code
     * actuators.step(delta_time_seconds);
     * environment.step(delta_time_seconds, actuators.output());
     * const auto readings = sensors.read(environment.state());
     * @endcode
     *
     * @param delta_time_seconds Durata positiva del passo, in secondi.
     * @param actuator_output Volumi d'acqua e concentrati erogati nel passo,
     *         piu la potenza delle lampade.
     * @throws std::invalid_argument Se la durata non e finita o positiva, se
     * un ingresso e negativo/non finito, oppure se viene indicato concentrato
     * senza acqua di diluizione.
     * @post EnvironmentState::simulation_time_seconds aumenta esattamente di
     * delta_time_seconds e tutte le grandezze rimangono nei limiti documentati.
     */
    void step(double delta_time_seconds, const ActuatorOutput& actuator_output);

    /**
     * @brief Restituisce lo stato fisico corrente senza modificarlo.
     *
     * @return Riferimento costante valido per la vita del simulatore. Il
     * contenuto osservato cambia dopo ogni successiva chiamata a step().
     */
    const EnvironmentState& state() const noexcept;

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
    /** Masse N, P e K conservate nel terriccio, in milligrammi. */
    std::array<double, 3> nutrient_masses_milligrams_{};
};

/**
 * @brief Restituisce un nome breve e stabile del tipo di terriccio.
 * @param soil_type Terriccio da convertire.
 * @return `"aerated-universal"`, `"draining"` o `"organic-retentive"`;
 * `"unknown"` per un valore enum non riconosciuto. La stringa e statica e non
 * deve essere liberata dal chiamante.
 */
const char* to_string(SoilType soil_type) noexcept;

}  // namespace smarthydro
