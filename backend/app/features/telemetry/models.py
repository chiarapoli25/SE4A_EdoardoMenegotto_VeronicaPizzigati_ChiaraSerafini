"""@file models.py
@brief Modelli Pydantic della telemetria dei sensori.
"""

from pydantic import AwareDatetime, BaseModel, Field

from ..zones.models import (
    ControlSetpoints,
    ControlStrategies,
    OperationalState,
    ZoneLifecycleState,
)


class GreenhouseTelemetry(BaseModel):
    """@brief Campione sincronizzato dei sensori inviato dalla serra.

    @details Il timestamp e sempre presente; ciascun canale e invece
    indipendentemente opzionale, per rappresentare un dropout strumentale.
    """

    ## @brief Timestamp della misura, in secondi simulati.
    timestamp_seconds: float = Field(ge=0.0)
    ## @brief Temperatura dell'aria in gradi Celsius, oppure `None`.
    temperature_c: float | None = Field(default=None, ge=-50.0, le=80.0)
    ## @brief Umidita relativa dell'aria in percentuale, oppure `None`.
    air_humidity_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    ## @brief Umidita del terriccio in percentuale, oppure `None`.
    soil_moisture_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    ## @brief EC apparente letta dalla sonda resistiva nel terriccio, in mS/cm.
    soil_bulk_ec_ms_cm: float | None = Field(default=None, ge=0.0, le=8.0)
    ## @brief EC stimata dell'acqua nei pori dopo la correzione per umidita.
    soil_ec_ms_cm: float | None = Field(default=None, ge=0.0, le=8.0)
    ## @brief Concentrazione totale stimata dei fertilizzanti solubili, in mg/L.
    fertilizer_concentration_mg_per_liter: float | None = Field(
        default=None,
        ge=0.0,
    )
    ## @brief Quota stimata di azoto della concentrazione totale, in mg/L.
    nitrogen_estimate_mg_per_liter: float | None = Field(default=None, ge=0.0)
    ## @brief Quota stimata di fosforo della concentrazione totale, in mg/L.
    phosphorus_estimate_mg_per_liter: float | None = Field(default=None, ge=0.0)
    ## @brief Quota stimata di potassio della concentrazione totale, in mg/L.
    potassium_estimate_mg_per_liter: float | None = Field(default=None, ge=0.0)
    ## @brief pH dell'acqua nei pori del terriccio, oppure `None`.
    ph: float | None = Field(default=None, ge=0.0, le=14.0)
    ## @brief PPFD della luce in umol/(m2 s), oppure `None`.
    light_ppfd_umol_m2_s: float | None = Field(
        default=None,
        ge=0.0,
        le=3000.0,
    )
    ## @brief Identificativo della ricetta attualmente eseguita.
    active_recipe_id: str = Field(min_length=1, max_length=64)
    ## @brief Versione invalidante della ricetta attualmente eseguita.
    active_recipe_version: int = Field(ge=1)
    ## @brief Fase corrente della ricetta.
    current_phase: str = Field(min_length=1, max_length=100)
    ## @brief Stato della FSM operativa dell'Edge.
    operational_state: OperationalState
    ## @brief Stato applicativo del ciclo della zona.
    lifecycle_state: ZoneLifecycleState
    ## @brief Strategy selezionata per ogni variabile controllata.
    current_strategies: ControlStrategies
    ## @brief Setpoint della fase corrente per ogni variabile controllata.
    current_setpoints: ControlSetpoints
    ## @brief Rapporto fra tempo simulato e tempo reale.
    time_scale: float = Field(ge=1.0, le=60.0)


class TelemetryCreate(GreenhouseTelemetry):
    """@brief Campione di telemetria ricevuto dall'Edge Controller.

    @details `sequence_number` identifica univocamente il campione all'interno
    della zona e rende idempotenti i retry dell'Edge. `recorded_at` indica
    quando la misura e stata prodotta e deve includere il fuso orario.
    """

    ## @brief Progressivo monotono generato dall'Edge per la singola zona.
    sequence_number: int = Field(ge=0)
    ## @brief Identifica univocamente il processo Edge fra due riavvii.
    boot_id: str = Field(
        default="legacy",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Istante della misura completo di fuso orario.
    recorded_at: AwareDatetime


class TelemetrySample(TelemetryCreate):
    """@brief Campione di telemetria persistito dal backend."""

    ## @brief Identificativo interno assegnato da SQLite.
    sample_id: int = Field(ge=1)
    ## @brief Zona alla quale appartiene il campione.
    zone_id: str
    ## @brief Istante UTC nel quale il backend ha ricevuto il campione.
    received_at: AwareDatetime
