#pragma once

/**
 * @file actuator_simulator.hpp
 * @brief Comandi, uscite fisiche e modello degli attuatori simulati.
 */

#include <array>
#include <cstddef>

namespace smarthydro {

/**
 * @brief Liquidi concentrati disponibili nei cinque serbatoi.
 *
 * I valori sono stabili e vengono usati come indici nelle collezioni
 * FertilizerValues. COUNT non identifica un prodotto valido.
 */
enum class FertilizerType : std::size_t {
    NITROGEN = 0,
    PHOSPHORUS,
    POTASSIUM,
    PH_UP,
    PH_DOWN,
    COUNT,
};

/** Numero dei serbatoi di concentrato supportati. */
constexpr std::size_t kFertilizerTypeCount =
    static_cast<std::size_t>(FertilizerType::COUNT);

/** Collezione con un valore per ciascuno dei cinque serbatoi. */
template <typename T>
using FertilizerValues = std::array<T, kFertilizerTypeCount>;

/**
 * @brief Converte un tipo di fertilizzante nel relativo indice.
 * @throws std::invalid_argument Se type non identifica uno dei cinque prodotti.
 */
std::size_t fertilizer_index(FertilizerType type);

/**
 * @brief Restituisce un nome breve e stabile del prodotto.
 * @return `"nitrogen"`, `"phosphorus"`, `"potassium"`, `"ph-up"`,
 * `"ph-down"` oppure `"unknown"`.
 */
const char* to_string(FertilizerType type) noexcept;

/** @brief Limiti fisici degli attuatori installati. */
struct ActuatorConfig {
    /** Portata fissa della pompa ON/OFF, in litri all'ora. */
    double water_pump_flow_liters_per_hour = 2.0;
    /** Volume massimo accettato per una singola irrigazione, in litri. */
    double maximum_irrigation_volume_liters = 5.0;
    /**
     * Portata del concentrato attraverso ciascuna elettrovalvola aperta,
     * in millilitri all'ora. La portata d'acqua non viene ridotta.
     */
    FertilizerValues<double> fertilizer_flow_milliliters_per_hour{
        20.0, 20.0, 20.0, 20.0, 20.0};
    /** Potenza elettrica massima delle lampade, in watt. */
    double maximum_lighting_power_watts = 200.0;
};

/** @brief Richieste logiche prodotte manualmente o da un controllore. */
struct ActuatorCommand {
    /** Dose totale richiesta per l'irrigazione corrente, in litri. */
    double requested_irrigation_volume_liters = 0.0;
    /** Comando ON/OFF delle cinque elettrovalvole dei concentrati. */
    FertilizerValues<bool> fertilizer_valves_open{};
    /** Comando dell'illuminazione, tra 0% e 100%. */
    double lighting_percent = 0.0;
};

/**
 * @brief Stato e uscite fisiche effettivamente erogate dagli attuatori.
 *
 * I campi `*_last_step` sono quantita integrate nell'ultima chiamata a step().
 * Le portate rappresentano invece valori istantanei e diventano nulle quando
 * la pompa comune o la relativa elettrovalvola sono chiuse.
 */
struct ActuatorOutput {
    /** Indica se la pompa comune e accesa. */
    bool water_pump_on = false;
    /** Portata d'acqua corrente, in L/h. */
    double water_pump_flow_liters_per_hour = 0.0;
    /** Acqua erogata nell'ultimo passo, in litri. */
    double irrigation_volume_liters_last_step = 0.0;
    /** Tempo effettivo di pompaggio nell'ultimo passo, in secondi. */
    double water_pump_on_time_seconds_last_step = 0.0;
    /** Acqua ancora da erogare, in litri. */
    double remaining_irrigation_volume_liters = 0.0;

    /** Stato fisico delle cinque elettrovalvole. */
    FertilizerValues<bool> fertilizer_valves_open{};
    /** Portata corrente per prodotto, in mL/h. */
    FertilizerValues<double> fertilizer_flow_milliliters_per_hour{};
    /** Volume realmente erogato per prodotto nell'ultimo passo, in mL. */
    FertilizerValues<double> fertilizer_volume_milliliters_last_step{};

    /** Potenza elettrica corrente delle lampade, in watt. */
    double lighting_power_watts = 0.0;
};

/**
 * @brief Simula una pompa ON/OFF condivisa da acqua e cinque concentrati.
 *
 * La pompa eroga la dose d'acqua richiesta alla portata configurata. Le
 * elettrovalvole possono essere aperte solo mentre l'irrigazione e attiva e
 * aggiungono piccole portate di concentrato senza ridurre quella dell'acqua.
 * N, P e K possono fluire insieme; PH_UP e PH_DOWN sono interbloccati.
 */
class ActuatorSimulator {
public:
    /**
     * @brief Costruisce tutti gli attuatori nello stato sicuro.
     * @throws std::invalid_argument Se una portata o un limite non e positivo.
     */
    explicit ActuatorSimulator(ActuatorConfig config = {});

    /** @brief Restituisce i comandi logici correnti. */
    const ActuatorCommand& command() const noexcept;
    /** @brief Restituisce le uscite fisiche correnti. */
    const ActuatorOutput& output() const noexcept;
    /** @brief Restituisce la configurazione fisica validata. */
    const ActuatorConfig& config() const noexcept;

    /**
     * @brief Avvia l'erogazione della dose d'acqua indicata.
     * @throws std::invalid_argument Se la dose non e valida.
     * @throws std::logic_error Se un'irrigazione e gia attiva.
     */
    void request_irrigation_volume_liters(double volume_liters);

    /**
     * @brief Annulla l'irrigazione e chiude tutte le elettrovalvole.
     */
    void cancel_irrigation() noexcept;

    /** @brief Restituisce i secondi necessari a completare la dose d'acqua. */
    double remaining_irrigation_time_seconds() const noexcept;

    /**
     * @brief Apre o chiude l'elettrovalvola del prodotto indicato.
     *
     * L'apertura richiede un'irrigazione attiva. PH_UP e PH_DOWN non possono
     * essere aperti contemporaneamente. La chiusura e sempre consentita.
     *
     * @throws std::invalid_argument Se type non e valido.
     * @throws std::logic_error Se si tenta un'apertura senza pompa attiva o
     * in conflitto con il correttore di pH opposto.
     */
    void set_fertilizer_valve_open(FertilizerType type, bool open);

    /** @brief Chiude simultaneamente tutte le elettrovalvole. */
    void close_all_fertilizer_valves() noexcept;

    /** @brief Restituisce il comando logico della valvola indicata. */
    bool fertilizer_valve_command(FertilizerType type) const;
    /** @brief Restituisce lo stato fisico della valvola indicata. */
    bool fertilizer_valve_open(FertilizerType type) const;
    /** @brief Restituisce la portata corrente del prodotto, in mL/h. */
    double fertilizer_flow_milliliters_per_hour(FertilizerType type) const;
    /** @brief Restituisce il volume erogato nell'ultimo passo, in mL. */
    double fertilizer_volume_milliliters_last_step(FertilizerType type) const;

    /** @brief Imposta il comando percentuale delle lampade. */
    void set_lighting_command_percent(double value);

    /**
     * @brief Integra acqua e concentrati per la durata effettiva della pompa.
     *
     * Se l'irrigazione termina prima di delta_time_seconds, i volumi dei
     * concentrati vengono integrati solo fino a quell'istante. Al termine
     * tutte le elettrovalvole vengono riportate nello stato sicuro chiuso.
     */
    void step(double delta_time_seconds);

    /** @brief Azzera comandi, uscite e volumi integrati. */
    void stop_all() noexcept;

private:
    void close_fertilizer_valves_preserving_last_step() noexcept;

    ActuatorConfig config_;
    ActuatorCommand command_;
    ActuatorOutput output_;
};

}  // namespace smarthydro
