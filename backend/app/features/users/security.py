"""@file security.py
@brief Hashing delle password e generazione dei token di sessione.

@details Usa esclusivamente la libreria standard (PBKDF2-HMAC-SHA256 via
`hashlib`), coerentemente con `core.security`, che evita gia' dipendenze
esterne per il Bearer token dell'Edge.
"""

import hashlib
import hmac
import secrets

## @brief Iterazioni PBKDF2: costo ragionevole per un progetto didattico.
_PBKDF2_ITERATIONS = 200_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """@brief Deriva un hash salato nel formato `algoritmo$iterazioni$salt$hash`.

    @param password Password in chiaro da non conservare mai cosi' com'e'.
    @return Stringa autocontenuta, pronta per essere salvata su SQLite.
    """
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"{_ALGORITHM}${_PBKDF2_ITERATIONS}${salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """@brief Verifica una password rispetto a un hash prodotto da `hash_password`.

    @param password Password in chiaro fornita al login.
    @param stored_hash Hash salvato per l'account.
    @return `True` se la password corrisponde, `False` altrimenti o in caso di
        formato non riconosciuto.
    """
    try:
        algorithm, iterations_text, salt, expected_hex = stored_hash.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_text)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return hmac.compare_digest(derived.hex(), expected_hex)


def generate_session_token() -> str:
    """@brief Genera un token di sessione opaco, imprevedibile e URL-safe."""
    return secrets.token_urlsafe(32)
