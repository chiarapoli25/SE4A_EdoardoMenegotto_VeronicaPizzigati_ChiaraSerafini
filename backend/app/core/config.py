"""Configurazione runtime del backend letta dall'ambiente."""

import math
import os


DEFAULT_OFFLINE_THRESHOLD_SECONDS = 60.0

## @brief Validita predefinita di un token di login: un turno lavorativo.
DEFAULT_AUTH_TOKEN_TTL_SECONDS = 8 * 60 * 60

## @brief Chiave di firma usata solo quando `SMARTHYDRO_AUTH_SECRET` non e
## configurata (sviluppo/test locali). Non deve mai raggiungere un ambiente
## esposto: un token firmato con una chiave nota pubblicamente e falsificabile.
_DEV_ONLY_AUTH_SECRET = "smarthydro-dev-only-secret-do-not-use-in-production"


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


def auth_secret() -> str:
    """@brief Chiave di firma HMAC dei token di login della dashboard.

    @details In produzione va sempre impostata esplicitamente
    (`SMARTHYDRO_AUTH_SECRET`, una stringa lunga e casuale): senza, il
    fallback di sviluppo e noto pubblicamente in questo repository e chiunque
    potrebbe forgiare token validi.
    """
    return os.environ.get("SMARTHYDRO_AUTH_SECRET", _DEV_ONLY_AUTH_SECRET)


def auth_token_ttl_seconds() -> int:
    """@brief Validita, in secondi, di un token di login appena emesso."""
    raw_value = os.environ.get(
        "SMARTHYDRO_AUTH_TOKEN_TTL_SECONDS",
        str(DEFAULT_AUTH_TOKEN_TTL_SECONDS),
    )
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            "SMARTHYDRO_AUTH_TOKEN_TTL_SECONDS must be an integer"
        ) from error
    if value <= 0:
        raise RuntimeError(
            "SMARTHYDRO_AUTH_TOKEN_TTL_SECONDS must be positive"
        )
    return value
