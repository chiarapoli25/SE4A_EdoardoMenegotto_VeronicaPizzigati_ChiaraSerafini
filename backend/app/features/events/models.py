"""@file
@brief Contratti HTTP degli eventi Edge.
"""

from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class EdgeEventCreate(BaseModel):
    """@brief Evento idempotente prodotto da un processo Edge."""

    ## @brief Identificatore idempotente dell'evento.
    event_id: str = Field(min_length=1, max_length=128)
    ## @brief Edge che ha prodotto l'evento.
    edge_id: str = Field(min_length=1, max_length=64)
    ## @brief Avvio del processo Edge al quale appartiene la sequenza.
    boot_id: str = Field(min_length=1, max_length=64)
    ## @brief Tipo stabile dell'evento di dominio.
    event_type: str = Field(min_length=1, max_length=64)
    ## @brief Tempo simulato trascorso dall'avvio della zona.
    timestamp_seconds: float = Field(ge=0.0)
    recorded_at: AwareDatetime
    ## @brief Dati diagnostici specifici del tipo di evento.
    payload: dict[str, Any] = Field(default_factory=dict)


class EdgeEvent(EdgeEventCreate):
    """@brief Evento persistito e associato a una zona."""

    zone_id: str
    received_at: AwareDatetime
