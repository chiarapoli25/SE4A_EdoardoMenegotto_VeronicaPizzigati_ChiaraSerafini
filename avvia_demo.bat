@echo off
REM avvia_demo.bat — avvio automatico della demo SmartHydro, senza alcuna
REM interazione manuale.
REM
REM Fa, in ordine:
REM   1. Avvia il backend FastAPI (uvicorn) in una finestra separata.
REM   2. Attende che risponda su /health.
REM   3. Esegue demo\seed_users.py per creare (o aggiornare, se gia'
REM      esistono) gli account demo di autenticazione — rieseguibile senza
REM      effetti collaterali: serve perche' demo\seed_test_scenario.py, dal
REM      passo successivo, fa login come l'account amministratore di seed
REM      prima di popolare la demo, e su un database nuovo quell'account
REM      non esisterebbe ancora.
REM   4. Esegue demo\seed_test_scenario.py (POST dirette via API, nessun
REM      Edge C++ richiesto) per popolare zone, ricette e piante in
REM      quarantena in pochi secondi.
REM   5. Apre la dashboard nel browser predefinito.
REM
REM NON usa demo\seed_dev_data.py: quello script aspetta un INVIO manuale
REM per l'avvio dell'Edge C++ reale (test rigoroso, non adatto a un avvio
REM automatico). Deve restare cosi'.
REM
REM La finestra del backend resta aperta al termine: chiudila quando hai
REM finito, oppure lascia questa demo in esecuzione.

setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PYTHON=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON=%ROOT%.venv\Scripts\python.exe"

echo [avvia_demo] Avvio il backend in una nuova finestra...
start "SmartHydro Backend" cmd /k "cd /d "%ROOT%" && "%PYTHON%" -m uvicorn backend.app.main:app"

echo [avvia_demo] Attendo che il backend risponda su http://127.0.0.1:8000/health ...
set /a ATTEMPTS=0
:wait_for_backend
set /a ATTEMPTS+=1
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8000/health -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %ERRORLEVEL% EQU 0 goto backend_ready
if %ATTEMPTS% GEQ 30 goto backend_timeout
timeout /t 1 /nobreak > nul
goto wait_for_backend

:backend_timeout
echo [avvia_demo] ERRORE: il backend non ha risposto entro 30 secondi.
echo [avvia_demo] Controlla la finestra "SmartHydro Backend" per l'errore.
pause
exit /b 1

:backend_ready
echo [avvia_demo] Backend pronto.

echo [avvia_demo] Creo/aggiorno gli account demo con demo\seed_users.py...
"%PYTHON%" "%ROOT%demo\seed_users.py"
if %ERRORLEVEL% NEQ 0 (
    echo [avvia_demo] ERRORE: seed_users.py e' terminato con un errore.
    pause
    exit /b 1
)

echo [avvia_demo] Popolo la demo con demo\seed_test_scenario.py (test RAPIDO, nessun Edge)...
"%PYTHON%" "%ROOT%demo\seed_test_scenario.py"
if %ERRORLEVEL% NEQ 0 (
    echo [avvia_demo] ERRORE: seed_test_scenario.py e' terminato con un errore.
    pause
    exit /b 1
)

echo [avvia_demo] Apro la dashboard nel browser...
start "" "http://127.0.0.1:8000/dashboard/"

echo [avvia_demo] Fatto. Il backend resta in esecuzione nella finestra "SmartHydro Backend".
endlocal
