"""Applicazione FastAPI e relativi endpoint HTTP di SmartHydro."""

from fastapi import FastAPI


## Applicazione FastAPI principale del backend SmartHydro.
app = FastAPI(title="SmartHydro Backend", version="0.1.0")


@app.get("/")
def read_root() -> dict[str, str]:
    """Restituisce nome e versione del backend."""
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@app.get("/health")
def read_health() -> dict[str, str]:
    """Restituisce lo stato di salute del backend."""
    return {"status": "healthy"}
