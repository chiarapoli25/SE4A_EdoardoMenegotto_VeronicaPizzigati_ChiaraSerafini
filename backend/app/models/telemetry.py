"""@file telemetry.py
@brief Modelli Pydantic della telemetria dei sensori.
"""

from pydantic import AwareDatetime, BaseModel, Field


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
    ## @brief pH della soluzione nei pori del terriccio, oppure `None`.
    ph: float | None = Field(default=None, ge=0.0, le=14.0)
    ## @brief PPFD della luce in umol/(m2 s), oppure `None`.
    light_ppfd_umol_m2_s: float | None = Field(
        default=None,
        ge=0.0,
        le=3000.0,
    )


class TelemetryCreate(GreenhouseTelemetry):
    """@brief Campione di telemetria ricevuto dall'Edge Controller.

    @details `sequence_number` identifica univocamente il campione all'interno
    della zona e rende idempotenti i retry dell'Edge. `recorded_at` indica
    quando la misura e stata prodotta e deve includere il fuso orario.
    """

    ## @brief Progressivo monotono generato dall'Edge per la singola zona.
    sequence_number: int = Field(ge=0)
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
