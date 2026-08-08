"""Configurazione runtime del backend letta dall'ambiente."""

import math
import os
import re


DEFAULT_OFFLINE_THRESHOLD_SECONDS = 60.0
DEFAULT_EDGE_ID = "smarthydro-edge"


def default_edge_id() -> str:
    """Edge al quale il backend assegna i nuovi settori produttivi."""
    value = os.environ.get("SMARTHYDRO_DEFAULT_EDGE_ID", DEFAULT_EDGE_ID).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise RuntimeError(
            "SMARTHYDRO_DEFAULT_EDGE_ID must contain only letters, numbers, "
            "underscores and hyphens"
        )
    return value


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
