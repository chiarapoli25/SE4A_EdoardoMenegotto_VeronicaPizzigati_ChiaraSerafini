"""@file
@brief Endpoint delle simulazioni batch e delle riproduzioni live effimere.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.database import get_db
from ..plants.repository import get_plant
from ..recipes.models import Recipe
from ..recipes.repository import get_recipe
from ..zones.models import ZoneAdministrativeStatus
from ..zones.repository import list_zones
from .live_manager import live_simulation_manager
from .manager import (
    SimulationBusy,
    SimulationInvalid,
    SimulationMissing,
    SimulationNotReady,
    simulation_manager,
)
from .models import (
    LiveSimulationControl,
    LiveSimulationCreate,
    LiveSimulationJob,
    LiveSimulationQuarantineUpdate,
    SimulationCreate,
    SimulationJob,
    SimulationPreview,
)


## @brief Router HTTP condiviso da simulazioni batch e live.
router = APIRouter(prefix="/simulations", tags=["simulations"])

# Reparto 5 e' la quarantena (nessuna specie unica, mai simulabile — vedi
# ZoneCreate._validate_department_role); solo i settori produttivi attivi
# con una ricetta assegnata partecipano a "simula l'intera serra" (batch e
# live).
## @brief Reparto escluso da qualunque simulazione produttiva.
_QUARANTINE_DEPARTMENT = 5


def _greenhouse_targets(connection: sqlite3.Connection) -> list[tuple[str, Recipe]]:
    """@brief Seleziona ogni settore produttivo attivo con una ricetta assegnata.

    @details Applica la stessa
    selezione per il batch e per la riproduzione live, vedi commento sopra
    _QUARANTINE_DEPARTMENT. get_recipe puo' restituire None solo se il
    catalogo e il riferimento del settore sono disallineati (mai in
    condizioni normali): quei settori vengono scartati piuttosto che far
    fallire l'intera simulazione per un singolo riferimento orfano."""
    targets = [
        (zone.id, get_recipe(connection, zone.active_recipe_id))
        for zone in list_zones(connection)
        if zone.department_number != _QUARANTINE_DEPARTMENT
        and zone.administrative_status == ZoneAdministrativeStatus.ACTIVE
        and zone.active_recipe_id is not None
    ]
    return [(zone_id, recipe) for zone_id, recipe in targets if recipe is not None]


@router.post("", response_model=SimulationJob, status_code=202)
def create_simulation(
    request: SimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> SimulationJob:
    """@brief Avvia un'anteprima batch per una ricetta o per l'intera serra."""
    try:
        if request.recipe_id is None:
            # "Simula l'intera serra": ogni settore produttivo attivo con
            # una ricetta assegnata, tutti sullo stesso arco temporale.
            return simulation_manager.create_greenhouse(request, _greenhouse_targets(connection))
        recipe = get_recipe(connection, request.recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="recipe not found")
        return simulation_manager.create(request, recipe)
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# --- Riproduzione live dell'intera serra --------------------------------
#
# Route dedicate sotto /simulations/live/..., dichiarate PRIMA di
# /{run_id} qui sotto cosi' un run_id live (mai letterale "live") non ha
# alcuna ambiguita' di matching con le route batch generiche.


@router.post("/live", response_model=LiveSimulationJob, status_code=202)
def create_live_simulation(
    request: LiveSimulationCreate,
    connection: sqlite3.Connection = Depends(get_db),
) -> LiveSimulationJob:
    """@brief Avvia la riproduzione live dell'intera serra produttiva."""
    try:
        return live_simulation_manager.create_greenhouse(request, _greenhouse_targets(connection))
    except SimulationBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/live/current", response_model=LiveSimulationJob | None)
def read_current_live_simulation() -> LiveSimulationJob | None:
    """@brief La riproduzione live attiva nel sistema, se ce n'e' una.

    @details Serve a riagganciare la dashboard a un run gia' in corso
    quando STATE.liveSimulation locale non lo conosce piu' — un reload
    della pagina, una scheda nuova, o il ritorno da un'altra vista dopo
    un crash del tab: il run non si ferma mai da solo (vedi il modulo
    docstring di live_manager) e senza questo endpoint resterebbe visibile
    solo come "un'altra riproduzione live nel sistema" nel 409 di POST
    /live, con "⟲ Reset" incapace di fermarlo perche' il client non ne
    conosce l'id. Dichiarata PRIMA di /live/{run_id} (letterale prima del
    path param, altrimenti Starlette la instraderebbe li' con
    run_id="current") cosi' lo stato del cluster e il pulsante Reset
    tornano corretti non appena si (ri)entra nella pagina Simulatore.
    Nessuna eccezione per "nessun run attivo": e' l'esito normale della
    stragrande maggioranza delle visite alla pagina.
    """
    return live_simulation_manager.current()


@router.get("/live/{run_id}", response_model=LiveSimulationJob)
def read_live_simulation(run_id: str) -> LiveSimulationJob:
    """@brief Restituisce stato e snapshot correnti di una riproduzione live."""
    try:
        return live_simulation_manager.get(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/live/{run_id}/result", response_model=list[SimulationPreview])
def read_live_simulation_result(run_id: str) -> list[SimulationPreview]:
    """@brief Restituisce le serie gia calcolate per la riproduzione live."""
    try:
        return live_simulation_manager.result(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/live/{run_id}/control", response_model=LiveSimulationJob)
def control_live_simulation(run_id: str, request: LiveSimulationControl) -> LiveSimulationJob:
    """@brief Applica play, pausa o una nuova velocita a una riproduzione live."""
    try:
        return live_simulation_manager.control(run_id, request.action, request.speed_multiplier)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/live/{run_id}/quarantine", response_model=LiveSimulationJob)
def update_live_simulation_quarantine(
    run_id: str,
    update: LiveSimulationQuarantineUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> LiveSimulationJob:
    """@brief Sposta o richiama virtualmente una pianta in quarantena.

    @details Solo per questa riproduzione live — non tocca mai la
    quarantena reale della pianta (vedi LiveSimulationQuarantineUpdate).
    La pianta deve esistere ed essere attualmente nel settore indicato
    quando la si mette in quarantena (non ha senso "simulare" lo
    spostamento di una pianta che in realta' non e' li'); nessun vincolo
    di posizione quando la si richiama, cosi' un client puo' sempre
    annullare una propria azione precedente."""
    plant = get_plant(connection, update.plant_id)
    if plant is None:
        raise HTTPException(
            status_code=404, detail=f"plant {update.plant_id!r} not found"
        )
    if update.quarantined and plant.current_zone_id != update.zone_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"plant {update.plant_id!r} is not currently in zone "
                f"{update.zone_id!r}"
            ),
        )
    try:
        return live_simulation_manager.set_simulated_quarantine(
            run_id, update.zone_id, update.plant_id, update.quarantined
        )
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SimulationInvalid as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/live/{run_id}", status_code=204)
def delete_live_simulation(run_id: str) -> Response:
    """@brief Arresta una riproduzione attiva o elimina quella conclusa."""
    try:
        live_simulation_manager.cancel_or_discard(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)


@router.get("/{run_id}", response_model=SimulationJob)
def read_simulation(run_id: str) -> SimulationJob:
    """@brief Restituisce lo stato di un job batch."""
    try:
        return simulation_manager.get(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{run_id}/result", response_model=SimulationPreview | list[SimulationPreview])
def read_simulation_result(run_id: str) -> SimulationPreview | list[SimulationPreview]:
    """@brief Restituisce il risultato batch singolo o multi-zona."""
    try:
        return simulation_manager.result(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SimulationNotReady as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/{run_id}", status_code=204)
def delete_simulation(run_id: str) -> Response:
    """@brief Annulla un job batch attivo o elimina quello concluso."""
    try:
        simulation_manager.cancel_or_discard(run_id)
    except SimulationMissing as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)
