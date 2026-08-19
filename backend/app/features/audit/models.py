"""@file models.py
@brief Modelli Pydantic del log di audit.
"""

from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class AuditOutcome(str, Enum):
    """@brief Esito registrato per una voce di audit."""

    ## @brief L'operazione e' stata eseguita con successo.
    SUCCESS = "success"
    ## @brief L'operazione e' stata rifiutata o e' fallita.
    FAILURE = "failure"


class AuditLogEntry(BaseModel):
    """@brief Voce persistita del log di audit.

    @details Un'unica tabella per tutte le feature: `action` e una stringa
    libera con la convenzione `<dominio>.<verbo>` (es. `auth.login`,
    `cultivation.confirm`), cosi da poter aggiungere nuove azioni senza
    toccare lo schema. `resource_type`/`resource_id` identificano l'oggetto
    coinvolto quando pertinente (es. `cultivation`/`cult-1`); `detail`
    conserva contesto aggiuntivo non strutturato (es. lo `zone_id`, il
    motivo di un rifiuto).
    """

    ## @brief Identificativo progressivo della voce.
    id: int
    ## @brief Timestamp UTC dell'evento.
    occurred_at: AwareDatetime
    ## @brief Utente autenticato che ha eseguito l'azione, se noto.
    actor_username: str | None = None
    ## @brief Ruolo dell'utente al momento dell'azione, se noto.
    actor_role: str | None = None
    ## @brief Azione eseguita, nella forma `<dominio>.<verbo>`.
    action: str = Field(min_length=1, max_length=100)
    ## @brief Esito dell'operazione.
    outcome: AuditOutcome
    ## @brief Tipo dell'oggetto coinvolto, se pertinente (es. `cultivation`).
    resource_type: str | None = None
    ## @brief Identificativo dell'oggetto coinvolto, se pertinente.
    resource_id: str | None = None
    ## @brief Contesto aggiuntivo libero (es. `zone_id`, motivo del rifiuto).
    detail: dict[str, Any] | None = None
