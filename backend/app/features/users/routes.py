"""@file routes.py
@brief Endpoint di autenticazione e di gestione degli account della dashboard.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from ...core.config import session_ttl_seconds
from ...core.database import get_db
from .dependencies import extract_bearer_token, get_current_user, require_admin
from .models import (
    BootstrapAdminRequest,
    LoginRequest,
    LoginResponse,
    SetupStatus,
    User,
    UserCreate,
)
from .repository import (
    SetupAlreadyComplete,
    UsernameConflict,
    bootstrap_admin,
    create_session,
    create_user,
    delete_session,
    get_stored_user,
    is_setup_required,
    list_users,
)
from .security import verify_password

## @brief Login, logout e identita' corrente: non richiede un account esistente.
auth_router = APIRouter(prefix="/auth", tags=["auth"])
## @brief Gestione degli account: ogni rotta e' riservata agli amministratori.
router = APIRouter(prefix="/users", tags=["users"])


@auth_router.post("/login", response_model=LoginResponse)
def login(
    credentials: LoginRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> LoginResponse:
    """@brief Verifica le credenziali e apre una nuova sessione.

    @details Il messaggio d'errore non distingue username inesistente da
    password errata, per non rivelare quali account esistono.
    """
    stored = get_stored_user(connection, credentials.username)
    if stored is None or not verify_password(
        credentials.password, stored.password_hash
    ):
        raise HTTPException(status_code=401, detail="invalid username or password")
    token, _ = create_session(connection, stored.username, session_ttl_seconds())
    return LoginResponse(token=token, user=stored.to_public())


@auth_router.post("/logout", status_code=204)
def logout(
    authorization: Annotated[str | None, Header()] = None,
    connection: sqlite3.Connection = Depends(get_db),
) -> Response:
    """@brief Chiude la sessione corrente; idempotente su un token gia' scaduto."""
    token = extract_bearer_token(authorization)
    delete_session(connection, token)
    return Response(status_code=204)


@auth_router.get("/me", response_model=User)
def read_current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """@brief Restituisce l'identita' associata al token di sessione corrente."""
    return user


@auth_router.get("/setup-required", response_model=SetupStatus)
def read_setup_required(
    connection: sqlite3.Connection = Depends(get_db),
) -> SetupStatus:
    """@brief Indica se la dashboard deve mostrare la creazione del primo account.

    @details Interrogato dal frontend prima di disegnare la schermata di
    accesso: se la tabella `users` e' ancora vuota, al suo posto va mostrato
    il form di creazione del primo amministratore.
    """
    return SetupStatus(setup_required=is_setup_required(connection))


@auth_router.post("/bootstrap-admin", response_model=LoginResponse, status_code=201)
def create_bootstrap_admin(
    payload: BootstrapAdminRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> LoginResponse:
    """@brief Crea il primo account (sempre amministratore) e apre subito una sessione.

    @details Funziona una sola volta: appena esiste un account, qualunque
    chiamata successiva viene rifiutata con 409, qualunque sia il contenuto
    inviato. Non e' quindi possibile usare questo endpoint per bypassare il
    pannello "Utenti" riservato agli amministratori gia' esistenti.
    """
    try:
        created = bootstrap_admin(connection, payload.username, payload.password)
    except SetupAlreadyComplete as error:
        raise HTTPException(status_code=409, detail="setup already completed") from error
    token, _ = create_session(connection, created.username, session_ttl_seconds())
    return LoginResponse(token=token, user=created)


@router.post("", response_model=User, status_code=201)
def register_user(
    payload: UserCreate,
    _: Annotated[User, Depends(require_admin)],
    connection: sqlite3.Connection = Depends(get_db),
) -> User:
    """@brief Crea un nuovo account amministratore o agronomo.

    @details Riservato agli amministratori: e' cosi' che un amministratore
    puo' creare altri amministratori o agronomi, senza che un agronomo possa
    farlo.
    """
    try:
        return create_user(
            connection,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            display_name=payload.display_name,
        )
    except UsernameConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=list[User])
def read_users(
    _: Annotated[User, Depends(require_admin)],
    connection: sqlite3.Connection = Depends(get_db),
) -> list[User]:
    """@brief Elenca gli account registrati; riservato agli amministratori."""
    return list_users(connection)
