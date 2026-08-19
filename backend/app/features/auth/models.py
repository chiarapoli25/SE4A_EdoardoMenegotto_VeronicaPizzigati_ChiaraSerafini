"""@file models.py
@brief Modelli Pydantic di utenti, login e token della dashboard.
"""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field


class UserRole(str, Enum):
    """@brief Ruolo operativo di un utente della dashboard.

    @details Determina quali operazioni sulle coltivazioni sono ammesse:
    `AGRONOMIST` e `ADMIN` possono aprire e confermare una coltivazione
    (impegnano il settore e l'Edge); tutti e tre i ruoli, incluso
    `OPERATOR`, possono pausare, riprendere o concludere un ciclo gia
    avviato. `ADMIN` non ha oggi permessi aggiuntivi rispetto ad
    `AGRONOMIST` sulle coltivazioni: la distinzione e riservata a future
    operazioni amministrative (gestione utenti, settori).
    """

    ## @brief Puo creare e confermare coltivazioni, gestire le ricette.
    AGRONOMIST = "agronomist"
    ## @brief Puo operare un ciclo gia avviato (pausa, ripresa, conclusione).
    OPERATOR = "operator"
    ## @brief Permessi completi, incluse le future operazioni amministrative.
    ADMIN = "admin"


## @brief Ruoli ammessi ad aprire e confermare una coltivazione.
CULTIVATION_WRITE_ROLES = frozenset({UserRole.AGRONOMIST, UserRole.ADMIN})


class UserCreate(BaseModel):
    """@brief Dati necessari per creare un utente (solo via script di seed).

    @details Non esiste un endpoint HTTP di self-registration nemmeno per
    l'ambito minimale: un nuovo account nasce da uno script eseguito da chi
    amministra il backend (`backend/scripts/create_user.py`), non da una
    richiesta pubblica.
    """

    ## @brief Nome utente univoco usato per il login.
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    ## @brief Password in chiaro, mai persistita: viene subito derivata in hash.
    password: str = Field(min_length=8, max_length=200)
    ## @brief Ruolo assegnato all'utente.
    role: UserRole
    ## @brief Nome leggibile mostrato nella dashboard, se diverso da `username`.
    full_name: str | None = Field(default=None, max_length=100)


class User(BaseModel):
    """@brief Utente persistito, senza l'hash della password."""

    ## @brief Identificativo univoco interno.
    id: str
    ## @brief Nome utente univoco usato per il login.
    username: str
    ## @brief Ruolo operativo dell'utente.
    role: UserRole
    ## @brief Nome leggibile, se impostato in fase di creazione.
    full_name: str | None = None
    ## @brief `False` se l'account e stato disabilitato senza eliminarlo.
    is_active: bool = True
    ## @brief Timestamp UTC di creazione dell'account.
    created_at: AwareDatetime


class LoginRequest(BaseModel):
    """@brief Credenziali inviate a `POST /auth/login`."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    """@brief Access token restituito da un login riuscito."""

    ## @brief Token JWT firmato dal backend (HS256).
    access_token: str
    ## @brief Schema del token, sempre `bearer` (RFC 6750).
    token_type: str = "bearer"
    ## @brief Timestamp UTC di scadenza del token.
    expires_at: AwareDatetime
    ## @brief Utente autenticato, per popolare subito la dashboard.
    user: User
