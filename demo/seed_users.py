# Seed degli account della dashboard — nessuna registrazione self-service.
# Crea (o aggiorna la password di) due utenze di esempio, una per ruolo.
# Le password in chiaro vivono SOLO in questo file: qualunque altro script
# demo/ che debba autenticarsi le importa da qui (ADMIN_USERNAME /
# ADMIN_PASSWORD), non le ripete.

"""Seed degli account di autenticazione della dashboard SmartHydro.

USO:

    python demo/seed_users.py

A differenza degli altri script in demo/ (che parlano col backend solo via
HTTP), questo script scrive DIRETTAMENTE nel database SQLite: non esiste —
per scelta — nessun endpoint HTTP di registrazione self-service, quindi non
c'e' un modo "via API" per creare il primo utente. Puoi eseguirlo col
backend acceso o spento: usa lo stesso DEFAULT_DATABASE_PATH del backend
(rispetta SMARTHYDRO_DATABASE_PATH se impostata), quindi finche' punta allo
stesso file .db le utenze saranno visibili al backend in esecuzione senza
bisogno di riavviarlo.

Rieseguibile senza effetti collaterali: se un username esiste gia', la sua
password/ruolo vengono aggiornati agli stessi valori qui sotto invece di
fallire con un errore (comodo per rigenerare l'hash se cambi le password in
questo file).

Le password NON sono mai salvate in chiaro nel database (solo il loro hash
pbkdf2_sha256, vedi backend/app/features/users/security.py). Le uniche due
copie in chiaro che dovrebbero esistere sono qui sotto e nel riepilogo
stampato a fine esecuzione — non incollarle altrove nel codice sorgente.

Nota: init_db() non crea piu' automaticamente alcun account all'avvio del
backend (era un rischio di sicurezza avere admin/pass123 cablato nel codice
di startup) — questo script e' quindi ora l'UNICO modo per popolare un
database vuoto di un primo account amministratore.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# A differenza degli altri script in demo/, questo importa direttamente il
# pacchetto backend (nessun endpoint HTTP di registrazione, vedi sopra):
# garantisce che la radice del repository sia su sys.path indipendentemente
# dalla cwd da cui viene lanciato (`python demo/seed_users.py` dalla radice,
# oppure `python seed_users.py` da dentro demo/).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.core.database import DEFAULT_DATABASE_PATH, get_connection, init_db
from backend.app.features.users.models import UserRole
from backend.app.features.users.repository import UsernameConflict, create_user
from backend.app.features.users.security import hash_password

# NOTA: queste sono le UNICHE due righe di questo repository in cui una
# password compare in chiaro, a parte l'output di questo script. Chi
# consuma queste credenziali (demo/seed_dev_data.py, demo/seed_test_scenario.py,
# la persona che testa la dashboard) le importa da qui.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "V3IkzqpWRh2Hfc"
AGRONOMO_USERNAME = "agronomo"
AGRONOMO_PASSWORD = "N4MKdI0jRIB6i5"

SEED_ACCOUNTS = (
    (ADMIN_USERNAME, ADMIN_PASSWORD, UserRole.ADMIN, "Amministratore"),
    (AGRONOMO_USERNAME, AGRONOMO_PASSWORD, UserRole.AGRONOMO, "Agronomo"),
)


def _upsert_user(
    connection: sqlite3.Connection,
    username: str,
    password: str,
    role: UserRole,
    display_name: str,
) -> None:
    try:
        create_user(connection, username, password, role, display_name)
        print(f"[seed] utente creato: {username} (ruolo={role.value})")
    except UsernameConflict:
        connection.execute(
            "UPDATE users SET password_hash = ?, role = ?, display_name = ? WHERE username = ?",
            (hash_password(password), role.value, display_name, username),
        )
        connection.commit()
        print(f"[seed] utente gia' esistente, password/ruolo aggiornati: {username} (ruolo={role.value})")


def main() -> None:
    connection = get_connection()
    try:
        init_db(connection)
        for username, password, role, display_name in SEED_ACCOUNTS:
            _upsert_user(connection, username, password, role, display_name)
    finally:
        connection.close()

    print()
    print("=" * 78)
    print(f"[seed] database: {DEFAULT_DATABASE_PATH}")
    print("[seed] account pronti per il login su POST /auth/login:")
    print(f"[seed]   amministratore -> username={ADMIN_USERNAME!r} password={ADMIN_PASSWORD!r}")
    print(f"[seed]   agronomo       -> username={AGRONOMO_USERNAME!r} password={AGRONOMO_PASSWORD!r}")
    print("=" * 78)


if __name__ == "__main__":
    main()
