"""@file repository.py
@brief Persistenza SQLite degli account della dashboard e delle sessioni.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import User, UserRole
from .security import generate_session_token, hash_password


class UsernameConflict(Exception):
    """@brief Segnala un username gia' registrato."""


class SetupAlreadyComplete(Exception):
    """@brief Segnala che la tabella users non e' piu' vuota.

    @details Sollevata da `bootstrap_admin` quando esiste gia' almeno un
    account: da quel momento in poi l'endpoint di primo avvio deve restare
    permanentemente inutilizzabile.
    """


@dataclass
class StoredUser:
    """@brief Riga `users` completa, incluso l'hash: non lasciare la feature."""

    username: str
    display_name: str
    role: UserRole
    password_hash: str
    created_at: str

    def to_public(self) -> User:
        return User(
            username=self.username,
            display_name=self.display_name,
            role=self.role,
            created_at=self.created_at,
        )


def _stored_user_from_row(row: tuple) -> StoredUser:
    return StoredUser(
        username=row[0],
        display_name=row[1],
        role=UserRole(row[2]),
        password_hash=row[3],
        created_at=row[4],
    )


def get_stored_user(
    connection: sqlite3.Connection,
    username: str,
) -> StoredUser | None:
    """@brief Recupera un account completo di hash, per la verifica del login."""
    row = connection.execute(
        """
        SELECT username, display_name, role, password_hash, created_at
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()
    return None if row is None else _stored_user_from_row(row)


def create_user(
    connection: sqlite3.Connection,
    username: str,
    password: str,
    role: UserRole,
    display_name: str | None,
) -> User:
    """@brief Crea un account con la password gia' sottoposta a hashing.

    @throws UsernameConflict Se l'username e' gia' occupato.
    """
    now = datetime.now(timezone.utc)
    try:
        connection.execute(
            """
            INSERT INTO users (username, display_name, role, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                display_name or username,
                role.value,
                hash_password(password),
                now.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        raise UsernameConflict(f"username {username!r} already exists") from error
    connection.commit()
    stored = get_stored_user(connection, username)
    assert stored is not None
    return stored.to_public()


def is_setup_required(connection: sqlite3.Connection) -> bool:
    """@brief Vero se la tabella `users` non contiene ancora nessun account."""
    (count,) = connection.execute("SELECT COUNT(*) FROM users").fetchone()
    return count == 0


def bootstrap_admin(
    connection: sqlite3.Connection,
    username: str,
    password: str,
) -> User:
    """@brief Crea il primissimo account, sempre amministratore.

    @details L'inserimento e' un'unica istruzione `INSERT ... SELECT ...
    WHERE NOT EXISTS`: il controllo "la tabella e' vuota" e la scrittura
    avvengono all'interno della stessa istruzione SQL. SQLite serializza le
    scritture fra connessioni diverse (anche da processi diversi sullo
    stesso file), quindi non esiste una finestra in cui due richieste
    concorrenti possano superare entrambe il controllo: la seconda vede
    sempre la riga inserita dalla prima. E' cosi' impossibile creare un
    secondo amministratore chiamando questa funzione una seconda volta,
    indipendentemente da username o password inviati.

    @throws SetupAlreadyComplete Se esiste gia' almeno un account.
    """
    now = datetime.now(timezone.utc)
    cursor = connection.execute(
        """
        INSERT INTO users (username, display_name, role, password_hash, created_at)
        SELECT ?, ?, ?, ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM users)
        """,
        (
            username,
            username,
            UserRole.ADMIN.value,
            hash_password(password),
            now.isoformat(),
        ),
    )
    if cursor.rowcount == 0:
        connection.rollback()
        raise SetupAlreadyComplete("an account already exists")
    connection.commit()
    stored = get_stored_user(connection, username)
    assert stored is not None
    return stored.to_public()


def list_users(connection: sqlite3.Connection) -> list[User]:
    """@brief Elenca gli account registrati, senza alcun hash di password."""
    rows = connection.execute(
        """
        SELECT username, display_name, role, password_hash, created_at
        FROM users
        ORDER BY created_at, username
        """
    ).fetchall()
    return [_stored_user_from_row(row).to_public() for row in rows]


def create_session(
    connection: sqlite3.Connection,
    username: str,
    ttl_seconds: float,
) -> tuple[str, str]:
    """@brief Apre una nuova sessione e restituisce `(token, expires_at)`."""
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    connection.execute(
        """
        INSERT INTO sessions (token, username, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, username, now.isoformat(), expires_at.isoformat()),
    )
    connection.commit()
    return token, expires_at.isoformat()


def get_session_user(
    connection: sqlite3.Connection,
    token: str,
) -> User | None:
    """@brief Risolve un token di sessione non scaduto nel relativo account."""
    row = connection.execute(
        """
        SELECT u.username, u.display_name, u.role, u.password_hash, u.created_at,
               s.expires_at
        FROM sessions AS s
        JOIN users AS u ON u.username = s.username
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(row[5])
    if expires_at <= datetime.now(timezone.utc):
        return None
    return _stored_user_from_row(row[:5]).to_public()


def delete_session(connection: sqlite3.Connection, token: str) -> None:
    """@brief Chiude una sessione; idempotente se il token non esiste piu'."""
    connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
    connection.commit()


def seed_default_users(connection: sqlite3.Connection) -> None:
    """@brief Crea gli account dimostrativi se la tabella e' ancora vuota.

    @details Gira ad ogni avvio come il bootstrap del catalogo ricette, ma non
    tocca piu' nulla appena esiste almeno un account: cosi' una password
    cambiata dopo l'installazione non viene mai resettata da un riavvio.
    """
    (count,) = connection.execute("SELECT COUNT(*) FROM users").fetchone()
    if count > 0:
        return
    for username, display_name, role in (
        ("admin", "Admin", UserRole.ADMIN),
        ("agronomo", "Agronomo", UserRole.AGRONOMO),
    ):
        create_user(
            connection,
            username=username,
            password="pass123",
            role=role,
            display_name=display_name,
        )
