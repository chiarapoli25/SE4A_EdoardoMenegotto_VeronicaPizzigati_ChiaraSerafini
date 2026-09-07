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

## @brief Limiti di comando del PID per le tre variabili non nutritive, nella
## reale unita' fisica del rispettivo attuatore (edge_runtime_actuation.cpp):
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
_PID_COMMAND_LIMITS: dict[ControlledVariable, tuple[float, float]] = {
    ControlledVariable.SOIL_MOISTURE: (0.0, 0.5),
    ControlledVariable.LIGHT: (0.0, 100.0),
    ControlledVariable.PH: (-0.5, 0.5),
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
_INTEGRAL_TIME_SECONDS = 4.0 * 3600.0


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
            (0.0, 3.0) if dose_only else _PID_COMMAND_LIMITS[variable]
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
        # Guadagno integrale non piu' trascurabile: elimina nel tempo il
        # bias stazionario che un P-solo lascia contro un disturbo costante
        # (l'evapotraspirazione per l'umidita', il consumo dei nutrienti per
        # N/P/K) — vedi _INTEGRAL_TIME_SECONDS.
        integral_gain = proportional_gain / _INTEGRAL_TIME_SECONDS
        return PidConfig(
            setpoint=setpoint,
            proportional_gain=proportional_gain,
            integral_gain=integral_gain,
            derivative_gain=0.0,
            command_minimum=command_minimum,
            command_maximum=command_maximum,
            direction=_INCREASES,
        )
    return PredictiveConfig(
        setpoint=setpoint,
        prediction_horizon_steps=1.0,
        response_gain=0.02,
        neutral_command=0.0,
        command_minimum=0.0,
        command_maximum=3.0 if dose_only else 1.0,
        direction=_INCREASES,
        water_dilution_gain=1.0,
        cumulative_dose_gain=0.02,
        substrate_gain=2.0,
    )
