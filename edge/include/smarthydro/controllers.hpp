#pragma once

#include <optional>

namespace smarthydro {

/** @brief Direzione con cui l'attuatore influenza la variabile controllata. */
enum class ControlDirection {
    /** L'aumento del comando fa aumentare la variabile di processo. */
    INCREASES_PROCESS_VALUE,
    /** L'aumento del comando fa diminuire la variabile di processo. */
    DECREASES_PROCESS_VALUE,
};

/** @brief Limiti ammessi per l'uscita normalizzata di un controllore. */
struct OutputLimits {
    /** Limite minimo dell'uscita, espresso in percentuale. */
    double minimum_percent = 0.0;
    /** Limite massimo dell'uscita, espresso in percentuale. */
    double maximum_percent = 100.0;
};

/**
 * @brief Controllore a doppia soglia con isteresi.
 *
 * Conserva lo stato nella zona tra le due soglie per evitare commutazioni
 * ripetute in prossimita di un singolo valore limite.
 */
class ThresholdController {
public:
    /**
     * @brief Configura le soglie e i valori di uscita del controllore.
     * @param lower_threshold Soglia inferiore, minore di upper_threshold.
     * @param upper_threshold Soglia superiore.
     * @param direction Effetto dell'attuatore sulla variabile controllata.
     * @param active_output_percent Uscita applicata nello stato attivo.
     * @param inactive_output_percent Uscita applicata nello stato inattivo.
     * @throws std::invalid_argument Se una soglia non e finita, se l'intervallo
     *         non e valido o se un'uscita non appartiene a [0, 100].
     */
    ThresholdController(
        double lower_threshold,
        double upper_threshold,
        ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE,
        double active_output_percent = 100.0,
        double inactive_output_percent = 0.0);

    /**
     * @brief Aggiorna il controllo usando una nuova misura.
     * @param measured_value Valore corrente della variabile controllata.
     * @return Uscita attiva o inattiva, espressa in percentuale.
     * @throws std::invalid_argument Se measured_value non e finito.
     */
    double update(double measured_value);

    /**
     * @brief Reimposta lo stato interno del controllore.
     * @param active Nuovo stato iniziale.
     */
    void reset(bool active = false) noexcept;

    /**
     * @brief Verifica lo stato corrente dell'isteresi.
     * @return true se il controllore e attivo, altrimenti false.
     */
    bool is_active() const noexcept;

private:
    double lower_threshold_;
    double upper_threshold_;
    ControlDirection direction_;
    double active_output_percent_;
    double inactive_output_percent_;
    bool active_ = false;
};

/** @brief Parametri di configurazione di un controllore PID. */
struct PidConfig {
    /** Valore obiettivo della variabile controllata. */
    double setpoint = 0.0;
    /** Guadagno del termine proporzionale. */
    double proportional_gain = 0.0;
    /** Guadagno del termine integrale. */
    double integral_gain = 0.0;
    /** Guadagno del termine derivativo. */
    double derivative_gain = 0.0;
    /** Intervallo ammesso per il comando prodotto. */
    OutputLimits output_limits;
    /** Effetto dell'attuatore sulla variabile controllata. */
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
};

/** @brief Controllore proporzionale-integrale-derivativo con anti-windup. */
class PidController {
public:
    /**
     * @brief Costruisce un controllore PID dalla configurazione indicata.
     * @param config Setpoint, guadagni, limiti e direzione del controllo.
     * @throws std::invalid_argument Se i valori non sono finiti, se un guadagno
     *         e negativo o se i limiti di uscita non sono validi.
     */
    explicit PidController(PidConfig config);

    /**
     * @brief Calcola un nuovo comando PID.
     * @param measured_value Valore corrente della variabile controllata.
     * @param delta_time_seconds Tempo trascorso dall'aggiornamento precedente.
     * @return Comando limitato, espresso in percentuale.
     * @throws std::invalid_argument Se un parametro non e finito o se
     *         delta_time_seconds non e positivo.
     */
    double update(double measured_value, double delta_time_seconds);

    /** @brief Azzera integrale, errore precedente e ultima uscita. */
    void reset() noexcept;

    /**
     * @brief Restituisce l'ultimo comando calcolato.
     * @return Ultima uscita del PID, espressa in percentuale.
     */
    double last_output_percent() const noexcept;

private:
    PidConfig config_;
    double integral_ = 0.0;
    std::optional<double> previous_error_;
    double last_output_percent_ = 0.0;
};

/** @brief Parametri della previsione lineare usata dal controllore predittivo. */
struct PredictiveConfig {
    /** Valore obiettivo della variabile controllata. */
    double setpoint = 0.0;
    /** Numero, anche frazionario, di passi su cui proiettare il trend. */
    double prediction_horizon_steps = 1.0;
    /** Guadagno applicato all'errore previsto. */
    double response_gain = 1.0;
    /** Uscita usata quando l'errore previsto e nullo. */
    double neutral_output_percent = 0.0;
    /** Intervallo ammesso per il comando prodotto. */
    OutputLimits output_limits;
    /** Effetto dell'attuatore sulla variabile controllata. */
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
};

/** @brief Risultati intermedi e uscita di un aggiornamento predittivo. */
struct PredictiveControlResult {
    /** Differenza tra la misura corrente e quella precedente. */
    double measured_trend;
    /** Valore stimato alla fine dell'orizzonte di previsione. */
    double predicted_value;
    /** Comando calcolato e limitato, espresso in percentuale. */
    double output_percent;
};

/** @brief Controllore basato sulla proiezione lineare del trend misurato. */
class PredictiveController {
public:
    /**
     * @brief Costruisce un controllore predittivo.
     * @param config Setpoint, orizzonte, guadagno, uscita neutra e limiti.
     * @throws std::invalid_argument Se la configurazione contiene valori non
     *         finiti, negativi o incompatibili con i limiti di uscita.
     */
    explicit PredictiveController(PredictiveConfig config);

    /**
     * @brief Aggiorna trend, previsione e comando usando una nuova misura.
     * @param measured_value Valore corrente della variabile controllata.
     * @return Trend misurato, valore previsto e uscita percentuale.
     * @throws std::invalid_argument Se measured_value non e finito.
     */
    PredictiveControlResult update(double measured_value);

    /** @brief Dimentica la misura precedente e azzera il trend implicito. */
    void reset() noexcept;

private:
    PredictiveConfig config_;
    std::optional<double> previous_measurement_;
};

}  // namespace smarthydro
