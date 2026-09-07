"""@file routes.py
@brief Endpoint della Strategy di controllo a livello di impianto.

@details Sostituisce quella che era una scelta per singola ricetta:
Threshold/PID/Predictive e' ora un'unica impostazione per variabile, valida
per l'intero impianto (vedi recipes/repository.py, dove diventa autorevole
su ogni ricetta letta — comprese quelle gia' assegnate a un settore o usate
dal Simulatore). Solo l'Amministratore puo' cambiarla; ogni cambio si
propaga subito, live, a ogni settore reparto 1-4 con una coltivazione in
corso, con lo stesso comando ChangeStrategy+ConfirmConfiguration che il
pannello Controllo inviava gia' dal browser — qui lato server, una volta
sola per il cambio globale invece che ad ogni apertura della pagina.
"""

import sqlite3
import time

from fastapi import APIRouter, Depends

from ...core.database import get_db
from ..commands.models import CommandType, RuntimeCommandCreate
from ..commands.repository import RuntimeCommandConflict, create_command
from ..recipes.models import ControlledVariable, StrategyType
from ..recipes.parameters import default_parameters_for
from ..recipes.repository import get_recipe
from ..users.dependencies import get_current_user, require_admin
from ..zones.models import Zone
from ..zones.repository import list_zones
from .models import ControlStrategySetting, ControlStrategyUpdate
from .repository import get_all, set_strategy

router = APIRouter(prefix="/control-strategy", tags=["control-strategy"])

## @brief Reparti produttivi (5 e' la quarantena, mai un bersaglio).
_PRODUCTION_DEPARTMENTS = range(1, 5)


def _active_production_zones(connection: sqlite3.Connection) -> list[Zone]:
    """@brief Settori reparto 1-4 con una coltivazione in corso.

    @details Lo stesso bersaglio che il vecchio pannello Controllo calcolava
    lato client (`activeProductionZones()` in dashboard/script.js) — un
    settore con solo una ricetta assegnata ma nessuna coltivazione attiva,
    o un settore in quarantena, non riceve mai il comando live: prendera'
    comunque la Strategy globale corrente al prossimo caricamento ricetta.
    """
    return [
        zone
        for zone in list_zones(connection)
        if zone.department_number in _PRODUCTION_DEPARTMENTS
        and zone.active_cultivation_id is not None
    ]


def _push_live_strategy_change(
    connection: sqlite3.Connection,
    zone: Zone,
    variable: ControlledVariable,
    strategy: StrategyType,
) -> None:
    """@brief Invia a un settore gia' in coltivazione il cambio live.

    @details `parameters` qui non e' che un punto di partenza: al prossimo
    ricalcolo controllori l'Edge sovrascrive comunque setpoint/soglie dal
    target della fase corrente (`parameters_for_phase()` in
    control_system.cpp), quindi basta derivarli dal target di fase noto al
    backend in questo istante, senza bisogno di ulteriori garanzie di
    freschezza.
    """
    recipe = (
        get_recipe(connection, zone.active_recipe_id)
        if zone.active_recipe_id is not None
        else None
    )
    target = None
    if recipe is not None:
        phase = next(
            (p for p in recipe.phases if p.name == zone.current_phase), None
        )
        if phase is not None:
            target = next(
                (t for t in phase.targets if t.variable is variable), None
            )
    parameters = default_parameters_for(strategy, variable, target)
    command_base = f"global-strategy-{zone.id}-{variable.value}-{time.time_ns()}"
    try:
        create_command(
            connection,
            zone.id,
            RuntimeCommandCreate(
                command_id=command_base,
                command_type=CommandType.CHANGE_STRATEGY,
                payload={
                    "variable": variable.value,
                    "strategy": strategy.value,
                    "parameters": parameters.model_dump(),
                },
            ),
        )
        create_command(
            connection,
            zone.id,
            RuntimeCommandCreate(
                command_id=command_base + "-confirm",
                command_type=CommandType.CONFIRM_CONFIGURATION,
                payload={"variable": variable.value},
            ),
        )
    except RuntimeCommandConflict:
        # Un command_id univoco per timestamp non dovrebbe mai collidere;
        # se succede, il cambio globale resta comunque persistito e il
        # settore lo riceve al prossimo cambio di fase/ricetta.
        pass


@router.get("", response_model=list[ControlStrategySetting])
def read_control_strategy(
    connection: sqlite3.Connection = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[ControlStrategySetting]:
    settings = get_all(connection)
    return [
        ControlStrategySetting(variable=variable, selected_strategy=strategy)
        for variable, strategy in settings.items()
    ]


@router.put("/{variable}", response_model=ControlStrategySetting)
def update_control_strategy(
    variable: ControlledVariable,
    update: ControlStrategyUpdate,
    connection: sqlite3.Connection = Depends(get_db),
    _user=Depends(require_admin),
) -> ControlStrategySetting:
    set_strategy(connection, variable, update.selected_strategy)
    for zone in _active_production_zones(connection):
        _push_live_strategy_change(
            connection, zone, variable, update.selected_strategy
        )
    return ControlStrategySetting(
        variable=variable, selected_strategy=update.selected_strategy
    )
