#pragma once

/**
 * @file controllers.hpp
 * @brief Controllori scalari generici a soglia, PID e predittivi.
 *
 * I controllori non conoscono sensori o attuatori specifici. L'integrazione
 * decide quale misura fornire, quale unita attribuire al comando e a quale
 * attuatore applicarlo.
 */

#include <memory>
#include <optional>
#include <string>
#include <variant>

namespace smarthydro {

/** @brief Strategie selezionabili dall'agronomo. */
enum class StrategyType {
    THRESHOLD,
    PID,
    PREDICTIVE,
};

/**
 * @brief Segno dell'effetto dell'attuatore sulla variabile controllata.
 *
 * Permette allo stesso algoritmo di comandare, per esempio, una pompa che
 * aumenta l'umidita o un correttore che riduce la grandezza osservata.
 */
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
 * grandezza scelta dall'integrazione. I due estremi sono inclusivi e devono
 * essere finiti, con minimum minore o uguale a maximum.
 */
struct CommandLimits {
    /** Limite minimo del comando. */
    double minimum = 0.0;
    /** Limite massimo del comando. */
    double maximum = 100.0;
};

/**
 * @brief Contesto uniforme fornito a qualsiasi strategia.
 *
 * I controllori sensor-based usano measured_value; quelli model-based usano
 * model_estimate. I campi aggiuntivi alimentano la previsione N/P/K.
 */
struct ControllerInput {
    /** Misura fisica per i controlli dotati di sensore. */
    std::optional<double> measured_value;
    /** Stima del modello per una variabile senza sensore. */
    std::optional<double> model_estimate;
    /** Intervallo trascorso dall'ultimo calcolo, in secondi. */
    double delta_time_seconds = 1.0;
    /** Acqua erogata nell'intervallo corrente, in litri. */
    double water_delivered_liters = 0.0;
    /** Dose del prodotto gia erogata nella fase, in millilitri. */
    double cumulative_dose_milliliters = 0.0;
    /** Dose totale suggerita dalla ricetta per la fase, in millilitri. */
    double phase_target_dose_milliliters = 0.0;
    /** Fattore correttivo adimensionale del substrato. */
    double substrate_factor = 1.0;
};

/** @brief Risultato comune prodotto da una strategia. */
struct ControllerResult {
    /** Indica se il calcolo ha prodotto un risultato utilizzabile. */
    bool valid = false;
    /** Comando nell'unita definita dalla configurazione. */
    double command = 0.0;
    /** Valore futuro stimato, presente per il controllo predittivo. */
    std::optional<double> predicted_value;
    /** Diagnostica valorizzata quando valid e false. */
    std::string error;
};

/** @brief Interfaccia Strategy comune a tutti i controllori. */
class IController {
public:
    virtual ~IController() = default;
    /** @brief Restituisce il tipo concreto della strategia. */
    virtual StrategyType strategy_type() const noexcept = 0;
    /** @brief Calcola un comando a partire dal contesto uniforme. */
    virtual ControllerResult compute(const ControllerInput& input) = 0;
    /** @brief Azzera lo stato dinamico accumulato dalla strategia. */
    virtual void reset() noexcept = 0;
};

/** @brief Parametri completi del controllore a soglia. */
struct ThresholdConfig {
    /** Soglia inferiore della banda di isteresi. */
    double lower_threshold = 0.0;
    /** Soglia superiore della banda di isteresi. */
    double upper_threshold = 1.0;
    /** Segno dell'effetto dell'attuatore. */
    ControlDirection direction = ControlDirection::INCREASES_PROCESS_VALUE;
    /** Comando prodotto quando il controllo e attivo. */
    double active_command = 100.0;
    /** Comando prodotto quando il controllo e inattivo. */
    double inactive_command = 0.0;
    /**
     * Se true produce un comando positivo sotto la banda, negativo sopra e
     * zero al suo interno. E usato dal controllo pH.
     */
    bool bidirectional = false;
};

/**
 * @brief Controllore a doppia soglia con isteresi.
 *
 * @details Conserva lo stato nella zona tra le due soglie per evitare
 * commutazioni ripetute in prossimita di un singolo valore limite.
 *
 * Con ControlDirection::INCREASES_PROCESS_VALUE il comando diventa attivo
 * sotto la soglia inferiore e inattivo sopra quella superiore. Con
 * ControlDirection::DECREASES_PROCESS_VALUE avviene il contrario. Valori
 * uguali alle soglie o interni alla banda non cambiano lo stato precedente.
 *
 * Lo stato iniziale e inattivo.
 */
class ThresholdController : public IController {
public:
    /**
     * @brief Configura soglie e valori di comando del controllore.
     *
     * Soglie e comandi possono usare qualsiasi unita coerente con l'anello
     * scelto; il costruttore non impone che active_command sia maggiore di
     * inactive_command.
     *
     * @param lower_threshold Soglia inferiore finita.
     * @param upper_threshold Soglia superiore finita e strettamente maggiore.
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

    /** @brief Costruisce la strategia dalla configurazione serializzabile. */
    explicit ThresholdController(ThresholdConfig config);

    /**
     * @brief Aggiorna il controllo usando una nuova misura.
     *
     * Ogni chiamata puo cambiare lo stato dell'isteresi. Nella banda compresa
     * fra le soglie viene restituito lo stesso comando attivo/inattivo
     * determinato dall'ultimo attraversamento.
     *
     * @param measured_value Valore corrente della variabile controllata.
     * @return Comando attivo o inattivo nell'unita scelta dall'integrazione.
     * @throws std::invalid_argument Se measured_value non e finito.
     */
    double update(double measured_value);

    /** @copydoc IController::strategy_type */
    StrategyType strategy_type() const noexcept override;
    /** @copydoc IController::compute */
    ControllerResult compute(const ControllerInput& input) override;
    /** @copydoc IController::reset */
    void reset() noexcept override;

private:
    double lower_threshold_;
    double upper_threshold_;
    ControlDirection direction_;
    double active_command_;
    double inactive_command_;
    bool active_ = false;
    bool bidirectional_ = false;
};

/**
 * @brief Parametri di configurazione di un controllore PID.
 *
 * I guadagni devono essere non negativi. Le loro unita dipendono dalla misura,
 * dall'unita del comando e dal fatto che il tempo passato a update() e espresso
 * in secondi.
 */
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

/**
 * @brief Controllore proporzionale-integrale-derivativo con anti-windup.
 *
 * @details A ogni aggiornamento calcola l'errore orientato secondo
 * ControlDirection e produce:
 *
 * `u = Kp * errore + Ki * integrale + Kd * derivata`.
 *
 * Al primo campione la derivata vale zero. Il comando viene limitato dentro
 * PidConfig::command_limits. Se la nuova integrazione spingerebbe ulteriormente
 * il comando oltre un limite nella stessa direzione dell'errore, il candidato
 * integrale viene scartato per evitare wind-up.
 */
class PidController : public IController {
public:
    /**
     * @brief Costruisce un controllore PID dalla configurazione indicata.
     *
     * Il controllore parte con integrale nullo e senza un errore precedente;
     * il primo termine derivativo sara quindi nullo.
     *
     * @param config Setpoint, guadagni, limiti e direzione del controllo.
     * @throws std::invalid_argument Se i valori non sono finiti, se un guadagno
     *         e negativo o se i limiti del comando non sono validi.
     */
    explicit PidController(PidConfig config);

    /**
     * @brief Calcola un nuovo comando PID.
     *
     * La durata deve rappresentare il tempo trascorso dall'ultima chiamata,
     * perche scala sia l'accumulo integrale sia la derivata. La funzione
     * aggiorna lo stato interno anche quando il comando viene saturato.
     *
     * @param measured_value Valore corrente della variabile controllata.
     * @param delta_time_seconds Tempo trascorso dall'aggiornamento precedente.
     * @return Comando limitato nell'unita definita dalla configurazione.
     * @throws std::invalid_argument Se un parametro non e finito o se
     *         delta_time_seconds non e positivo.
     */
    double update(double measured_value, double delta_time_seconds);

    /** @copydoc IController::strategy_type */
    StrategyType strategy_type() const noexcept override;
    /** @copydoc IController::compute */
    ControllerResult compute(const ControllerInput& input) override;
    /**
     * @brief Azzera integrale ed errore precedente.
     *
     * Dopo il reset il successivo aggiornamento si comporta come il primo:
     * derivata nulla e integrale ricostruito dal nuovo campione.
     */
    void reset() noexcept override;

private:
    PidConfig config_;
    double integral_ = 0.0;
    std::optional<double> previous_error_;
};

/**
 * @brief Parametri della previsione lineare usata dal controllore predittivo.
 *
 * L'orizzonte e espresso in numero di aggiornamenti, non in secondi. Per avere
 * un significato temporale stabile, il chiamante deve quindi usare una cadenza
 * di campionamento costante.
 */
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
    /** Perdita stimata per litro d'acqua erogata. */
    double water_dilution_gain = 0.0;
    /** Peso della dose ancora da erogare nella fase. */
    double cumulative_dose_gain = 0.0;
    /** Correzione applicata al fattore del substrato. */
    double substrate_gain = 0.0;
};

/**
 * @brief Risultati intermedi e comando di un aggiornamento predittivo.
 *
 * Esporre trend e previsione consente a experiment e diagnostica di spiegare
 * perche il controllore ha prodotto il comando restituito.
 */
struct PredictiveControlResult {
    /** Differenza tra la misura corrente e quella precedente. */
    double measured_trend;
    /** Valore stimato alla fine dell'orizzonte di previsione. */
    double predicted_value;
    /** Comando limitato nell'unita definita dalla configurazione. */
    double command;
};

/**
 * @brief Controllore basato sulla proiezione lineare del trend misurato.
 *
 * @details Il trend e la differenza tra misura corrente e precedente. La
 * previsione e:
 *
 * `misura + trend * prediction_horizon_steps`.
 *
 * Il comando e ottenuto sommando al comando neutro l'errore previsto
 * moltiplicato per response_gain, quindi saturando il risultato nei limiti.
 * Non e un MPC e non usa un modello fisico dell'ambiente.
 */
class PredictiveController : public IController {
public:
    /**
     * @brief Costruisce un controllore predittivo.
     *
     * Non esiste ancora una misura precedente: al primo update() il trend sara
     * assunto pari a zero.
     *
     * @param config Setpoint, orizzonte, guadagno, comando neutro e limiti.
     * @throws std::invalid_argument Se la configurazione contiene valori non
     *         finiti, negativi o incompatibili con i limiti del comando.
     */
    explicit PredictiveController(PredictiveConfig config);

    /**
     * @brief Aggiorna trend, previsione e comando usando una nuova misura.
     *
     * La funzione memorizza measured_value come riferimento per il campione
     * successivo. Non ricevendo una durata, interpreta il trend come variazione
     * per aggiornamento.
     *
     * @param measured_value Valore corrente della variabile controllata.
     * @return Trend misurato, valore previsto e comando limitato.
     * @throws std::invalid_argument Se measured_value non e finito.
     */
    PredictiveControlResult update(double measured_value);

    /** @copydoc IController::strategy_type */
    StrategyType strategy_type() const noexcept override;
    /** @copydoc IController::compute */
    ControllerResult compute(const ControllerInput& input) override;
    /**
     * @brief Dimentica la misura precedente e azzera il trend implicito.
     *
     * Il successivo update() restituira measured_trend uguale a zero.
     */
    void reset() noexcept override;

private:
    PredictiveConfig config_;
    std::optional<double> previous_measurement_;
    // Usato solo da compute(), non da update(): la stima N/P/K arriva da una
    // EC corretta per l'umidita (esponente ~1.3), quindi un piccolo rumore
    // sulla lettura di umidita si amplifica in grandi oscillazioni sulla
    // concentrazione stimata da un ciclo di controllo al successivo. Una
    // differenza grezza a un solo passo (misura(t) - misura(t-1)) scambia
    // quel rumore per un trend reale e lo proietta in avanti, spingendo
    // predicted ben sotto (a volte anche sotto zero) il valore vero appena
    // prima di una finestra di dosaggio valida: vedi PredictiveController::
    // compute() in controllers.cpp per la spiegazione completa.
    std::optional<double> smoothed_measurement_;
};

/** @brief Parametri associati alla strategia selezionata. */
using ControllerParameters =
    std::variant<ThresholdConfig, PidConfig, PredictiveConfig>;

/** @brief Factory del pattern Strategy. */
class ControllerFactory {
public:
    /**
     * @brief Crea il controllore corrispondente a type.
     * @throws std::invalid_argument Se il tipo dei parametri non coincide.
     */
    static std::unique_ptr<IController> create(
        StrategyType type,
        const ControllerParameters& parameters);
};

/** @brief Nomi stabili usati dalla serializzazione JSON. */
const char* to_string(StrategyType type) noexcept;

}  // namespace smarthydro
