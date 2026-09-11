"""@file parameters.py
@brief Derivazione dei parametri di controllo da Strategy + target di fase.

@details Da quando la Strategy e' un'impostazione globale per variabile
(vedi ../control_strategy/) e non piu' una scelta libera per singola
ricetta, i suoi parametri numerici (guadagni, soglie, limiti di comando) non
sono piu' scritti a mano dall'agronomo — sono sempre ricalcolati da questa
funzione pura, a partire dalla Strategy scelta, dalla variabile e dal target
di fase (setpoint/range, che restano specifici di reparto/specie). Porta in
Python le stesse formule gia' usate da `buildStrategyParameters` in
dashboard/script.js per i comandi live del pannello Controllo — qui
diventano l'unica fonte, riusata sia per lo stamping delle ricette lette
(recipes/repository.py) sia per il comando ChangeStrategy live inviato ai
settori gia' in coltivazione (control_strategy/routes.py).

`catalog.py` non usa questa funzione: le sue formule di seed (in particolare
`_predictive_parameters`, con la sua storia di bug v3/v4 documentata li')
restano un dettaglio del bootstrap iniziale, subito sovrascritto alla prima
lettura della ricetta.
"""

from .models import (
    ControlDirection,
    ControlledVariable,
    ControllerParameters,
    PhaseVariableTarget,
    PidConfig,
    PredictiveConfig,
    StrategyType,
    ThresholdConfig,
    _NUTRIENT_VARIABLES,
)

_INCREASES = ControlDirection.INCREASES_PROCESS_VALUE

## @brief Limiti di comando (PID e Predictive) per le tre variabili non
## nutritive, nella reale unita' fisica del rispettivo attuatore
## (edge_runtime_actuation.cpp):
##  - soil_moisture: litri per singola erogazione della pompa
##    (water_pump_.request_volume_liters) — mai negativa, la pompa puo' solo
##    aggiungere acqua, mai toglierla.
##  - light: percento di potenza dell'impianto (lighting_.set_command_percent,
##    clampato [0,100]) — un livello assoluto continuo, puo' scendere quanto
##    salire: l'unica delle tre con un attuatore davvero bidirezionale sopra
##    lo zero.
##  - ph: mL di correttore, pH+ se il comando e' positivo, pH- se negativo
##    (apply_decisions) — bidirezionale anch'essa, ma su una scala di dose
##    minuscola rispetto a litri/percento.
## Usare 1.0 per tutte e tre, come prima di questo fix, ignorava che
## "1 unita' di errore" significa cose radicalmente diverse (un punto
## percentuale di umidita' contro un grado di potenza luminosa contro un
## decimo di pH): il guadagno proporzionale calcolato sotto usa questi
## limiti insieme alla banda della fase per restare significativo in ogni
## unita', invece di saturare sempre al comando massimo.
_NON_DOSE_COMMAND_LIMITS: dict[ControlledVariable, tuple[float, float]] = {
    ControlledVariable.SOIL_MOISTURE: (0.0, 0.5),
    ControlledVariable.LIGHT: (0.0, 100.0),
    ControlledVariable.PH: (-0.5, 0.5),
}

## @brief Guadagni Predictive specifici di ciascun nutriente (water_dilution_gain,
## substrate_gain), identici a quelli gia' tarati in catalog.py::_build_recipe
## per il seed iniziale — non c'e' ragione per cui la Strategy globale debba
## usare valori diversi da quelli gia' verificati per specie/nutriente.
_NUTRIENT_PREDICTIVE_GAINS: dict[ControlledVariable, tuple[float, float]] = {
    ControlledVariable.NITROGEN: (2.0, 4.0),
    ControlledVariable.PHOSPHORUS: (0.8, 2.0),
    ControlledVariable.POTASSIUM: (2.5, 5.0),
}

## @brief Tempo di integrazione del PID, in secondi: quanto a lungo un errore
## persistente deve restare tale prima che il termine integrale pesi quanto
## quello proporzionale. Un valore troppo piccolo insegue il rumore di ogni
## singolo ciclo di controllo (900 s di norma); uno troppo grande lascia un
## bias stazionario per ore prima di correggerlo (il difetto originale: con
## guadagno integrale pressoche' nullo, la media restava sistematicamente
## sopra il setpoint invece di convergerci, specialmente con un attuatore
## mono-direzionale come la pompa che non puo' mai "tirare giu'" il valore).
## Poche ore e' un compromesso ragionevole per cicli di irrigazione/dosaggio
## che si ripetono tipicamente piu' volte al giorno.
_PID_INTEGRAL_TIME_SECONDS = 4.0 * 3600.0

# La pompa applica il comando come volume discreto di una singola
# irrigazione. Usare l'intero command_max gia' al bordo della banda faceva
# superare il setpoint ad ogni correzione; il PID finiva cosi' in un ciclo
# limite con media stabilmente alta. Un quarto di quella pendenza mantiene
# autorita' sui deficit grandi ma rende fini le correzioni vicino al target.
_SOIL_MOISTURE_PID_RESPONSE_FACTOR = 0.25

## @brief Tempo di integrazione del Predictive per eventuali variabili
## continue non nutritive. Per N/P/K l'integrale viene disabilitato sotto:
## il dosaggio e' gia' un'integrazione fisica di massa persistente.
_PREDICTIVE_INTEGRAL_TIME_SECONDS = 3.0 * 24.0 * 3600.0

# Il comando N/P/K e' una dose di concentrato, non un livello continuo di
# attuatore: anche dopo che la valvola si chiude, la massa erogata resta nel
# substrato. Una dose pari al massimo gia' per un errore grande quanto la
# mezza banda produceva quindi salti di decine di mg/L nei piccoli volumi
# radicali delle succulente. Il fattore rende la correzione progressiva; il
# limite fisico massimo resta disponibile per deficit davvero eccezionali.
_NUTRIENT_DOSE_RESPONSE_FACTOR = 0.10


def _band_half_margin(setpoint: float, target: PhaseVariableTarget | None) -> float:
    """@brief Meta' banda ammessa attorno al setpoint, nell'unita' della variabile.

    @details Stessa idea gia' usata per `response_gain` nel Predictive
    (vedi catalog.py::_predictive_parameters): un errore grande quanto il
    margine della banda deve spingere il comando vicino al suo massimo,
    cosi' il guadagno proporzionale resta significativo sull'intero
    intervallo operativo della variabile invece di essere un numero fisso
    scelto a caso. Non degenere: mai zero, altrimenti il guadagno esploderebbe.
    """
    if target is None:
        return 1.0
    return max(
        target.allowed_range.maximum - setpoint,
        setpoint - target.allowed_range.minimum,
        1e-6,
    )


def default_parameters_for(
    strategy: StrategyType,
    variable: ControlledVariable,
    target: PhaseVariableTarget | None,
) -> ControllerParameters:
    """@brief Calcola i parametri di una Strategy per una variabile e un target.

    @details Una valvola di fertilizzante puo' solo aggiungere, mai
    "ritirare" (vedi la stessa nota in dashboard/script.js): per N/P/K i
    comandi restano percio' su una scala di dose in mL, sempre positiva, non
    sullo stesso ordine di grandezza usato per acqua/luce/pH.

    @param strategy Strategy attualmente scelta per `variable` (impostazione
        globale, vedi control_strategy/).
    @param variable Variabile controllata.
    @param target Target della fase di riferimento (prima fase della
        ricetta), oppure `None` se non disponibile: in tal caso si usa un
        setpoint/range neutro, dato che verra' comunque sovrascritto da
        `parameters_for_phase()` lato Edge ad ogni cambio fase.
    @return Parametri tipizzati coerenti con `strategy`.
    """
    setpoint = target.setpoint if target is not None else 0.0
    minimum = target.allowed_range.minimum if target is not None else 0.0
    maximum = (
        target.allowed_range.maximum
        if target is not None
        else max(setpoint + 1.0, 1.0)
    )
    dose_only = variable in _NUTRIENT_VARIABLES

    if strategy is StrategyType.THRESHOLD:
        return ThresholdConfig(
            lower_threshold=minimum,
            upper_threshold=maximum,
            direction=_INCREASES,
            active_command=2.0 if dose_only else 1.0,
            inactive_command=0.0,
            bidirectional=False,
        )
    if strategy is StrategyType.PID:
        command_minimum, command_maximum = (
            (0.0, 3.0) if dose_only else _NON_DOSE_COMMAND_LIMITS[variable]
        )
        # Guadagno proporzionale scalato sulla banda della fase: un errore
        # grande quanto la banda spinge il comando (quasi) al suo massimo,
        # ma vicino al setpoint il comando si affievolisce di conseguenza —
        # a differenza di un guadagno fisso, che con una variabile dalla
        # banda stretta o dal comando ristretto (es. l'umidita' del
        # terriccio: banda di decine di punti percentuali contro un massimo
        # di 0.5 L per erogazione) restava saturato per qualunque errore non
        # trascurabile, comportandosi come un bang-bang travestito da PID
        # invece di una vera correzione proporzionale.
        proportional_gain = command_maximum / _band_half_margin(setpoint, target)
        if variable is ControlledVariable.SOIL_MOISTURE:
            proportional_gain *= _SOIL_MOISTURE_PID_RESPONSE_FACTOR
        # Guadagno integrale non piu' trascurabile: elimina nel tempo il
        # bias stazionario che un P-solo lascia contro un disturbo costante
        # (l'evapotraspirazione) — vedi _PID_INTEGRAL_TIME_SECONDS.
        integral_gain = proportional_gain / _PID_INTEGRAL_TIME_SECONDS
        return PidConfig(
            setpoint=setpoint,
            proportional_gain=proportional_gain,
            integral_gain=integral_gain,
            derivative_gain=0.0,
            command_minimum=command_minimum,
            command_maximum=command_maximum,
            direction=_INCREASES,
        )
    command_minimum, command_maximum = (
        (0.0, 3.0) if dose_only else _NON_DOSE_COMMAND_LIMITS[variable]
    )
    # response_gain scalato sulla banda della fase, stessa formula e stessa
    # ragione del proportional_gain del PID sopra — qui e' quella che
    # catalog.py::_predictive_parameters usa gia' per il seed iniziale.
    response_gain = command_maximum / _band_half_margin(setpoint, target)
    if dose_only:
        response_gain *= _NUTRIENT_DOSE_RESPONSE_FACTOR
    # water_dilution_gain/substrate_gain hanno senso solo per un dosaggio di
    # fertilizzante (correggono la stima N/P/K per la diluizione data
    # dall'acqua appena irrigata e per il fattore del substrato — vedi
    # PredictiveController::compute in controllers.cpp): per una variabile
    # non nutritiva questi campi non descrivono nulla di fisico, quindi
    # restano a zero invece di riusare per errore un valore tarato per i
    # nutrienti (com'era prima di questo fix, dove /anche/ luce/umidita/pH
    # avrebbero preso un dilution_gain pensato per l'azoto).
    water_dilution_gain, substrate_gain = (
        _NUTRIENT_PREDICTIVE_GAINS[variable] if dose_only else (0.0, 0.0)
    )
    # Per N/P/K la dose e' gia' l'integrale fisico del flusso e la massa
    # somministrata persiste nel terriccio: integrare una seconda volta
    # l'errore del controllore accumula sovradosaggio durante l'attesa tra
    # due irrigazioni. Il feedback predittivo al dosaggio successivo e'
    # sufficiente e produce correzioni molto piu' regolari.
    integral_gain = (
        0.0
        if dose_only
        else response_gain / _PREDICTIVE_INTEGRAL_TIME_SECONDS
    )
    return PredictiveConfig(
        setpoint=setpoint,
        prediction_horizon_steps=1.0,
        response_gain=response_gain,
        neutral_command=0.0,
        command_minimum=command_minimum,
        command_maximum=command_maximum,
        direction=_INCREASES,
        water_dilution_gain=water_dilution_gain,
        # Azzerato deliberatamente: un guadagno positivo incondizionato qui
        # continua a spingere il comando verso l'alto finche' resta dose di
        # fase da erogare, ANCHE quando l'errore e' gia' nullo o negativo —
        # esattamente il bug v4 di catalog.py (vedi la sua nota lunga su
        # _predictive_parameters), che qui era stato reintrodotto usando
        # 0.02 invece di 0.0. E' la ragione principale per cui, prima di
        # questo fix, il valore medio di Azoto/Fosforo/Potassio restava
        # incollato sopra il setpoint invece di convergerci.
        cumulative_dose_gain=0.0,
        substrate_gain=substrate_gain,
        integral_gain=integral_gain,
    )
