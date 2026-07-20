from fastapi import FastAPI


app = FastAPI(title="SmartHydro Backend", version="0.1.0")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"name": "SmartHydro Backend", "version": "0.1.0"}


@app.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "healthy"}
