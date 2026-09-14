"""@file
@brief Contratti HTTP dei cicli colturali orientati all'agronomo.
"""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field

from ..commands.models import RuntimeCommand


class CultivationState(str, Enum):
    """@brief Stato del ciclo colturale coordinato con l'Edge."""

    ACTIVATING = "activating"  ##< Comando di avvio in attesa di esito.
    RUNNING = "running"  ##< Controllo della ricetta attivo.
    PAUSING = "pausing"  ##< Comando di pausa in attesa di esito.
    PAUSED = "paused"  ##< Ciclo sospeso senza perderne la configurazione.
    RESUMING = "resuming"  ##< Comando di ripresa in attesa di esito.
    STOPPING = "stopping"  ##< Comando di arresto in attesa di esito.
    ERROR = "error"  ##< Ultima transizione rifiutata o fallita.
    ARCHIVED = "archived"  ##< Ciclo terminato e conservato nello storico.


class CultivationCreate(BaseModel):
    """@brief Scelta minima per creare e avviare un ciclo in un solo passaggio."""

    ## @brief Zona produttiva sulla quale avviare il ciclo.
    zone_id: str = Field(min_length=1, max_length=64)
    ## @brief Ricetta compatibile da caricare nell'Edge.
    recipe_id: str = Field(min_length=1, max_length=64)


class Cultivation(BaseModel):
    """@brief Stato persistito e proiettato di un ciclo colturale."""

    ## @brief Identificatore stabile del ciclo.
    id: str
    ## @brief Zona produttiva associata.
    zone_id: str
    ## @brief Specie coltivata, copiata dalla ricetta al momento dell'avvio.
    plant_species: str
    ## @brief Ricetta adottata dal ciclo.
    recipe_id: str
    ## @brief Versione immutabile della ricetta adottata.
    recipe_version: int = Field(ge=1)
    ## @brief Stato corrente della macchina a stati del ciclo.
    state: CultivationState
    ## @brief Indica che l'Edge ha raggiunto l'ultima fase della ricetta.
    recipe_completed: bool = False
    ## @brief Istante di creazione della richiesta.
    created_at: AwareDatetime
    ## @brief Istante di effettivo avvio sull'Edge.
    started_at: AwareDatetime | None = None
    ## @brief Istante di arresto del controllo.
    ended_at: AwareDatetime | None = None
    ## @brief Istante di archiviazione definitiva.
    archived_at: AwareDatetime | None = None
    ## @brief Ultimo comando asincrono associato alla transizione.
    last_command_id: str | None = None


class CultivationAction(BaseModel):
    """@brief Risposta composta da ciclo aggiornato e comando accodato."""

    ## @brief Proiezione del ciclo dopo la richiesta.
    cultivation: Cultivation
    ## @brief Comando che l'Edge deve ancora elaborare.
    command: RuntimeCommand
