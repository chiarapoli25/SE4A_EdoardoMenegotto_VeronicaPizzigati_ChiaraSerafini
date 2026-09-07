"""Persistenza SQLite di utenti e sessioni della dashboard."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone

import bcrypt

from .models import AuthenticatedUser, Role, User

## @brief Lunghezza in byte del token di sessione opaco (prima della
## codifica URL-safe base64, quindi la stringa risultante e' piu' lunga).
SESSION_TOKEN_BYTES = 32


class UserAlreadyExists(Exception):
    """@brief Segnala un tentativo di creare un username gia' registrato."""


def hash_password(password: str) -> str:
    """@brief Calcola l'hash bcrypt di una password in chiaro."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """@brief Verifica una password in chiaro contro un hash bcrypt salvato."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Hash malformato/di formato inatteso: mai trattarlo come valido.
        return False


def _user_from_row(row: tuple) -> User:
    return User(username=row[0], role=Role(row[1]), created_at=row[2])


def create_user(
    connection: sqlite3.Connection,
    username: str,
    password: str,
    role: Role,
    *,
    commit: bool = True,
) -> User:
    """@brief Registra un nuovo utente con password hashata.

    @throws UserAlreadyExists Se l'username e' gia' presente.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        connection.execute(
            """
            INSERT INTO users (username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (username, hash_password(password), role.value, created_at),
        )
    except sqlite3.IntegrityError as error:
        raise UserAlreadyExists(f"username {username!r} already exists") from error
    if commit:
        connection.commit()
    return User(username=username, role=role, created_at=created_at)


def get_user(connection: sqlite3.Connection, username: str) -> User | None:
    """@brief Recupera un utente registrato (senza l'hash della password)."""
    row = connection.execute(
        "SELECT username, role, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return None if row is None else _user_from_row(row)


def _get_password_hash(connection: sqlite3.Connection, username: str) -> str | None:
    row = connection.execute(
        "SELECT password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return None if row is None else row[0]


def authenticate(
    connection: sqlite3.Connection, username: str, password: str
) -> User | None:
    """@brief Verifica username+password; None se le credenziali non sono valide.

    Il confronto avviene sempre tramite bcrypt.checkpw anche quando
    l'utente non esiste, per non rivelare l'esistenza di uno username
    tramite differenze di tempo di risposta.
    """
    password_hash = _get_password_hash(connection, username)
    if password_hash is None:
        # Hash 'esca' di costo comparabile, cosi' il tempo di verifica di uno
        # username inesistente resta simile a quello di uno username reale.
        bcrypt.checkpw(password.encode("utf-8"), bcrypt.hashpw(b"", bcrypt.gensalt()))
        return None
    if not verify_password(password, password_hash):
        return None
    return get_user(connection, username)


def create_session(
    connection: sqlite3.Connection, username: str, *, commit: bool = True
) -> str:
    """@brief Crea una sessione per l'utente e restituisce il token opaco."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    created_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO sessions (token, username, created_at, expires_at)
        VALUES (?, ?, ?, NULL)
        """,
        (token, username, created_at),
    )
    if commit:
        connection.commit()
    return token


def resolve_session(
    connection: sqlite3.Connection, token: str
) -> AuthenticatedUser | None:
    """@brief Risolve un token verso l'utente proprietario.

    Restituisce None se il token non esiste, se l'utente e' stato rimosso,
    o se la sessione e' scaduta (`expires_at` nel passato).
    """
    row = connection.execute(
        """
        SELECT sessions.expires_at, users.username, users.role
        FROM sessions
        JOIN users ON users.username = sessions.username
        WHERE sessions.token = ?
        """,
        (token,),
    ).fetchone()
    if row is None:
        return None
    expires_at, username, role = row
    if expires_at is not None:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry <= datetime.now(timezone.utc):
            return None
    return AuthenticatedUser(username=username, role=Role(role))
