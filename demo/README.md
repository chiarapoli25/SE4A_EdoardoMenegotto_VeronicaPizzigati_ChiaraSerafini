# Demo end-to-end Edge–backend

`run_end_to_end.py` avvia un backend FastAPI e il servizio Edge C++ reali in
un ambiente temporaneo. Lo scenario dichiarativo si trova in
`end_to_end_scenario.json`.

Esecuzione dalla radice del repository, dopo aver creato l'ambiente Python e
installato `backend/requirements.txt`:

```bash
.venv/bin/python demo/run_end_to_end.py
```

Il comando configura e compila l'Edge, sceglie una porta libera e verifica:

1. provisioning di due zone con ricette diverse;
2. attivazione e telemetria di entrambe;
3. cambio Strategy e conferma della nuova configurazione;
4. avanzamento manuale della fase;
5. fault temporaneo, `FaultDetected`, `Degraded` e recovery in `Nominal`;
6. rimozione di una zona, mancata esecuzione dei suoi nuovi comandi e
   prosecuzione dell'altra zona.

Il database, la cache delle assegnazioni, l'outbox e i log sono isolati in una
directory temporanea. Vengono eliminati dopo un esito positivo. Per conservarli:

```bash
.venv/bin/python demo/run_end_to_end.py --keep-artifacts
```

Per riusare un eseguibile Edge già compilato:

```bash
.venv/bin/python demo/run_end_to_end.py --skip-build
```

Non è necessaria una procedura manuale di reset: ogni esecuzione usa un nuovo
database e una nuova outbox. Un esito diverso da zero indica quale verifica è
fallita e conserva automaticamente gli artefatti con le code finali dei log.
