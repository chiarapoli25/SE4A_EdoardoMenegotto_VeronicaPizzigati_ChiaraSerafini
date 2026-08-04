"""@file models.py
@brief Modelli Pydantic dei reparti e dei settori della serra.
"""

from enum import Enum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from ...greenhouse_layout import department_name


class ZoneStatus(str, Enum):
    """@brief Stato del collegamento fra un settore e il relativo Edge."""

    ## @brief Nessun aggiornamento recente e disponibile.
    OFFLINE = "offline"
    ## @brief Il settore comunica regolarmente con il backend.
    ONLINE = "online"


class ZoneAdministrativeStatus(str, Enum):
    """@brief Disponibilita amministrativa di un settore."""

    ## @brief Settore disponibile per il normale utilizzo.
    ACTIVE = "active"
    ## @brief Settore disabilitato dall'amministratore.
    INACTIVE = "inactive"
    ## @brief Settore temporaneamente riservato alla manutenzione.
    MAINTENANCE = "maintenance"


class ZoneCreate(BaseModel):
    """@brief Dati necessari per registrare un settore della serra.

    @details La serra possiede quattro reparti produttivi, numerati da 1 a 4,
    e il reparto 5 destinato alle piante in quarantena. Ogni reparto contiene
    al massimo due settori, numerati da 1 a 2. I settori produttivi ospitano
    una sola specie; quelli del quinto reparto sono misti e non dichiarano
    `plant_species`. Lo stato di quarantena appartiene alla singola pianta.
    """

    ## @brief Identificativo univoco usato negli endpoint HTTP.
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Nome leggibile mostrato nella dashboard.
    name: str = Field(min_length=1, max_length=100)
    ## @brief Numero del reparto fisico: 1-4 produttivi, 5 quarantena.
    department_number: int = Field(ge=1, le=5)
    ## @brief Numero del settore nel reparto, compreso fra 1 e 2.
    sector_number: int = Field(ge=1, le=2)
    ## @brief Specie unica del settore produttivo; assente in quarantena.
    plant_species: str | None = Field(default=None, min_length=1, max_length=100)
    ## @brief Edge incaricato di gestire fisicamente il settore.
    assigned_edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Ricetta assegnata al settore, se presente.
    active_recipe_id: str | None = Field(default=None, max_length=64)
    ## @brief Nome della fase di coltivazione corrente, se presente.
    current_phase: str | None = Field(default=None, max_length=100)
    ## @brief Disponibilita configurata dall'amministratore.
    administrative_status: ZoneAdministrativeStatus = (
        ZoneAdministrativeStatus.ACTIVE
    )

    @computed_field
    @property
    def department_name(self) -> str:
        """Nome canonico del reparto fisico mostrato ai client."""
        return department_name(self.department_number)

    @model_validator(mode="after")
    def _validate_department_role(self) -> "ZoneCreate":
        """Mantiene coerenti reparto, funzione e specie vegetale."""
        if self.department_number == 5:
            if self.plant_species is not None:
                raise ValueError(
                    "a zone in department 5 cannot have one plant_species"
                )
        else:
            if self.plant_species is None:
                raise ValueError(
                    "a cultivation zone requires plant_species"
                )
        return self


class Zone(ZoneCreate):
    """@brief Stato persistente di un settore della serra."""

    ## @brief Stato corrente del collegamento Edge.
    status: ZoneStatus = ZoneStatus.OFFLINE
    ## @brief Timestamp UTC dell'ultimo contatto Edge, oppure `None`.
    last_edge_contact: AwareDatetime | None = None


class ZoneUpdate(BaseModel):
    """@brief Modifiche parziali ammesse per un settore esistente.

    @details Posizione fisica e identificativo non sono modificabili. I campi
    nullable distinguono il valore JSON `null` da un campo omesso tramite
    `model_fields_set`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    plant_species: str | None = Field(default=None, min_length=1, max_length=100)
    assigned_edge_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    active_recipe_id: str | None = Field(default=None, max_length=64)
    administrative_status: ZoneAdministrativeStatus | None = None

    @model_validator(mode="after")
    def _reject_null_required_fields(self) -> "ZoneUpdate":
        """Impedisce di azzerare campi che devono sempre avere un valore."""
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if (
            "administrative_status" in self.model_fields_set
            and self.administrative_status is None
        ):
            raise ValueError("administrative_status cannot be null")
        return self
