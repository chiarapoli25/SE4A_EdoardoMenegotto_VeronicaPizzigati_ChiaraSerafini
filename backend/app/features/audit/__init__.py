"""Log di audit delle operazioni sensibili: chi ha fatto cosa e quando.

@details Distinto dallo storico di dominio gia' presente altrove (es.
`cultivations.created_at`/`confirmed_at`, `commands.result_message`): quello
storico serve a ricostruire lo stato di un singolo oggetto, questo modulo
risponde invece alla domanda "chi ha eseguito questa azione, con quale esito,
e quando" per qualunque operazione sensibile del sistema (login,
apertura/conferma/pausa/ripresa/conclusione di una coltivazione,
registrazione o modifica di un settore, pubblicazione di una ricetta),
indipendentemente dalla feature che l'ha generata.
"""
