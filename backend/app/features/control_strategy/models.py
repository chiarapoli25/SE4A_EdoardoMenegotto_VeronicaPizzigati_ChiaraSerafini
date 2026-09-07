"""@file models.py
@brief Modelli della Strategy di controllo a livello di impianto.

@details Prima del 2026, Threshold/PID/Predictive era una caratteristica di
ogni singola ricetta (`recipes.models.ControllerConfiguration.selected_strategy`).
Questo modulo la trasforma in un'unica impostazione per variabile, condivisa
da tutto l'impianto: setpoint, range e limiti di sicurezza restano proprieta'
della ricetta (variano per reparto/specie), ma il tipo di dinamica di
controllo no.
"""

from pydantic import BaseModel

from ..recipes.models import ControlledVariable, StrategyType


class ControlStrategySetting(BaseModel):
    """@brief Strategy corrente per una singola variabile controllata."""

    ## @brief Variabile alla quale si applica la Strategy.
    variable: ControlledVariable
    ## @brief Strategy attualmente in vigore per l'intero impianto.
    selected_strategy: StrategyType


class ControlStrategyUpdate(BaseModel):
    """@brief Corpo della richiesta per cambiare la Strategy di una variabile."""

    ## @brief Nuova Strategy da applicare a tutto l'impianto.
    selected_strategy: StrategyType
