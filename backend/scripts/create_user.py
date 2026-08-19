#!/usr/bin/env python3
"""@file create_user.py
@brief Crea un utente della dashboard direttamente sul database SQLite.

@details Non esiste un endpoint HTTP di registrazione: un account nasce
sempre da questo script, eseguito da chi amministra il backend, cosi che
nessuna richiesta pubblica possa auto-assegnarsi un ruolo. Va eseguito dalla
cartella che contiene `backend/` (la radice del repository), puntato allo
stesso database del backend in esecuzione (`SMARTHYDRO_DATABASE_PATH`, se
impostata).

Esempi:
    python -m backend.scripts.create_user --username mario.rossi --role agronomist
    python -m backend.scripts.create_user --username op1 --role operator \\
        --full-name "Operatore turno notte"
"""

import argparse
import getpass
import sys

from backend.app.core.database import get_connection, init_db
from backend.app.features.auth.models import UserCreate, UserRole
from backend.app.features.auth.repository import UsernameConflict, create_user


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="nome utente per il login")
    parser.add_argument(
        "--role",
        required=True,
        choices=[role.value for role in UserRole],
        help="ruolo assegnato all'utente",
    )
    parser.add_argument(
        "--full-name", default=None, help="nome leggibile mostrato nella dashboard"
    )
    parser.add_argument(
        "--password",
        default=None,
        help="password in chiaro (sconsigliato: se omessa viene richiesta "
        "in modo interattivo senza restare nella cronologia della shell)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    password = args.password or getpass.getpass("Password: ")
    if len(password) < 8:
        print("la password deve avere almeno 8 caratteri", file=sys.stderr)
        return 1

    connection = get_connection()
    try:
        init_db(connection)
        user = create_user(
            connection,
            UserCreate(
                username=args.username,
                password=password,
                role=UserRole(args.role),
                full_name=args.full_name,
            ),
        )
    except UsernameConflict as error:
        print(f"errore: {error}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    print(f"utente creato: {user.username} (ruolo {user.role.value}, id {user.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
