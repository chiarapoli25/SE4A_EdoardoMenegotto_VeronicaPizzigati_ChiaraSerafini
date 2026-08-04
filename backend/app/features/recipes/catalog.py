"""Caricamento e inizializzazione SQLite del catalogo ricette esterno."""

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


DEFAULT_CATALOG_PATH = Path(
    os.environ.get(
        "SMARTHYDRO_RECIPE_CATALOG_PATH",
        Path(__file__).resolve().parents[4] / "config" / "recipe_catalog",
    )
)


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _PhaseProfile(_CatalogModel):
    name: str = Field(min_length=1)
    duration_hours: float = Field(gt=0.0)
    start_hour: float = Field(ge=0.0, lt=24.0)


class _LightProfile(_CatalogModel):
    setpoint: float = Field(ge=0.0)
    minimum: float = Field(ge=0.0)
    maximum: float = Field(gt=0.0)
    photoperiod_hours: float = Field(gt=0.0, le=24.0)

    @model_validator(mode="after")
    def _check_range(self) -> "_LightProfile":
        if not self.minimum <= self.setpoint <= self.maximum:
            raise ValueError("light setpoint must be within its range")
        return self


class _WaterProfile(_CatalogModel):
    setpoint: float = Field(ge=0.0, le=100.0)
    minimum: float = Field(ge=0.0, le=100.0)
    maximum: float = Field(ge=0.0, le=100.0)
    maximum_water_liters: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _check_range(self) -> "_WaterProfile":
        if not self.minimum <= self.setpoint <= self.maximum:
            raise ValueError("water setpoint must be within its range")
        return self


class _FeedProfile(_CatalogModel):
    nitrogen: float = Field(ge=0.0)
    phosphorus: float = Field(ge=0.0)
    potassium: float = Field(ge=0.0)
    phase_dose_milliliters: float = Field(ge=0.0)
    maximum_command_milliliters: float = Field(ge=0.0)
    maximum_daily_milliliters: float = Field(ge=0.0)


class _RecipeSeed(_CatalogModel):
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    plant_type: str = Field(min_length=1)
    department_number: int = Field(ge=1, le=4)
    substrate: SoilType
    care_profile: RecipeCareProfile
    light_profile: str = Field(min_length=1)
    water_profile: str = Field(min_length=1)
    feed_profile: str = Field(min_length=1)
    ph_setpoint: float = Field(gt=4.8, lt=7.7)


class _CatalogProfiles(_CatalogModel):
    schema_version: int = Field(ge=1, le=1)
    phase: _PhaseProfile
    light_profiles: dict[str, _LightProfile] = Field(min_length=1)
    water_profiles: dict[str, _WaterProfile] = Field(min_length=1)
    feed_profiles: dict[str, _FeedProfile] = Field(min_length=1)


def _validate_recipe_seeds(
    profiles: _CatalogProfiles,
    recipes: list[_RecipeSeed],
) -> None:
    recipe_ids = [recipe.id for recipe in recipes]
    if len(recipe_ids) != len(set(recipe_ids)):
        raise ValueError("recipe ids in catalog must be unique")
    for recipe in recipes:
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


def _target(
    variable: str,
    setpoint: float,
    minimum: float,
    maximum: float,
    safety_minimum: float,
    safety_maximum: float,
    dose: float = 0.0,
) -> dict:
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
    margin = max(5.0, round(setpoint * 0.15, 1))
    safety_maximum = 500.0 if variable == "potassium" else 350.0
    return _target(
        variable,
        setpoint,
        setpoint - margin,
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
    }


def _controller(
    variable: str,
    parameters: dict,
    unit: str,
    output_limits: dict,
) -> dict:
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
    sensor, actuator, strategy = associations[variable]
    return {
        "variable": variable,
        "sensor": sensor,
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
    setpoint: float,
    variable: str,
    command_max: float,
) -> dict:
    gains = {
        "nitrogen": (0.03, 2.0, 0.04, 4.0),
        "phosphorus": (0.05, 0.8, 0.03, 2.0),
        "potassium": (0.025, 2.5, 0.04, 5.0),
    }
    response, dilution, cumulative, substrate = gains[variable]
    return {
        "setpoint": setpoint,
        "prediction_horizon_steps": 4.0,
        "response_gain": response,
        "neutral_command": 0.0,
        "command_minimum": 0.0,
        "command_maximum": command_max,
        "direction": "increases",
        "water_dilution_gain": dilution,
        "cumulative_dose_gain": cumulative,
        "substrate_gain": substrate,
    }


def _build_recipe(spec: _RecipeSeed, catalog: _CatalogProfiles) -> Recipe:
    light = catalog.light_profiles[spec.light_profile]
    water = catalog.water_profiles[spec.water_profile]
    feed = catalog.feed_profiles[spec.feed_profile]
    targets = [
        _target(
            "soil_moisture",
            water.setpoint,
            water.minimum,
            water.maximum,
            0.0,
            100.0,
        ),
        _target(
            "light",
            light.setpoint,
            light.minimum,
            light.maximum,
            0.0,
            1200.0,
        ),
        _target(
            "ph",
            spec.ph_setpoint,
            spec.ph_setpoint - 0.3,
            spec.ph_setpoint + 0.3,
            4.5,
            8.0,
        ),
        _nutrient_target(
            "nitrogen", feed.nitrogen, feed.phase_dose_milliliters
        ),
        _nutrient_target(
            "phosphorus", feed.phosphorus, feed.phase_dose_milliliters
        ),
        _nutrient_target(
            "potassium", feed.potassium, feed.phase_dose_milliliters
        ),
    ]
    nutrient_limits = _safety_limits(
        maximum_dose_milliliters=feed.maximum_command_milliliters,
        maximum_daily_milliliters=feed.maximum_daily_milliliters,
        minimum_seconds_between_doses=3600.0,
    )
    controllers = [
        _controller(
            "soil_moisture",
            {
                "lower_threshold": water.minimum,
                "upper_threshold": water.maximum,
                "direction": "increases",
                "active_command": 0.5,
                "inactive_command": 0.0,
                "bidirectional": False,
            },
            "% soil moisture",
            _safety_limits(maximum_water_liters=water.maximum_water_liters),
        ),
        _controller(
            "light",
            {
                "lower_threshold": light.minimum,
                "upper_threshold": light.maximum,
                "direction": "increases",
                "active_command": 100.0,
                "inactive_command": 0.0,
                "bidirectional": False,
            },
            "umol/(m2 s)",
            _safety_limits(),
        ),
        _controller(
            "ph",
            {
                "setpoint": spec.ph_setpoint,
                "proportional_gain": 1.0,
                "integral_gain": 0.00001,
                "derivative_gain": 0.0,
                "command_minimum": -0.5,
                "command_maximum": 0.5,
                "direction": "increases",
            },
            "pH",
            _safety_limits(
                maximum_dose_milliliters=0.5,
                maximum_daily_milliliters=5.0,
            ),
        ),
    ]
    for variable, setpoint in (
        ("nitrogen", feed.nitrogen),
        ("phosphorus", feed.phosphorus),
        ("potassium", feed.potassium),
    ):
        controllers.append(
            _controller(
                variable,
                _predictive_parameters(
                    setpoint,
                    variable,
                    feed.maximum_command_milliliters,
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
            "version": 1,
            "department_number": spec.department_number,
            "care_profile": spec.care_profile,
            "phases": [
                {
                    "name": catalog.phase.name,
                    "duration_hours": catalog.phase.duration_hours,
                    "photoperiod": {
                        "start_hour": catalog.phase.start_hour,
                        "duration_hours": light.photoperiod_hours,
                    },
                    "targets": targets,
                }
            ],
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
    """Importa una sola volta ogni id, lasciando poi SQLite come autorita."""
    inserted = 0
    for recipe in load_recipe_catalog(catalog_path):
        already_imported = connection.execute(
            """
            SELECT 1
            FROM recipe_catalog_imports
            WHERE recipe_id = ?
            """,
            (recipe.id,),
        ).fetchone()
        if already_imported is not None:
            continue
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO recipes (id, version, data, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (recipe.id, recipe.version, recipe.model_dump_json()),
        )
        connection.execute(
            """
            INSERT INTO recipe_catalog_imports (recipe_id, imported_at)
            VALUES (?, datetime('now'))
            """,
            (recipe.id,),
        )
        inserted += cursor.rowcount
    return inserted
