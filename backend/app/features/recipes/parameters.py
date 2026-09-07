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
        if dose_only:
            return PidConfig(
                setpoint=setpoint,
                proportional_gain=0.05,
                integral_gain=0.0001,
                derivative_gain=0.0,
                command_minimum=0.0,
                command_maximum=3.0,
                direction=_INCREASES,
            )
        return PidConfig(
            setpoint=setpoint,
            proportional_gain=1.0,
            integral_gain=0.00001,
            derivative_gain=0.0,
            command_minimum=-0.5,
            command_maximum=0.5,
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
