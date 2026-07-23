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

/**
 * @brief Limiti del comando di un controllore.
 *
 * L'unita dipende dall'anello di controllo: percentuale, litri, watt o altra
 * grandezza scelta dall'integrazione.
 */
struct CommandLimits {
    /** Limite minimo del comando. */
    double minimum = 0.0;
    /** Limite massimo del comando. */
    double maximum = 100.0;
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
     * @brief Configura soglie e valori di comando del controllore.
     * @param lower_threshold Soglia inferiore, minore di upper_threshold.
     * @param upper_threshold Soglia superiore.
     * @param direction Effetto dell'attuatore sulla variabile controllata.
     * @param active_command Comando applicato nello stato attivo.
     * @param inactive_command Comando applicato nello stato inattivo.
     * @throws std::invalid_argument Se una soglia o un comando non e finito,
     *         oppure se l'intervallo delle soglie non e valido.
     */
    ThresholdController(
        double lower_threshold,
        double upper_threshold,
        ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE,
        double active_command = 100.0,
        double inactive_command = 0.0);

    /**
     * @brief Aggiorna il controllo usando una nuova misura.
     * @param measured_value Valore corrente della variabile controllata.
     * @return Comando attivo o inattivo nell'unita scelta dall'integrazione.
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
    double active_command_;
    double inactive_command_;
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
    CommandLimits command_limits;
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
     *         e negativo o se i limiti del comando non sono validi.
     */
    explicit PidController(PidConfig config);

    /**
     * @brief Calcola un nuovo comando PID.
     * @param measured_value Valore corrente della variabile controllata.
     * @param delta_time_seconds Tempo trascorso dall'aggiornamento precedente.
     * @return Comando limitato nell'unita definita dalla configurazione.
     * @throws std::invalid_argument Se un parametro non e finito o se
     *         delta_time_seconds non e positivo.
     */
    double update(double measured_value, double delta_time_seconds);

    /** @brief Azzera integrale, errore precedente e ultimo comando. */
    void reset() noexcept;

    /**
     * @brief Restituisce l'ultimo comando calcolato.
     * @return Ultimo comando del PID nell'unita configurata.
     */
    double last_command() const noexcept;

private:
    PidConfig config_;
    double integral_ = 0.0;
    std::optional<double> previous_error_;
    double last_command_ = 0.0;
};

/** @brief Parametri della previsione lineare usata dal controllore predittivo. */
struct PredictiveConfig {
    /** Valore obiettivo della variabile controllata. */
    double setpoint = 0.0;
    /** Numero, anche frazionario, di passi su cui proiettare il trend. */
    double prediction_horizon_steps = 1.0;
    /** Guadagno applicato all'errore previsto. */
    double response_gain = 1.0;
    /** Comando usato quando l'errore previsto e nullo. */
    double neutral_command = 0.0;
    /** Intervallo ammesso per il comando prodotto. */
    CommandLimits command_limits;
    /** Effetto dell'attuatore sulla variabile controllata. */
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
};

/** @brief Risultati intermedi e comando di un aggiornamento predittivo. */
struct PredictiveControlResult {
    /** Differenza tra la misura corrente e quella precedente. */
    double measured_trend;
    /** Valore stimato alla fine dell'orizzonte di previsione. */
    double predicted_value;
    /** Comando limitato nell'unita definita dalla configurazione. */
    double command;
};

/** @brief Controllore basato sulla proiezione lineare del trend misurato. */
class PredictiveController {
public:
    /**
     * @brief Costruisce un controllore predittivo.
     * @param config Setpoint, orizzonte, guadagno, comando neutro e limiti.
     * @throws std::invalid_argument Se la configurazione contiene valori non
     *         finiti, negativi o incompatibili con i limiti del comando.
     */
    explicit PredictiveController(PredictiveConfig config);

    /**
     * @brief Aggiorna trend, previsione e comando usando una nuova misura.
     * @param measured_value Valore corrente della variabile controllata.
     * @return Trend misurato, valore previsto e comando limitato.
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
