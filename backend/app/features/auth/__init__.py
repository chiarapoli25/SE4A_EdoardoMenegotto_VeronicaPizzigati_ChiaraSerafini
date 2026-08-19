"""Autenticazione utente della dashboard: utenti, ruoli, login JWT.

@details Distinta dal token Edge (`core.security.require_api_token`), che
resta una credenziale tecnica macchina-a-macchina condivisa da tutti gli
Edge. Questo modulo identifica invece la singola persona (agronomo,
operatore, amministratore) che opera dalla dashboard, cosi che
`created_by`/`confirmed_by` sulle coltivazioni riportino un'identita reale
anziche un valore libero fornito dal chiamante.
"""
