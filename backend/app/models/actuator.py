"""@file actuator.py
@brief Modelli Pydantic dei comandi e delle uscite fisiche degli attuatori.

@details I modelli rispecchiano `ActuatorCommand` e `ActuatorOutput` del
simulatore C++. Il comando richiesto e mantenuto distinto dall'uscita
effettivamente erogata.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class FertilizerValveStates(BaseModel):
    """@brief Stato delle cinque elettrovalvole dei concentrati."""

    ## @brief Consente sia i nomi Python sia gli alias stabili del C++.
    model_config = ConfigDict(populate_by_name=True)

    ## @brief Stato della valvola dell'azoto.
    nitrogen: bool
    ## @brief Stato della valvola del fosforo.
    phosphorus: bool
    ## @brief Stato della valvola del potassio.
    potassium: bool
    ## @brief Stato della valvola del correttore pH+.
    ph_up: bool = Field(alias="ph-up")
    ## @brief Stato della valvola del correttore pH-.
    ph_down: bool = Field(alias="ph-down")


class FertilizerQuantities(BaseModel):
    """@brief Valori non negativi associati ai cinque concentrati."""

    ## @brief Consente sia i nomi Python sia gli alias stabili del C++.
    model_config = ConfigDict(populate_by_name=True)

    ## @brief Valore relativo all'azoto.
    nitrogen: float = Field(ge=0.0)
    ## @brief Valore relativo al fosforo.
    phosphorus: float = Field(ge=0.0)
    ## @brief Valore relativo al potassio.
    potassium: float = Field(ge=0.0)
    ## @brief Valore relativo al correttore pH+.
    ph_up: float = Field(ge=0.0, alias="ph-up")
    ## @brief Valore relativo al correttore pH-.
    ph_down: float = Field(ge=0.0, alias="ph-down")


class ActuatorCommandState(BaseModel):
    """@brief Comandi logici richiesti dal controllore."""

    ## @brief Volume totale richiesto per l'irrigazione corrente, in litri.
    requested_irrigation_volume_liters: float = Field(ge=0.0)
    ## @brief Comando di apertura delle cinque elettrovalvole.
    fertilizer_valves_open: FertilizerValveStates
    ## @brief Comando delle lampade, in percentuale.
    lighting_percent: float = Field(ge=0.0, le=100.0)


class ActuatorPhysicalOutput(BaseModel):
    """@brief Stato e uscite fisiche effettive degli attuatori."""

    ## @brief Indica se la pompa comune e fisicamente accesa.
    water_pump_on: bool
    ## @brief Portata corrente della pompa, in litri all'ora.
    water_pump_flow_liters_per_hour: float = Field(ge=0.0)
    ## @brief Acqua erogata nell'ultimo passo, in litri.
    irrigation_volume_liters_last_step: float = Field(ge=0.0)
    ## @brief Tempo di pompaggio nell'ultimo passo, in secondi.
    water_pump_on_time_seconds_last_step: float = Field(ge=0.0)
    ## @brief Volume d'acqua ancora da erogare, in litri.
    remaining_irrigation_volume_liters: float = Field(ge=0.0)
    ## @brief Stato fisico delle cinque elettrovalvole.
    fertilizer_valves_open: FertilizerValveStates
    ## @brief Portata corrente dei concentrati, in millilitri all'ora.
    fertilizer_flow_milliliters_per_hour: FertilizerQuantities
    ## @brief Volume erogato nell'ultimo passo, in millilitri.
    fertilizer_volume_milliliters_last_step: FertilizerQuantities
    ## @brief Potenza elettrica corrente delle lampade, in watt.
    lighting_power_watts: float = Field(ge=0.0)


class ActuatorSnapshotCreate(BaseModel):
    """@brief Snapshot degli attuatori inviato dall'Edge Controller."""

    ## @brief Progressivo monotono generato dall'Edge per la zona.
    sequence_number: int = Field(ge=0)
    ## @brief Timestamp del simulatore, in secondi.
    timestamp_seconds: float = Field(ge=0.0)
    ## @brief Istante della rilevazione completo di fuso orario.
    recorded_at: AwareDatetime
    ## @brief Comando logico richiesto.
    command: ActuatorCommandState
    ## @brief Uscita fisica prodotta dagli attuatori.
    output: ActuatorPhysicalOutput


class ActuatorSnapshot(ActuatorSnapshotCreate):
    """@brief Snapshot degli attuatori persistito dal backend."""

    ## @brief Identificativo interno assegnato da SQLite.
    snapshot_id: int = Field(ge=1)
    ## @brief Zona alla quale appartiene lo snapshot.
    zone_id: str
    ## @brief Istante UTC di ricezione da parte del backend.
    received_at: AwareDatetime
