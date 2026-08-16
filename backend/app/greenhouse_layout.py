"""Configurazione stabile dei reparti fisici della serra SmartHydro."""

from types import MappingProxyType


PRODUCTION_DEPARTMENT_NAMES = MappingProxyType(
    {
        1: "Piante Tropicali e da Fogliame",
        2: "Piante da Fiore",
        3: "Piante Grasse e Succulente",
        4: "Piante da Frutto e Ortaggi",
    }
)

QUARANTINE_DEPARTMENT_NUMBER = 5
QUARANTINE_DEPARTMENT_NAME = "Quarantena"

DEPARTMENT_NAMES = MappingProxyType(
    {
        **PRODUCTION_DEPARTMENT_NAMES,
        QUARANTINE_DEPARTMENT_NUMBER: QUARANTINE_DEPARTMENT_NAME,
    }
)


def department_name(department_number: int) -> str:
    """Restituisce il nome canonico del reparto oppure segnala il numero errato."""
    try:
        return DEPARTMENT_NAMES[department_number]
    except KeyError as error:
        raise ValueError(
            f"unknown greenhouse department {department_number}"
        ) from error
