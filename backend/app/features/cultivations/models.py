"""@file models.py
@brief Modelli Pydantic della feature Cultivations.

@details Una coltivazione descrive l'occupazione di un settore da parte di
una specie e conserva il legame con la ricetta selezionata. E' separata dalla
zona per conservarne lo storico e per impedire che una nuova assegnazione
cancelli le informazioni precedenti: un settore puo avere molte coltivazioni
storiche, ma una sola coltivazione non conclusa alla volta (vincolo applicato
anche a livello di persistenza in `core.database`).

Tutti gli endpoint HTTP sono annidati sotto `/zones/{zone_id}/cultivations`:
il settore identifica sempre lo scope della richiesta e non compare piu nel
corpo di `CultivationCreate` (lo stesso pattern gia usato da
`features.commands.RuntimeCommandCreate`/`RuntimeCommand`).

Il ciclo di vita e composto da quattro contratti distinti:

- `CultivationCreate` (bozza): l'agronomo assegna una specie e la ricetta
  desiderata per il settore indicato nel path, senza ancora impegnare l'Edge.
- `CultivationConfirm` (conferma): l'agronomo richiede l'attivazione. La
  richiesta e ammessa solo se settore, specie, ricetta e substrato sono
  compatibili; la versione della ricetta viene fissata in questo momento e
  non cambia piu automaticamente, nemmeno se viene pubblicata una versione
  successiva. La coltivazione passa allo stato `starting` e la conferma
  accoda un comando `ActivateCultivation` sulla stessa coda usata dalle altre
  feature Edge (`features.commands`), con la ricetta pinnata incorporata nel
  payload.
- `CultivationActivationResult` (risposta): esito dell'attivazione,
  ricostruito internamente quando l'Edge riporta il risultato del comando
  `ActivateCultivation` tramite `POST /zones/{zone_id}/commands/{id}/result`.
  La coltivazione passa allo stato `active` solo dopo un esito positivo; un
  fallimento conserva il motivo in `error_message` e lascia lo stato
  `failed`, senza che gli attuatori vengano mai comandati da questo modulo.
- `Cultivation` (stato): rappresentazione completa persistita, usata sia
  dalla dashboard sia dagli altri servizi.
"""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class CultivationStatus(str, Enum):
    """@brief Stato del ciclo di vita di una coltivazione."""

    ## @brief Assegnazione creata ma non ancora confermata.
    DRAFT = "draft"
    ## @brief Confermata dall'agronomo, in attesa dell'esito dell'Edge.
    STARTING = "starting"
    ## @brief Attivata con successo: la ricetta e in esecuzione sul settore.
    ACTIVE = "active"
    ## @brief Sospesa temporaneamente senza essere conclusa.
    PAUSED = "paused"
    ## @brief Conclusa regolarmente. Libera il settore per una nuova bozza.
    COMPLETED = "completed"
    ## @brief Attivazione fallita. Libera il settore per una nuova bozza.
    FAILED = "failed"


## @brief Stati che non occupano piu in modo esclusivo il settore.
CONCLUDED_CULTIVATION_STATUSES = frozenset(
    {CultivationStatus.COMPLETED, CultivationStatus.FAILED}
)


class CultivationCreate(BaseModel):
    """@brief Dati necessari per aprire una bozza di coltivazione.

    @details Il settore non compare qui: arriva dal path
    `/zones/{zone_id}/cultivations` della richiesta HTTP, esattamente come
    per `RuntimeCommandCreate`.
    """

    ## @brief Identificativo univoco usato negli endpoint HTTP.
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Specie vegetale coltivata in questo ciclo.
    plant_species: str = Field(min_length=1, max_length=100)
    ## @brief Ricetta desiderata per il ciclo di coltivazione.
    recipe_id: str = Field(min_length=1)
    ## @brief Versione della ricetta richiesta; fissata definitivamente alla conferma.
    recipe_version: int = Field(ge=1)
    ## @brief Fattore di accelerazione della simulazione richiesto dall'agronomo.
    requested_time_scale: float = Field(default=1.0, gt=0.0)
    ## @brief Autore della bozza, se noto.
    created_by: str | None = Field(default=None, max_length=100)


class CultivationConfirm(BaseModel):
    """@brief Richiesta di conferma e avvio dell'attivazione su Edge.

    @details Non richiede dati propri: la conferma opera sulla bozza gia
    salvata e verifica la compatibilita fra settore, specie, ricetta e
    substrato prima di fissare la versione della ricetta. E' un modello a se
    stante, distinto da `CultivationCreate`, per mantenere il contratto HTTP
    esplicito e permettere future estensioni senza toccare la bozza.
    """


class CultivationActivationResult(BaseModel):
    """@brief Esito riportato dall'Edge dopo un tentativo di attivazione."""

    ## @brief `True` se l'Edge ha applicato la ricetta e avviato la coltivazione.
    success: bool
    ## @brief Fattore di accelerazione realmente applicato in caso di successo.
    applied_time_scale: float | None = Field(default=None, gt=0.0)
    ## @brief Fase corrente riportata nel risultato strutturato, se presente.
    current_phase: str | None = Field(default=None, min_length=1, max_length=100)
    ## @brief Motivo del fallimento; obbligatorio quando `success` e `False`.
    error_message: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _check_error_message_consistency(self) -> "CultivationActivationResult":
        """@brief Impone la coerenza fra esito e messaggio d'errore.

        @return Istanza validata senza modifiche.
        @throws ValueError Se un fallimento non riporta un motivo, o se un
            successo riporta comunque un messaggio d'errore.
        """
        if not self.success and not self.error_message:
            raise ValueError(
                "error_message is required when the activation fails")
        if self.success and self.error_message:
            raise ValueError(
                "error_message must be empty when the activation succeeds")
        return self


class CultivationProgress(BaseModel):
    """@brief Avanzamento simulato riportato durante pausa o conclusione."""

    ## @brief Secondi di simulazione trascorsi dall'avvio, se noti al chiamante.
    elapsed_simulation_seconds: float | None = Field(default=None, ge=0.0)


class CultivationResumeRequest(BaseModel):
    """@brief Richiesta di ripresa, con velocita opzionale da riapplicare.

    @details Corrisponde al comando Edge `ResumeCultivation(cultivation_id,
    time_scale?)`: se omessa, l'Edge riprende con l'ultima velocita
    applicata.
    """

    ## @brief Nuova velocita richiesta alla ripresa, oppure `None` per mantenere l'ultima applicata.
    time_scale: float | None = Field(default=None, gt=0.0)


class CultivationStopRequest(CultivationProgress):
    """@brief Richiesta di conclusione, con il motivo dell'arresto.

    @details Corrisponde al comando Edge `StopCultivation(cultivation_id,
    reason)`: a differenza della pausa, la conclusione e definitiva e libera
    il settore, quindi il motivo viene conservato per l'audit.
    """

    ## @brief Motivo dell'arresto, riportato all'Edge e conservato per l'audit.
    reason: str | None = Field(default=None, max_length=500)


class Cultivation(CultivationCreate):
    """@brief Stato persistente completo di una coltivazione."""

    ## @brief Settore fisico occupato dalla coltivazione.
    zone_id: str = Field(min_length=1, max_length=64)
    ## @brief Stato corrente del ciclo di vita.
    status: CultivationStatus = CultivationStatus.DRAFT
    ## @brief Timestamp UTC di creazione della bozza.
    created_at: AwareDatetime
    ## @brief Timestamp UTC della conferma (transizione a `starting`), oppure `None`.
    confirmed_at: AwareDatetime | None = None
    ## @brief Timestamp UTC dell'attivazione riuscita, oppure `None`.
    started_at: AwareDatetime | None = None
    ## @brief Timestamp UTC della conclusione o del fallimento, oppure `None`.
    completed_at: AwareDatetime | None = None
    ## @brief Secondi di simulazione accumulati dall'attivazione.
    elapsed_simulation_seconds: float = Field(default=0.0, ge=0.0)
    ## @brief Fattore di accelerazione realmente applicato dall'Edge.
    applied_time_scale: float | None = None
    ## @brief Fase corrente della ricetta, aggiornata da `RecipePhaseChanged`
    ## e dai risultati strutturati riportati dall'Edge.
    current_phase: str | None = Field(default=None, max_length=100)
    ## @brief Motivo dell'ultimo fallimento di attivazione, se presente.
    error_message: str | None = None
    ## @brief Comando `ActivateCultivation` accodato alla conferma, se presente.
    activation_command_id: str | None = None
