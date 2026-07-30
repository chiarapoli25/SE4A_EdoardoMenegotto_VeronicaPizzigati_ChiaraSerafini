"""Contratti HTTP della coda comandi Edge."""

from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class CommandType(str, Enum):
    CHANGE_STRATEGY = "ChangeStrategy"
    LOAD_RECIPE = "LoadRecipe"
    ACTIVATE_CULTIVATION = "ActivateCultivation"
    PAUSE_CULTIVATION = "PauseCultivation"
    RESUME_CULTIVATION = "ResumeCultivation"
    STOP_CULTIVATION = "StopCultivation"
    CONFIRM_CONFIGURATION = "ConfirmConfiguration"
    REJECT_CONFIGURATION = "RejectConfiguration"
    INJECT_FAULT = "InjectFault"
    RESET_FAULT = "ResetFault"
    ADVANCE_RECIPE_PHASE = "AdvanceRecipePhase"
    EMERGENCY_STOP = "EmergencyStop"
    RESET_EMERGENCY = "ResetEmergency"


class CommandStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


class RuntimeCommandCreate(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    command_type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeCommand(RuntimeCommandCreate):
    zone_id: str
    status: CommandStatus
    created_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    result_message: str | None = None
    result_replayed: bool | None = None


class RuntimeCommandResultCreate(BaseModel):
    status: CommandStatus
    message: str = Field(min_length=1, max_length=1000)
    replayed: bool = False

    @model_validator(mode="after")
    def _require_final_status(self) -> "RuntimeCommandResultCreate":
        if self.status is CommandStatus.PENDING:
            raise ValueError("a command result cannot remain pending")
        return self
