# Contratto di dominio della simulazione in terriccio

SmartHydro conserva il proprio nome, ma simula una coltivazione in terriccio:
non rappresenta un impianto idroponico. Ogni zona possiede un substrato con
capacita idrica, drenaggio e ritenzione differenti.

## Classificazione delle grandezze

| Grandezza | Ruolo | Unita | Sorgente usata dal controllo |
| --- | --- | --- | --- |
| Umidita del terriccio | Controllata | % di acqua disponibile | Stima calibrata della sonda capacitiva |
| Luce | Controllata | PPFD, umol/(m2 s) | Sensore PAR simulato |
| pH | Controllata | scala pH | Elettrodo simulato nell'acqua interstiziale |
| Disponibilita di N | Controllata tramite stima | mg/L nell'acqua della zona radicale | EC corretta e composizione nota al modello |
| Disponibilita di P | Controllata tramite stima | mg/L nell'acqua della zona radicale | EC corretta e composizione nota al modello |
| Disponibilita di K | Controllata tramite stima | mg/L nell'acqua della zona radicale | EC corretta e composizione nota al modello |
| Temperatura dell'aria | Solamente osservata | gradi Celsius | Sensore simulato |
| Umidita relativa dell'aria | Solamente osservata | % RH | Sensore simulato |
| EC apparente del terriccio | Misurata | mS/cm | Sonda resistiva/conducimetrica |
| EC dell'acqua nei pori | Derivata dalle sonde | mS/cm | EC apparente corretta con l'umidita capacitiva |
| Fertilizzante totale | Derivata dalle sonde | mg/L | Calibrazione empirica EC-concentrazione |

Temperatura e umidita relativa descrivono l'ambiente e influenzano le
dinamiche fisiche, ma non possiedono un setpoint, una Strategy o un attuatore
nel `RecipeControlSystem`.

N, P e K non dispongono di tre sensori fisici selettivi. La sonda resistiva
fornisce un'unica informazione aggregata sulla conducibilita ionica. Il sistema
la corregge usando l'umidita stimata dalla sonda capacitiva e converte la EC in
concentrazione totale mediante un coefficiente di calibrazione. Soltanto dopo
ripartisce il totale nelle quote N/P/K usando le proporzioni note al modello di
bilancio radicale: massa iniziale, dosi erogate, assorbimento e drenaggio.
Per questo le ricette mantengono `nitrogen_model`, `phosphorus_model` e
`potassium_model`: sono stime fuse sensore-modello, non sensori N/P/K.

La catena implementata e:

1. sonda capacitiva -> umidita del terriccio in percentuale;
2. elettrodi resistivi -> EC apparente del terriccio;
3. EC apparente + umidita -> EC stimata dell'acqua nei pori;
4. EC netta -> concentrazione totale stimata di fertilizzante;
5. concentrazione totale + composizione del bilancio -> stime N/P/K.

La conversione EC-concentrazione e un'approssimazione didattica configurabile:
anche sali non fertilizzanti e correttori di pH possono contribuire alla EC.

## Bilancio idrico e nutritivo

La pompa eroga una dose finita d'acqua direttamente al terriccio. Il modello:

1. trattiene una parte dell'acqua secondo il tipo di substrato;
2. rende l'acqua disponibile nella zona radicale;
3. sottrae l'assorbimento della pianta;
4. sottrae l'evaporazione;
5. rimuove l'eccesso per drenaggio.

Non esistono vasche di coltivazione, livelli d'acqua o circuiti di ricircolo.
I contenitori di N, P, K, pH+ e pH- contengono soltanto prodotti concentrati
dosati durante l'irrigazione; non costituiscono una soluzione idroponica.

L'EC fisica e N/P/K appartengono allo stato interno usato per simulare il
terriccio; il controllo vede invece le stime ottenute dalle sonde. L'irrigazione puo
diluire le concentrazioni, l'evaporazione puo concentrarle, il drenaggio
rimuove acqua e massa disciolta, l'assorbimento radicale consuma N/P/K e i
dosatori aggiungono massa senza trasformarsi in sensori.

## Compatibilita delle ricette

Il campo canonico di ogni controllore e `input_source`. L'Edge e il backend
accettano ancora il precedente campo `sensor` in ingresso, ma serializzano e
restituiscono soltanto `input_source`. Se entrambi sono presenti con valori
diversi, la ricetta viene rifiutata.
