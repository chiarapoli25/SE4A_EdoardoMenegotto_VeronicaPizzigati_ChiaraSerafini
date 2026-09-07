"""Autenticazione e autorizzazione della dashboard (utenti, sessioni, ruoli).

Meccanismo separato dal Bearer SMARTHYDRO_API_TOKEN usato dall'Edge (vedi
`backend/app/core/security.py`): quello autentica l'Edge verso il backend,
questo autentica un operatore umano verso la dashboard.
"""
