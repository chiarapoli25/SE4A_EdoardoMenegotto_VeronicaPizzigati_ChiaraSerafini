"""@file
@brief Contratti HTTP delle piante e del relativo stato di quarantena.
"""

from datetime import datetime, timezone

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class PlantCreate(BaseModel):
    """@brief Pianta registrata inizialmente in un settore produttivo."""

    ## @brief Identificatore stabile dell'esemplare.
    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Specie, che deve coincidere con quella del settore di origine.
    species: str = Field(min_length=1, max_length=100)
    ## @brief Settore produttivo di origine, conservato anche dopo gli spostamenti.
    home_zone_id: str = Field(min_length=1, max_length=64)


class Plant(PlantCreate):
    """@brief Stato corrente persistito di un singolo esemplare."""

    current_zone_id: str
    ## @brief Indica che la pianta e attualmente nel reparto di quarantena.
    is_quarantined: bool = False
    ## @brief Motivazione dell'ultimo ingresso in quarantena.
    quarantine_reason: str | None = None
    ## @brief Istante di inizio della quarantena corrente.
    quarantined_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PlantQuarantineUpdate(BaseModel):
    """@brief Cambio dello stato di quarantena di una pianta."""

    ## @brief `True` per entrare in quarantena, `False` per uscirne.
    is_quarantined: bool
    ## @brief Settore fisico di quarantena, obbligatorio solo in ingresso.
    quarantine_zone_id: str | None = Field(default=None, max_length=64)
    ## @brief Motivazione clinica o agronomica, obbligatoria solo in ingresso.
    reason: str | None = Field(default=None, max_length=500)
    ## @brief Istante (nel passato) da registrare come inizio quarantena.
    #
    # Puro input esplicito, non un modo per aggirare la validazione: serve
    # SOLO a chi popola scenari dimostrativi (vedi demo/seed_dev_data.py) e
    # deve poter mostrare, nel momento in cui la demo parte, una pianta già
    # in quarantena da piu' del periodo minimo previsto dal frontend prima
    # di abilitare "Fai uscire" (QUARANTINE_MIN_RELEASE_MS in
    # dashboard/script.js). Omesso, il comportamento e' quello di sempre:
    # il backend registra l'istante reale della chiamata. Accettato solo
    # quando is_quarantined=True e solo nel passato: non e' un modo per
    # programmare una quarantena futura.
    quarantined_at: AwareDatetime | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_transition(self) -> "PlantQuarantineUpdate":
        """@brief Verifica la coerenza dei campi con la direzione dello spostamento.

        @return Il modello validato.
        @throws ValueError Se destinazione, motivo o timestamp sono incoerenti.
        """
        ## @brief Stato booleano sottoposto alla validazione di transizione.
        if self.is_quarantined:
            if not self.quarantine_zone_id:
                raise ValueError(
                    "quarantine_zone_id is required when quarantining a plant"
                )
            if not self.reason:
                raise ValueError(
                    "reason is required when quarantining a plant"
                )
            if (
                self.quarantined_at is not None
                and self.quarantined_at > datetime.now(timezone.utc)
            ):
                raise ValueError("quarantined_at must not be in the future")
        else:
            if self.quarantine_zone_id is not None:
                raise ValueError(
                    "quarantine_zone_id must be omitted when releasing a plant"
                )
            if self.quarantined_at is not None:
                raise ValueError(
                    "quarantined_at must be omitted when releasing a plant"
                )
        return self


class PlantMovement(BaseModel):
    """@brief Spostamento storico di una pianta fra due settori."""

    ## @brief Progressivo persistente del movimento.
    movement_id: int = Field(ge=1)
    plant_id: str
    from_zone_id: str
    to_zone_id: str
    is_quarantined: bool
    ## @brief Motivo dello spostamento, se disponibile.
    reason: str | None = None
    moved_at: AwareDatetime
