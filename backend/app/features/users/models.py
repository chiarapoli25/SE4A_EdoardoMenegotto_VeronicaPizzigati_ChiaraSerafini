"""@file models.py
@brief Modelli Pydantic degli account della dashboard e della sessione.
"""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field

## @brief Vincolo comune per username e ruolo leggibile dai form della dashboard.
_USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class UserRole(str, Enum):
    """@brief Ruolo applicativo di un account della dashboard."""

    ## @brief Puo' gestire gli account, inclusa la creazione di altri account.
    ADMIN = "admin"
    ## @brief Definisce ricette e coltivazioni ma non gestisce gli account.
    AGRONOMO = "agronomo"


class UserCreate(BaseModel):
    """@brief Dati necessari per creare un nuovo account."""

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=_USERNAME_PATTERN,
    )
    password: str = Field(min_length=4, max_length=200)
    role: UserRole
    display_name: str | None = Field(default=None, min_length=1, max_length=100)


class User(BaseModel):
    """@brief Account persistito, senza alcun dato segreto."""

    username: str
    display_name: str
    role: UserRole
    created_at: AwareDatetime


class LoginRequest(BaseModel):
    """@brief Credenziali inviate dalla schermata di accesso."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    """@brief Esito di un accesso riuscito."""

    token: str
    user: User
