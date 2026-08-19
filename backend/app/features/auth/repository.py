"""@file repository.py
@brief Persistenza SQLite degli utenti della dashboard.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from .models import User, UserCreate, UserRole
from .security import hash_password, verify_password

_COLUMNS = "id, username, password_hash, role, full_name, is_active, created_at"


class UsernameConflict(Exception):
    """@brief Segnala uno `username` gia registrato."""


def _user_from_row(row: tuple) -> User:
    return User(
        id=row[0],
        username=row[1],
        role=UserRole(row[3]),
        full_name=row[4],
        is_active=bool(row[5]),
        created_at=row[6],
    )


def create_user(connection: sqlite3.Connection, user: UserCreate) -> User:
    """@brief Crea un nuovo utente con la password gia derivata in hash.

    @throws UsernameConflict Se `username` e gia registrato.
    """
    user_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc)
    try:
        connection.execute(
            """
            INSERT INTO users (
                id, username, password_hash, role, full_name, is_active,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                user_id,
                user.username,
                hash_password(user.password),
                user.role.value,
                user.full_name,
                created_at.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise UsernameConflict(
            f"username {user.username!r} is already registered"
        ) from error
    connection.commit()
    return User(
        id=user_id,
        username=user.username,
        role=user.role,
        full_name=user.full_name,
        is_active=True,
        created_at=created_at,
    )


def get_user(connection: sqlite3.Connection, user_id: str) -> User | None:
    """@brief Recupera un utente tramite identificativo interno."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return None if row is None else _user_from_row(row)


def get_user_by_username(
    connection: sqlite3.Connection, username: str
) -> User | None:
    """@brief Recupera un utente tramite nome utente."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM users WHERE username = ?", (username,)
    ).fetchone()
    return None if row is None else _user_from_row(row)


def authenticate_user(
    connection: sqlite3.Connection, username: str, password: str
) -> User | None:
    """@brief Verifica le credenziali e restituisce l'utente se valide.

    @return `None` se l'utente non esiste, e disattivato, o la password non
        corrisponde. Non distingue questi casi nella risposta: evita di
        rivelare se uno `username` esiste (enumerazione degli account).
    """
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    user = _user_from_row(row)
    password_hash = row[2]
    if not user.is_active or not verify_password(password, password_hash):
        return None
    return user


def list_users(connection: sqlite3.Connection) -> list[User]:
    """@brief Elenca tutti gli utenti registrati, ordinati per nome utente."""
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM users ORDER BY username"
    ).fetchall()
    return [_user_from_row(row) for row in rows]
