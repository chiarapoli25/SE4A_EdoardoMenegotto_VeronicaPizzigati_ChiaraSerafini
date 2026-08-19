"""@file security.py
@brief Hashing password e JWT (HS256) per il login della dashboard.

@details Implementati con la sola libreria standard, senza dipendenze
aggiuntive (`bcrypt`/`passlib`/`pyjwt`): PBKDF2-HMAC-SHA256 per le password
(modulo `hashlib`, gia usato per scopi analoghi altrove nell'ecosistema
Python) e un sottoinsieme minimo, ma conforme alla RFC 7519, di JWT firmato
HS256. Il backend controlla sia la codifica sia la decodifica del token, ed
esige sempre l'algoritmo HS256: gli attacchi di tipo "alg confusion" (che
sfruttano un decoder che accetta piu algoritmi, incluso `none`) non si
applicano qui perche l'algoritmo non e mai letto dal token in ingresso.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

## @brief Algoritmo PBKDF2 usato per derivare l'hash della password.
_PBKDF2_ALGORITHM = "sha256"
## @brief Iterazioni PBKDF2: coerenti con le raccomandazioni OWASP 2023+ per SHA-256.
_PBKDF2_ITERATIONS = 600_000
## @brief Lunghezza in byte del salt casuale generato per ogni password.
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """@brief Deriva un hash salato della password, pronto per la persistenza.

    @return Stringa `pbkdf2_sha256$<iterazioni>$<salt-hex>$<hash-hex>`: il
        formato auto-descrittivo permette di aumentare `_PBKDF2_ITERATIONS`
        in futuro senza invalidare gli hash gia salvati.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """@brief Verifica una password in chiaro contro un hash persistito.

    @return `False` anche se `stored_hash` non e nel formato atteso, invece
        di sollevare un'eccezione: un valore corrotto o legacy non deve mai
        risultare in un login riuscito.
    """
    try:
        algorithm, iterations_raw, salt_hex, expected_hex = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)


def _b64url_encode(data: bytes) -> str:
    """Base64url senza padding, come richiesto dalla RFC 7515."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class TokenError(Exception):
    """@brief Segnala un token JWT mancante, malformato, scaduto o con firma invalida."""


def create_access_token(
    claims: dict, secret: str, expires_in_seconds: int
) -> tuple[str, datetime]:
    """@brief Crea un token JWT HS256 firmato con i claim indicati.

    @param claims Corpo del token (es. `sub`, `role`); `iat` ed `exp` sono
        aggiunti automaticamente e sovrascrivono eventuali omonimi.
    @param secret Chiave di firma HMAC (vedi `SMARTHYDRO_AUTH_SECRET`).
    @param expires_in_seconds Validita del token in secondi da adesso.
    @return Tupla `(token, scadenza)`.
    """
    now = int(time.time())
    payload = {
        **claims,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_segment = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode()
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    token = f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    return token, expires_at


def decode_access_token(token: str, secret: str) -> dict:
    """@brief Verifica firma e scadenza di un token e ne restituisce i claim.

    @throws TokenError Se il token e malformato, la firma non corrisponde o
        e scaduto.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed token")
    header_segment, payload_segment, signature_segment = parts
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    try:
        header = json.loads(_b64url_decode(header_segment))
        payload = json.loads(_b64url_decode(payload_segment))
        signature = _b64url_decode(signature_segment)
    except (ValueError, UnicodeDecodeError) as error:
        raise TokenError("malformed token") from error
    if header.get("alg") != "HS256":
        raise TokenError("unsupported algorithm")
    expected_signature = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise TokenError("invalid signature")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)) or time.time() >= expires_at:
        raise TokenError("expired token")
    return payload
