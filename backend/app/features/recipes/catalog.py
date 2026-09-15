"""@file
@brief Caricamento, validazione e inizializzazione SQLite del catalogo ricette.
"""

import os
import sqlite3
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from .models import Recipe, RecipeCareProfile, SoilType


## @brief Directory del catalogo, sovrascrivibile tramite variabile d'ambiente.
DEFAULT_CATALOG_PATH = Path(
    os.environ.get(
        "SMARTHYDRO_RECIPE_CATALOG_PATH",
        Path(__file__).resolve().parents[4] / "recipe_catalog",
    )
)


class _CatalogModel(BaseModel):
    """@brief Base rigorosa dei modelli interni letti dai JSON del catalogo."""

    ## @brief Rifiuta chiavi sconosciute per intercettare errori nei dati sorgente.
    model_config = ConfigDict(extra="forbid")


class _PhaseProfile(_CatalogModel):
    """@brief Profilo riutilizzabile di una fase colturale."""

    ## @brief Chiave stabile usata da sequenze e override.
    key: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    ## @brief Nome leggibile della fase.
    name: str = Field(min_length=1)
    ## @brief Durata base della fase in ore.
    duration_hours: float = Field(gt=0.0)
    ## @brief Ora locale di inizio del fotoperiodo.
    start_hour: float = Field(ge=0.0, lt=24.0)
    ## @brief Correzione della durata luminosa rispetto al profilo base.
    photoperiod_offset_hours: float = Field(ge=-12.0, le=12.0)
    ## @brief Moltiplicatore del fabbisogno luminoso.
    light_factor: float = Field(gt=0.0, le=2.0)
    ## @brief Correzione del setpoint di umidita del terriccio.
    water_offset_percent: float = Field(ge=-30.0, le=30.0)
    ## @brief Moltiplicatore del target di azoto.
    nitrogen_factor: float = Field(ge=0.0, le=2.0)
    ## @brief Moltiplicatore del target di fosforo.
    phosphorus_factor: float = Field(ge=0.0, le=2.0)
    ## @brief Moltiplicatore del target di potassio.
    potassium_factor: float = Field(ge=0.0, le=2.0)
    ## @brief Moltiplicatore della dose suggerita per fase.
    dose_factor: float = Field(ge=0.0, le=2.0)
    ## @brief Scostamento del target pH.
    ph_offset: float = Field(ge=-1.0, le=1.0)


class _PhaseOverride(_CatalogModel):
    """@brief Sovrascritture opzionali applicate a una fase di una ricetta."""

    ## @brief Durata alternativa della fase.
    duration_hours: float | None = Field(default=None, gt=0.0)
    ## @brief Ora alternativa di inizio del fotoperiodo.
    start_hour: float | None = Field(default=None, ge=0.0, lt=24.0)
    ## @brief Correzione alternativa della durata luminosa.
    photoperiod_offset_hours: float | None = Field(
        default=None, ge=-12.0, le=12.0
    )
    ## @brief Moltiplicatore luminoso alternativo.
    light_factor: float | None = Field(default=None, gt=0.0, le=2.0)
    ## @brief Correzione alternativa dell'umidita del terriccio.
    water_offset_percent: float | None = Field(
        default=None, ge=-30.0, le=30.0
    )
    ## @brief Moltiplicatore alternativo del target di azoto.
    nitrogen_factor: float | None = Field(default=None, ge=0.0, le=2.0)
    ## @brief Moltiplicatore alternativo del target di fosforo.
    phosphorus_factor: float | None = Field(default=None, ge=0.0, le=2.0)
    ## @brief Moltiplicatore alternativo del target di potassio.
    potassium_factor: float | None = Field(default=None, ge=0.0, le=2.0)
    ## @brief Moltiplicatore alternativo della dose suggerita.
    dose_factor: float | None = Field(default=None, ge=0.0, le=2.0)
    ## @brief Scostamento alternativo del target pH.
    ph_offset: float | None = Field(default=None, ge=-1.0, le=1.0)
    ## @brief Profilo luminoso alternativo per questa fase.
    light_profile: str | None = Field(default=None, min_length=1)
    ## @brief Profilo idrico alternativo per questa fase.
    water_profile: str | None = Field(default=None, min_length=1)
    ## @brief Profilo nutrizionale alternativo per questa fase.
    feed_profile: str | None = Field(default=None, min_length=1)


class _LightProfile(_CatalogModel):
    """@brief Profilo di luce giornaliera e fotoperiodo."""

    ## @brief DLI giornaliero desiderato.
    setpoint: float = Field(ge=0.0)
    ## @brief DLI minimo ammesso.
    minimum: float = Field(ge=0.0)
    ## @brief DLI massimo descrittivo del profilo.
    maximum: float = Field(gt=0.0)
    ## @brief Durata base della finestra luminosa.
    photoperiod_hours: float = Field(gt=0.0, le=24.0)

    @model_validator(mode="after")
    def _check_range(self) -> "_LightProfile":
        """@brief Verifica che il setpoint appartenga all'intervallo ammesso."""
        if not self.minimum <= self.setpoint <= self.maximum:
            raise ValueError("light setpoint must be within its range")
        return self


class _WaterProfile(_CatalogModel):
    """@brief Profilo di umidita del terriccio e limite di irrigazione."""

    ## @brief Umidita desiderata in percentuale.
    setpoint: float = Field(ge=0.0, le=100.0)
    ## @brief Umidita minima ammessa.
    minimum: float = Field(ge=0.0, le=100.0)
    ## @brief Umidita massima ammessa.
    maximum: float = Field(ge=0.0, le=100.0)
    ## @brief Volume massimo erogabile da un singolo comando.
    maximum_water_liters: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _check_range(self) -> "_WaterProfile":
        """@brief Verifica che il setpoint appartenga all'intervallo ammesso."""
        if not self.minimum <= self.setpoint <= self.maximum:
            raise ValueError("water setpoint must be within its range")
        return self


class _FeedProfile(_CatalogModel):
    """@brief Target N/P/K e limiti comuni di dosaggio."""

    ## @brief Target base di azoto.
    nitrogen: float = Field(ge=0.0)
    ## @brief Target base di fosforo.
    phosphorus: float = Field(ge=0.0)
    ## @brief Target base di potassio.
    potassium: float = Field(ge=0.0)
    ## @brief Dose informativa prevista per una fase.
    phase_dose_milliliters: float = Field(ge=0.0)
    ## @brief Massima dose di un singolo comando.
    maximum_command_milliliters: float = Field(ge=0.0)
    ## @brief Massima dose cumulativa giornaliera.
    maximum_daily_milliliters: float = Field(ge=0.0)


class _RecipeSeed(_CatalogModel):
    """@brief Specifica compatta di una ricetta prima dell'espansione dei profili."""

    ## @brief Identificatore stabile della ricetta.
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    ## @brief Specie vegetale destinataria.
    plant_type: str = Field(min_length=1)
    ## @brief Reparto produttivo compatibile.
    department_number: int = Field(ge=1, le=4)
    substrate: SoilType
    care_profile: RecipeCareProfile
    ## @brief Nome del profilo luminoso base.
    light_profile: str = Field(min_length=1)
    ## @brief Nome del profilo idrico base.
    water_profile: str = Field(min_length=1)
    ## @brief Nome del profilo nutrizionale base.
    feed_profile: str = Field(min_length=1)
    ## @brief Target pH base della ricetta.
    ph_setpoint: float = Field(gt=4.8, lt=7.7)
    ## @brief Sequenza di fase esplicita; assente usa quella del reparto.
    phase_sequence: str | None = Field(default=None, min_length=1)
    ## @brief Override indicizzati tramite chiave della fase.
    phase_overrides: dict[str, _PhaseOverride] = Field(default_factory=dict)


class _CatalogProfiles(_CatalogModel):
    """@brief Documento dei profili condivisi e della relativa versione schema."""

    # v2 e' il vecchio catalogo multifase; v3 aveva provato Threshold come
    # default N/P/K al posto di Predictive (poi tornato indietro); v4 aveva
    # ricalcolato response_gain dalla banda della ricetta ma senza azzerare
    # cumulative_dose_gain, causando una fuga in avanti della stima una volta
    # raggiunto il setpoint (vedi _predictive_parameters); v6 ha convertito i
    # light_profiles da PPFD istantaneo (umol/(m2 s)) a DLI giornaliero
    # (mol/m2/giorno — vedi ControlledVariable.LIGHT e
    # RecipeControlSystem::execute() lato Edge): senza questo bump, i
    # database gia' seminati con la v5 sarebbero rimasti con target PPFD
    # nell'ordine delle centinaia, interpretati come mol/m2/giorno —
    # irraggiungibili, lampada sempre accesa. v7 aveva aggiunto
    # output_limits.lighting_reference_ppfd_umol_m2_s e una legge di
    # comando "a tempo residuo" (percentuale continua) per il ramo LIGHT;
    # v8 la sostituisce con un controllo ON/OFF a soglia sul deficit DLI
    # giornaliero (mai durante la finestra di luce naturale, solo dopo il
    # tramonto — vedi Photoperiod e RecipeControlSystem::execute() in
    # control_system.cpp) e aggiunge output_limits.
    # maximum_supplemental_lighting_hours_per_day (_safety_limits()): senza
    # questo bump, le ricette gia' seminate con la v7 non avrebbero quel
    # campo e la nuova legge di comando non potrebbe caricarle. Il limite
    # superiore va spostato a ogni nuovo bump: e' quello che fa scattare
    # seed_recipe_catalog() a rimigrare le ricette gia' importate a una
    # versione precedente (vedi la colonna
    # recipe_catalog_imports.catalog_version). v9 riallinea le ricette gia'
    # seminate al correttore pH da 1 mL: il valore precedente di 0.5 mL
    # rendeva in pratica irraggiungibile il setpoint, pur con irrigazioni
    # disponibili a veicolare il prodotto.
    ## @brief Versione che governa la migrazione del catalogo nel database.
    schema_version: int = Field(ge=2, le=9)
    ## @brief Sequenze ordinate di fasi riutilizzabili.
    phase_sequences: dict[str, list[_PhaseProfile]] = Field(min_length=1)
    ## @brief Profili luminosi indicizzati per nome.
    light_profiles: dict[str, _LightProfile] = Field(min_length=1)
    ## @brief Profili idrici indicizzati per nome.
    water_profiles: dict[str, _WaterProfile] = Field(min_length=1)
    ## @brief Profili nutrizionali indicizzati per nome.
    feed_profiles: dict[str, _FeedProfile] = Field(min_length=1)


## @brief Sequenza di fase predefinita per ciascun reparto produttivo.
_DEPARTMENT_PHASE_SEQUENCE = {
    1: "foliage",
    2: "flowering",
    3: "succulent",
    4: "fruiting",
}


def _phase_sequence_name(recipe: _RecipeSeed) -> str:
    """@brief Risolve la sequenza esplicita o quella predefinita del reparto."""
    return recipe.phase_sequence or _DEPARTMENT_PHASE_SEQUENCE[
        recipe.department_number
    ]


def _validate_recipe_seeds(
    profiles: _CatalogProfiles,
    recipes: list[_RecipeSeed],
) -> None:
    """@brief Verifica unicita e riferimenti incrociati dei file di catalogo."""
    recipe_ids = [recipe.id for recipe in recipes]
    if len(recipe_ids) != len(set(recipe_ids)):
        raise ValueError("recipe ids in catalog must be unique")
    for recipe in recipes:
        sequence_name = _phase_sequence_name(recipe)
        if sequence_name not in profiles.phase_sequences:
            raise ValueError(
                f"recipe {recipe.id!r} uses unknown phase sequence "
                f"{sequence_name!r}"
            )
        if recipe.light_profile not in profiles.light_profiles:
            raise ValueError(
                f"recipe {recipe.id!r} uses unknown light profile "
                f"{recipe.light_profile!r}"
            )
        if recipe.water_profile not in profiles.water_profiles:
            raise ValueError(
                f"recipe {recipe.id!r} uses unknown water profile "
                f"{recipe.water_profile!r}"
            )
        if recipe.feed_profile not in profiles.feed_profiles:
            raise ValueError(
                f"recipe {recipe.id!r} uses unknown feed profile "
                f"{recipe.feed_profile!r}"
            )
        phase_keys = {
            phase.key for phase in profiles.phase_sequences[sequence_name]
        }
        if len(phase_keys) != len(profiles.phase_sequences[sequence_name]):
            raise ValueError(
                f"phase sequence {sequence_name!r} contains duplicate keys"
            )
        unknown_overrides = set(recipe.phase_overrides) - phase_keys
        if unknown_overrides:
            raise ValueError(
                f"recipe {recipe.id!r} overrides unknown phases "
                f"{sorted(unknown_overrides)!r}"
            )
        for phase_key, override in recipe.phase_overrides.items():
            for profile_kind, profile_name, available in (
                ("light", override.light_profile, profiles.light_profiles),
                ("water", override.water_profile, profiles.water_profiles),
                ("feed", override.feed_profile, profiles.feed_profiles),
            ):
                if profile_name is not None and profile_name not in available:
                    raise ValueError(
                        f"recipe {recipe.id!r} phase {phase_key!r} uses "
                        f"unknown {profile_kind} profile {profile_name!r}"
                    )


def _target(
    variable: str,
    setpoint: float,
    minimum: float,
    maximum: float,
    safety_minimum: float,
    safety_maximum: float,
    dose: float = 0.0,
) -> dict:
    """@brief Costruisce il contratto numerico di un target di fase."""
    return {
        "variable": variable,
        "setpoint": setpoint,
        "allowed_range": {"minimum": minimum, "maximum": maximum},
        "safety_range": {
            "minimum": safety_minimum,
            "maximum": safety_maximum,
        },
        "suggested_phase_dose_milliliters": dose,
    }


def _nutrient_target(variable: str, setpoint: float, dose: float) -> dict:
    """@brief Costruisce target e bande di sicurezza per una variabile N/P/K."""
    margin = max(5.0, round(setpoint * 0.15, 1))
    safety_maximum = 500.0 if variable == "potassium" else 350.0
    return _target(
        variable,
        setpoint,
        max(0.0, setpoint - margin),
        setpoint + margin,
        0.0,
        safety_maximum,
        dose,
    )


def _safety_limits(
    maximum_water_liters: float = 1.0,
    maximum_dose_milliliters: float = 5.0,
    maximum_daily_milliliters: float = 20.0,
    minimum_seconds_between_doses: float = 900.0,
) -> dict:
    """@brief Costruisce i limiti comuni di acqua, dosaggio e luce artificiale."""
    return {
        "maximum_water_volume_liters": maximum_water_liters,
        "maximum_pump_duration_seconds": max(
            1.0, maximum_water_liters / 2.0 * 3600.0
        ),
        "water_pump_flow_liters_per_hour": 2.0,
        "maximum_dose_per_command_milliliters": maximum_dose_milliliters,
        "maximum_daily_dose_milliliters": maximum_daily_milliliters,
        "minimum_seconds_between_doses": minimum_seconds_between_doses,
        "ph_settling_time_seconds": 1800.0,
        # Taratura assunta della plafoniera simulata: 700W (ActuatorConfig::
        # maximum_lighting_power_watts) * 2.0 umol/(m2 s W) (EnvironmentConfig
        # ::lamp_ppfd_umol_m2_s_per_watt) = 1400 umol/(m2 s) da accesa — la
        # stessa relazione gia' usata sopra per
        # water_pump_flow_liters_per_hour=2.0 (identico alla portata
        # simulata della pompa). Il controllore LIGHT comanda ON/OFF (vedi
        # RecipeControlSystem::execute(), ramo LIGHT), non piu' una
        # percentuale: questo valore resta solo per stimare il contributo
        # artificiale al DLI giornaliero (dashboard/riepiloghi) — non e'
        # un valore che il controllo scopre dal simulatore, e' la stessa
        # calibrazione che un agronomo misurerebbe puntando un quantum
        # meter sotto la propria plafoniera in sede di commissioning.
        "lighting_reference_ppfd_umol_m2_s": 1400.0,
        # Tetto di illuminazione supplementare notturna: un valore
        # prudente per ogni ricetta (nessuna specie del catalogo ha oggi
        # un fabbisogno che lo saturi in condizioni meteo tipiche — vedi
        # la validazione empirica nella cronologia del progetto), non
        # derivato dal light_profile della singola ricetta.
        "maximum_supplemental_lighting_hours_per_day": 6.0,
    }


def _controller(
    variable: str,
    parameters: dict,
    unit: str,
    output_limits: dict,
) -> dict:
    """@brief Costruisce la configurazione Edge completa di un controllore."""
    associations = {
        "soil_moisture": ("soil_moisture_sensor", "water_pump", "Threshold"),
        "light": ("light_sensor", "lighting", "Threshold"),
        "ph": ("ph_sensor", "ph_corrector_valves", "PID"),
        "nitrogen": ("nitrogen_model", "nitrogen_valve", "Predictive"),
        "phosphorus": (
            "phosphorus_model",
            "phosphorus_valve",
            "Predictive",
        ),
        "potassium": ("potassium_model", "potassium_valve", "Predictive"),
    }
    input_source, actuator, strategy = associations[variable]
    return {
        "variable": variable,
        "input_source": input_source,
        "actuator": actuator,
        "default_strategy": strategy,
        "selected_strategy": strategy,
        "parameters": parameters,
        "unit": unit,
        "output_limits": output_limits,
        "confirmation_state": "PENDING_CONFIRMATION",
        "version": 1,
        "confirmed_recipe_version": 0,
    }


def _predictive_parameters(
    target: dict,
    command_max: float,
) -> dict:
    """@brief Parametri Predictive di bootstrap per un controllore N/P/K.

    @details Le valvole di concentrato si aprono solo mentre la pompa
    dell'acqua sta irrigando (si veda `apply_decisions` in
    `edge_runtime_actuation.cpp`), non a ogni ciclo di controllo: le
    occasioni per dosare sono percio' rare (in pratica una manciata al
    giorno, secondo la frequenza di irrigazione della ricetta), quindi ogni
    dose deve valere per l'attesa fino alla prossima, non solo per il ciclo
    corrente. `response_gain` viene percio' calcolato dalla banda della
    fase (`allowed_range`) invece che da una costante fissa uguale per ogni
    pianta: un errore grande quanto il margine della banda spinge gia' il
    comando vicino a `command_max`, cosi' un vero deficit viene corretto in
    una sola occasione utile invece di richiedere molti piccoli dosaggi che
    quasi mai coincidono con un'irrigazione. `prediction_horizon_steps` resta
    basso (il trend e' calcolato ogni singolo ciclo di controllo, 15 minuti
    di norma: proiettarlo troppo in avanti amplificherebbe il rumore della
    stima invece di anticipare un bisogno reale).

    `cumulative_dose_gain` e' azzerato, a differenza della vecchia taratura:
    in PredictiveController::compute() (controllers.cpp) si somma al termine
    di errore SENZA che quest'ultimo possa scendere sotto zero
    (`command_minimum = 0`, una valvola puo' solo aggiungere fertilizzante,
    mai ritirarlo). Con un `response_gain` piccolo il termine restava
    trascurabile e il problema passava inosservato, ma e' comunque un
    guadagno positivo incondizionato: appena la stima raggiunge il setpoint
    il termine d'errore si annulla e resta solo questo, che continua a
    spingere il comando verso l'alto finche' rimane dose di fase da erogare
    — una fuga in avanti, verificata fino a piu' di 3 volte il setpoint in
    30 giorni di simulazione. Il preventivo di dose per fase resta comunque
    tracciato (si veda `suggested_phase_dose_milliliters`) per finalita'
    informative, ma non deve mai forzare un dosaggio indipendente dal reale
    bisogno.
    """
    setpoint = target["setpoint"]
    margin = max(
        target["allowed_range"]["maximum"] - setpoint,
        setpoint - target["allowed_range"]["minimum"],
        1e-6,
    )
    gains = {
        "nitrogen": (2.0, 4.0),
        "phosphorus": (0.8, 2.0),
        "potassium": (2.5, 5.0),
    }
    dilution, substrate = gains[target["variable"]]
    return {
        "setpoint": setpoint,
        "prediction_horizon_steps": 1.0,
        # La dose resta nel substrato anche dopo la chiusura della valvola:
        # una correzione piena gia' al bordo della banda produceva il dente
        # di sega osservato soprattutto nelle succulente. Dose progressiva,
        # massimo ancora raggiungibile per deficit pari a dieci bande.
        "response_gain": 0.10 * command_max / margin,
        "neutral_command": 0.0,
        "command_minimum": 0.0,
        "command_maximum": command_max,
        "direction": "increases",
        "water_dilution_gain": dilution,
        "cumulative_dose_gain": 0.0,
        "substrate_gain": substrate,
        "integral_gain": 0.0,
    }


def _phase_value(
    phase: _PhaseProfile,
    override: _PhaseOverride | None,
    field_name: str,
) -> float:
    """@brief Risolve un parametro di fase applicando l'eventuale override."""
    override_value = None if override is None else getattr(override, field_name)
    return getattr(phase, field_name) if override_value is None else override_value


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """@brief Limita un valore all'intervallo chiuso indicato."""
    return max(minimum, min(maximum, value))


def _build_recipe(spec: _RecipeSeed, catalog: _CatalogProfiles) -> Recipe:
    """@brief Espande seed e profili condivisi in una ricetta Edge completa."""
    sequence = catalog.phase_sequences[_phase_sequence_name(spec)]
    phases: list[dict] = []
    resolved: list[dict] = []

    for phase in sequence:
        override = spec.phase_overrides.get(phase.key)
        light_name = (
            spec.light_profile
            if override is None or override.light_profile is None
            else override.light_profile
        )
        water_name = (
            spec.water_profile
            if override is None or override.water_profile is None
            else override.water_profile
        )
        feed_name = (
            spec.feed_profile
            if override is None or override.feed_profile is None
            else override.feed_profile
        )
        light = catalog.light_profiles[light_name]
        water = catalog.water_profiles[water_name]
        feed = catalog.feed_profiles[feed_name]

        light_factor = _phase_value(phase, override, "light_factor")
        water_offset = _phase_value(
            phase, override, "water_offset_percent"
        )
        nitrogen_factor = _phase_value(
            phase, override, "nitrogen_factor"
        )
        phosphorus_factor = _phase_value(
            phase, override, "phosphorus_factor"
        )
        potassium_factor = _phase_value(
            phase, override, "potassium_factor"
        )
        dose_factor = _phase_value(phase, override, "dose_factor")
        ph_setpoint = spec.ph_setpoint + _phase_value(
            phase, override, "ph_offset"
        )
        photoperiod_hours = _clamp(
            light.photoperiod_hours
            + _phase_value(phase, override, "photoperiod_offset_hours"),
            1.0,
            24.0,
        )

        light_setpoint = light.setpoint * light_factor
        light_minimum = light.minimum * light_factor
        light_maximum = light.maximum * light_factor
        water_setpoint = _clamp(water.setpoint + water_offset, 0.0, 100.0)
        water_minimum = _clamp(water.minimum + water_offset, 0.0, 100.0)
        water_maximum = _clamp(water.maximum + water_offset, 0.0, 100.0)
        nutrient_setpoints = {
            "nitrogen": feed.nitrogen * nitrogen_factor,
            "phosphorus": feed.phosphorus * phosphorus_factor,
            "potassium": feed.potassium * potassium_factor,
        }
        phase_dose = feed.phase_dose_milliliters * dose_factor
        targets = [
            _target(
                "soil_moisture",
                water_setpoint,
                water_minimum,
                water_maximum,
                0.0,
                100.0,
            ),
            _target(
                "light",
                light_setpoint,
                light_minimum,
                light_maximum,
                0.0,
                80.0,
            ),
            _target(
                "ph",
                ph_setpoint,
                ph_setpoint - 0.3,
                ph_setpoint + 0.3,
                4.5,
                8.0,
            ),
        ]
        targets.extend(
            _nutrient_target(variable, setpoint, phase_dose)
            for variable, setpoint in nutrient_setpoints.items()
        )
        phases.append(
            {
                "name": phase.name,
                "duration_hours": _phase_value(
                    phase, override, "duration_hours"
                ),
                "photoperiod": {
                    "start_hour": _phase_value(
                        phase, override, "start_hour"
                    ),
                    "duration_hours": photoperiod_hours,
                },
                "targets": targets,
            }
        )
        resolved.append(
            {
                "light": light,
                "water": water,
                "feed": feed,
                "targets": targets,
                "nutrient_setpoints": nutrient_setpoints,
            }
        )

    first = resolved[0]
    first_targets = {target["variable"]: target for target in first["targets"]}
    maximum_water_liters = max(
        phase["water"].maximum_water_liters for phase in resolved
    )
    maximum_command_milliliters = max(
        phase["feed"].maximum_command_milliliters for phase in resolved
    )
    maximum_daily_milliliters = max(
        phase["feed"].maximum_daily_milliliters for phase in resolved
    )
    nutrient_limits = _safety_limits(
        maximum_dose_milliliters=maximum_command_milliliters,
        maximum_daily_milliliters=maximum_daily_milliliters,
        minimum_seconds_between_doses=3600.0,
    )
    controllers = [
        _controller(
            "soil_moisture",
            {
                "lower_threshold": first_targets["soil_moisture"][
                    "allowed_range"
                ]["minimum"],
                "upper_threshold": first_targets["soil_moisture"][
                    "allowed_range"
                ]["maximum"],
                "direction": "increases",
                "active_command": 0.5,
                "inactive_command": 0.0,
                "bidirectional": False,
            },
            "% soil moisture",
            _safety_limits(maximum_water_liters=maximum_water_liters),
        ),
        _controller(
            "light",
            {
                "lower_threshold": first_targets["light"]["allowed_range"][
                    "minimum"
                ],
                "upper_threshold": first_targets["light"]["allowed_range"][
                    "maximum"
                ],
                "direction": "increases",
                "active_command": 100.0,
                "inactive_command": 0.0,
                "bidirectional": False,
            },
            "mol/(m2 day)",
            _safety_limits(),
        ),
        _controller(
            "ph",
            {
                "setpoint": first_targets["ph"]["setpoint"],
                # Stessa scala di default_parameters_for(): al bordo della
                # banda pH (±0.3 nel catalogo) il PID puo' richiedere 1 mL.
                "proportional_gain": 1.0 / 0.3,
                "integral_gain": (1.0 / 0.3) / (4.0 * 3600.0),
                "derivative_gain": 0.0,
                "command_minimum": -1.0,
                "command_maximum": 1.0,
                "direction": "increases",
            },
            "pH",
            _safety_limits(
                maximum_dose_milliliters=1.0,
                maximum_daily_milliliters=5.0,
            ),
        ),
    ]
    for variable in ("nitrogen", "phosphorus", "potassium"):
        controllers.append(
            _controller(
                variable,
                _predictive_parameters(
                    first_targets[variable],
                    maximum_command_milliliters,
                ),
                "mg/L",
                nutrient_limits,
            )
        )

    return Recipe.model_validate(
        {
            "id": spec.id,
            "plant_type": spec.plant_type,
            "substrate": spec.substrate,
            "version": catalog.schema_version,
            "department_number": spec.department_number,
            "care_profile": spec.care_profile,
            "phases": phases,
            "controllers": controllers,
        }
    )


def load_recipe_catalog(
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> tuple[Recipe, ...]:
    """Legge profili e file pianta, indicando precisamente gli errori."""
    directory = Path(catalog_path)
    profiles_path = directory / "profiles.json"
    try:
        profiles = _CatalogProfiles.model_validate_json(
            profiles_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as error:
        raise ValueError(
            f"invalid catalog profiles file {profiles_path}: {error}"
        ) from error

    recipes_directory = directory / "recipes"
    recipe_paths = sorted(recipes_directory.glob("*.json"))
    if not recipe_paths:
        raise ValueError(
            f"recipe catalog directory {recipes_directory} contains no JSON files"
        )

    recipe_seeds: list[_RecipeSeed] = []
    for recipe_path in recipe_paths:
        try:
            recipe_seeds.append(
                _RecipeSeed.model_validate_json(
                    recipe_path.read_text(encoding="utf-8")
                )
            )
        except (OSError, ValidationError) as error:
            raise ValueError(
                f"invalid recipe catalog file {recipe_path}: {error}"
            ) from error

    _validate_recipe_seeds(profiles, recipe_seeds)
    return tuple(_build_recipe(spec, profiles) for spec in recipe_seeds)


def seed_recipe_catalog(
    connection: sqlite3.Connection,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> int:
    """Importa o migra il bootstrap, lasciando poi SQLite come autorita."""
    changed = 0
    for recipe in load_recipe_catalog(catalog_path):
        imported = connection.execute(
            """
            SELECT catalog_version
            FROM recipe_catalog_imports
            WHERE recipe_id = ?
            """,
            (recipe.id,),
        ).fetchone()
        if imported is not None and imported[0] >= recipe.version:
            continue

        stored = connection.execute(
            "SELECT version FROM recipes WHERE id = ?",
            (recipe.id,),
        ).fetchone()
        if stored is None:
            connection.execute(
                """
                INSERT INTO recipes (id, version, data, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (recipe.id, recipe.version, recipe.model_dump_json()),
            )
            changed += 1
        elif stored[0] < recipe.version:
            connection.execute(
                """
                UPDATE recipes
                SET version = ?, data = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (recipe.version, recipe.model_dump_json(), recipe.id),
            )
            changed += 1

        connection.execute(
            """
            INSERT INTO recipe_catalog_imports (
                recipe_id, imported_at, catalog_version
            )
            VALUES (?, datetime('now'), ?)
            ON CONFLICT(recipe_id) DO UPDATE SET
                imported_at = excluded.imported_at,
                catalog_version = excluded.catalog_version
            """,
            (recipe.id, recipe.version),
        )
    return changed
