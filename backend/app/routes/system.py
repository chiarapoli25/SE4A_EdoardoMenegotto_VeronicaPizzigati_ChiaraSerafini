"""@file system.py
@brief Endpoint generali di identificazione e disponibilita.
"""

from fastapi import APIRouter


## @brief Router degli endpoint generali del servizio.
router = APIRouter(tags=["system"])


@router.get("/")
def read_root() -> dict[str, str]:
    """@brief Restituisce l'identita pubblica del servizio."""
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@router.get("/health")
def read_health() -> dict[str, str]:
    """@brief Segnala che il processo HTTP e in esecuzione.

    @details Lo stato e ancora statico e non verifica database o Edge.
    """
    return {"status": "healthy"}
