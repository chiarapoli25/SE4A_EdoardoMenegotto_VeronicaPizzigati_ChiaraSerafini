"""Contratti HTTP delle piante e del relativo stato di quarantena."""

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class PlantCreate(BaseModel):
    """Pianta registrata inizialmente in un settore produttivo."""

    id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    species: str = Field(min_length=1, max_length=100)
    home_zone_id: str = Field(min_length=1, max_length=64)


class Plant(PlantCreate):
    """Stato corrente persistito di un singolo esemplare."""

    current_zone_id: str
    is_quarantined: bool = False
    quarantine_reason: str | None = None
    quarantined_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PlantQuarantineUpdate(BaseModel):
    """Cambio dello stato di quarantena di una pianta."""

    is_quarantined: bool
    quarantine_zone_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_transition(self) -> "PlantQuarantineUpdate":
        if self.is_quarantined:
            if not self.quarantine_zone_id:
                raise ValueError(
                    "quarantine_zone_id is required when quarantining a plant"
                )
            if not self.reason:
                raise ValueError(
                    "reason is required when quarantining a plant"
                )
        elif self.quarantine_zone_id is not None:
            raise ValueError(
                "quarantine_zone_id must be omitted when releasing a plant"
            )
        return self


class PlantMovement(BaseModel):
    """Spostamento storico di una pianta fra due settori."""

    movement_id: int = Field(ge=1)
    plant_id: str
    from_zone_id: str
    to_zone_id: str
    is_quarantined: bool
    reason: str | None = None
    moved_at: AwareDatetime
