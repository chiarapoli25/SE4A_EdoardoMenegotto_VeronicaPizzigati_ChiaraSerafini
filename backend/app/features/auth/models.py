"""Contratti HTTP e domain model dell'autenticazione della dashboard."""

from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field


class Role(str, Enum):
    """@brief Ruolo applicativo di un utente della dashboard."""

    AGRONOMO = "agronomo"
    AMMINISTRATORE = "amministratore"


class LoginRequest(BaseModel):
    """@brief Credenziali inviate a POST /auth/login."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """@brief Esito di un login riuscito: token opaco e ruolo dell'utente."""

    token: str
    username: str
    role: Role


class AuthenticatedUser(BaseModel):
    """@brief Identita' risolta da un token di sessione valido."""

    username: str
    role: Role


class User(BaseModel):
    """@brief Utente registrato (senza l'hash della password)."""

    username: str
    role: Role
    created_at: AwareDatetime
