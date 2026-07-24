"""Applicazione FastAPI e relativi endpoint HTTP di SmartHydro.

Il modulo costruisce l'oggetto ASGI importato dal server e definisce gli
endpoint pubblici disponibili nella fase corrente. Non comunica ancora con
l'Edge Controller e non mantiene stato persistente.
"""

from fastapi import FastAPI


## Applicazione ASGI principale, con titolo e versione esposti da OpenAPI.
app = FastAPI(title="SmartHydro Backend", version="0.1.0")


@app.get("/")
def read_root() -> dict[str, str]:
    """Restituisce l'identita pubblica del servizio.

    Restituisce un dizionario JSON-serializzabile con nome del backend e
    versione applicativa. La funzione non modifica stato e non accede a
    risorse esterne.
    """
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@app.get("/health")
def read_health() -> dict[str, str]:
    """Segnala che il processo HTTP e in esecuzione.

    Restituisce un dizionario JSON-serializzabile con ``status`` uguale a
    ``"healthy"``. Il risultato e statico: non verifica database, sensori,
    attuatori o collegamento con l'Edge Controller.
    """
    return {"status": "healthy"}
