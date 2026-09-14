"""@file
@brief Contratti HTTP della coda comandi Edge.
"""

from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class CommandType(str, Enum):
    """@brief Operazioni serializzabili nella coda destinata all'Edge."""

    CHANGE_STRATEGY = "ChangeStrategy"  ##< Cambia Strategy per una variabile.
    LOAD_RECIPE = "LoadRecipe"  ##< Adotta una ricetta completa.
    ACTIVATE_CULTIVATION = "ActivateCultivation"  ##< Avvia un ciclo colturale.
    PAUSE_CULTIVATION = "PauseCultivation"  ##< Sospende un ciclo colturale.
    RESUME_CULTIVATION = "ResumeCultivation"  ##< Riprende un ciclo sospeso.
    STOP_CULTIVATION = "StopCultivation"  ##< Arresta un ciclo colturale.
    SET_SIMULATION_SPEED = "SetSimulationSpeed"  ##< Cambia la scala temporale.
    SET_SIMULATION_DURATION = "SetSimulationDuration"  ##< Limita la durata simulata.
    CONFIRM_CONFIGURATION = "ConfirmConfiguration"  ##< Conferma una configurazione.
    REJECT_CONFIGURATION = "RejectConfiguration"  ##< Rifiuta una configurazione.
    INJECT_FAULT = "InjectFault"  ##< Inietta un guasto nel simulatore.
    RESET_FAULT = "ResetFault"  ##< Rimuove un guasto simulato.
    ADVANCE_RECIPE_PHASE = "AdvanceRecipePhase"  ##< Avanza manualmente la fase.
    EMERGENCY_STOP = "EmergencyStop"  ##< Porta la zona in arresto di emergenza.
    RESET_EMERGENCY = "ResetEmergency"  ##< Richiede il recupero dall'emergenza.


class CommandStatus(str, Enum):
    """@brief Stato persistito dell'esecuzione di un comando."""

    PENDING = "pending"  ##< In attesa del polling Edge.
    SUCCEEDED = "succeeded"  ##< Eseguito con successo.
    REJECTED = "rejected"  ##< Rifiutato dall'Edge o dal dominio.


class RuntimeCommandCreate(BaseModel):
    """@brief Corpo richiesto per accodare un comando idempotente."""

    ## @brief Identificatore idempotente scelto dal mittente.
    command_id: str = Field(min_length=1, max_length=128)
    ## @brief Operazione richiesta all'Edge.
    command_type: CommandType
    ## @brief Parametri specifici del tipo di comando.
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeCommand(RuntimeCommandCreate):
    """@brief Proiezione persistita di un comando e del relativo esito."""

    ## @brief Zona destinataria.
    zone_id: str
    ## @brief Stato corrente del comando.
    status: CommandStatus
    ## @brief Istante di accodamento.
    created_at: AwareDatetime
    ## @brief Istante dell'esito definitivo, se disponibile.
    completed_at: AwareDatetime | None = None
    ## @brief Diagnostica restituita dall'Edge.
    result_message: str | None = None
    ## @brief Indica che l'Edge ha restituito un risultato dalla cache idempotente.
    result_replayed: bool | None = None


class RuntimeCommandResultCreate(BaseModel):
    """@brief Esito definitivo pubblicato dall'Edge per un comando."""

    ## @brief Stato finale, mai `pending`.
    status: CommandStatus
    ## @brief Messaggio operativo associato all'esito.
    message: str = Field(min_length=1, max_length=1000)
    ## @brief Indica se l'esito proviene dalla cache idempotente dell'Edge.
    replayed: bool = False

    @model_validator(mode="after")
    def _require_final_status(self) -> "RuntimeCommandResultCreate":
        """@brief Impedisce di pubblicare come risultato uno stato non definitivo.

        @return Il modello validato.
        @throws ValueError Se lo stato e ancora `pending`.
        """
        if self.status is CommandStatus.PENDING:
            raise ValueError("a command result cannot remain pending")
        return self
