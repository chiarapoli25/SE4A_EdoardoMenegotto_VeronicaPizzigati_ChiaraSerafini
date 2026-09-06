"""Configurazione runtime del backend letta dall'ambiente."""

import math
import os
import re


DEFAULT_OFFLINE_THRESHOLD_SECONDS = 60.0
DEFAULT_EDGE_ID = "smarthydro-edge"
## @brief Durata predefinita di una sessione della dashboard: 12 ore.
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60


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


def session_ttl_seconds() -> float:
    """Durata di validita' di un token di sessione della dashboard."""
    raw_value = os.environ.get(
        "SMARTHYDRO_SESSION_TTL_SECONDS",
        str(DEFAULT_SESSION_TTL_SECONDS),
    )
    try:
        value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "SMARTHYDRO_SESSION_TTL_SECONDS must be numeric"
        ) from error
    if not math.isfinite(value) or value <= 0.0:
        raise RuntimeError(
            "SMARTHYDRO_SESSION_TTL_SECONDS must be finite and positive"
        )
    return value
