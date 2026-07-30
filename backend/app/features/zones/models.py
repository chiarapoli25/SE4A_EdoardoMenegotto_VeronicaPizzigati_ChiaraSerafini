"""@file models.py
@brief Modelli Pydantic dei reparti e dei settori della serra.
"""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field


class ZoneStatus(str, Enum):
    """@brief Stato del collegamento fra un settore e il relativo Edge."""

    ## @brief Nessun aggiornamento recente e disponibile.
    OFFLINE = "offline"
    ## @brief Il settore comunica regolarmente con il backend.
    ONLINE = "online"


class ZoneCreate(BaseModel):
    """@brief Dati necessari per registrare un settore della serra.

    @details La serra possiede quattro reparti, numerati da 1 a 4. Ogni reparto
    contiene al massimo due settori, numerati da 1 a 2. Un settore ospita una
    sola specie vegetale, rappresentata dal campo scalare `plant_species`.
    """

    ## @brief Identificativo univoco usato negli endpoint HTTP.
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Nome leggibile mostrato nella dashboard.
    name: str = Field(min_length=1, max_length=100)
    ## @brief Numero del reparto fisico, compreso fra 1 e 4.
    department_number: int = Field(ge=1, le=4)
    ## @brief Numero del settore nel reparto, compreso fra 1 e 2.
    sector_number: int = Field(ge=1, le=2)
    ## @brief Unica specie vegetale ospitata nel settore.
    plant_species: str = Field(min_length=1, max_length=100)
    ## @brief Ricetta assegnata al settore, se presente.
    active_recipe_id: str | None = Field(default=None, max_length=64)
    ## @brief Nome della fase di coltivazione corrente, se presente.
    current_phase: str | None = Field(default=None, max_length=100)


class Zone(ZoneCreate):
    """@brief Stato persistente di un settore della serra."""

    ## @brief Stato corrente del collegamento Edge.
    status: ZoneStatus = ZoneStatus.OFFLINE
    ## @brief Timestamp UTC dell'ultimo contatto Edge, oppure `None`.
    last_edge_contact: AwareDatetime | None = None
