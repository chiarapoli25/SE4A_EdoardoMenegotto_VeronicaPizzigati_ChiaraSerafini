"""Configurazione runtime del backend letta dall'ambiente."""

import math
import os


DEFAULT_OFFLINE_THRESHOLD_SECONDS = 60.0


def offline_threshold_seconds() -> float:
    """Soglia oltre la quale una zona senza contatti Edge diventa offline."""
    raw_value = os.environ.get(
        "SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS",
        str(DEFAULT_OFFLINE_THRESHOLD_SECONDS),
    )
    try:
        value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS must be numeric"
        ) from error
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError(
            "SMARTHYDRO_OFFLINE_THRESHOLD_SECONDS must be finite and positive"
        )
    return value
