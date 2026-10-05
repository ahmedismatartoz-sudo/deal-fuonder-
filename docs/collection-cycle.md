# Base iniziale e ciclo giornaliero

La prima raccolta importa la base ampia una sola volta. I task quotidiani cercano
gli annunci nuovi; task `refresh` separati ricontrollano gruppi di annunci già
noti. Lo scanner prezzi consulta tutti i confronti pertinenti dell'archivio,
indipendentemente da lotto o giorno di raccolta. Non esiste un limite di 100
annunci per la base o per i confronti. La singola pagina è limitata per consentire
checkpoint e transazioni brevi; non limita il numero totale di pagine.

## Collegamento Apify

1. Creare task distinti per base iniziale, ricerca quotidiana e refresh mirato.
   I filtri geografici devono individuare l'Italia; separare le zone per copertura.
   Configurare sul task iniziale anche prezzi superiori a 50.000 €: possono
   costituire confronti per acquisti entro il budget. Verificare limiti e costi
   dell'Actor nel proprio account prima di avviare la raccolta ampia.
2. Per i task quotidiani, usare ordinamento per data e una finestra sovrapposta
   di almeno 48 ore, ove l'Actor lo supporta. Deduplicazione su fonte/ID nel database.
   Il filtro per creazione non scopre da solo modifiche ai vecchi annunci;
   configurare refresh con ricontrollo dei link esistenti, senza modalità new-only.
3. Programmare i task quotidiani in Apify prima del ciclo Render, con un margine
   sufficiente per completarli. L'adapter importa soltanto run SUCCEEDED e usa
   richieste GET: non avvia né addebita nuove esecuzioni di scraping.
4. Configurare APIFY_API_TOKEN nei segreti del servizio. Configurazione task in
   DEAL_FINDER_COLLECTION_CONFIG (JSON) o file passato a --config. Mai inserire
   token nei file di configurazione o nel repository.

`examples/apify_config.json` è uno **scheletro da verificare su un export reale**,
non una configurazione pronta del proprio account. Gli Actor cambiano schema:
confermare percorsi, valuta EUR, unità km, enum e disponibilità sui dati reali.
Non mappare miglia direttamente su mileage_km. Per ogni campo si può indicare
un percorso `vehicle.model`, oppure un oggetto path/values che traduce
esplicitamente i valori del provider. Il vincolo `require` impedisce di leggere
un importo se la valuta non è EUR o un chilometraggio se l'unità non è KM.
Modificare entrambi i task con la stessa
mappatura verificata. La mappa quotidiana ridotta nell'esempio archivia soltanto
gli originali finché non viene completata.

Non assegniamo automaticamente `price_kind=total`, `condition=undamaged`,
generazione, allestimento o identità verificata. Se il provider non distingue
rate/anticipi dal totale oppure manca una specifica, servono normalizzazione
documentata e nuovi eventi arricchiti. I record restano nell'archivio; non
entrano in un benchmark preciso con dati inventati. I campi originali, incluse
foto e descrizioni non ancora mappate, sono conservati in payload.original.

L'ora di osservazione è l'inizio del run, come limite conservativo di freschezza,
non la data di creazione dell'annuncio. I task devono raccogliere dati attuali,
non ripubblicare vecchi cache/export. I link delle immagini sono archiviati;
la copia dei file fotografici resta da implementare.

## Comandi operativi

Sul database condiviso già migrato:

```sh
python -m deal_finder.worker daily-cycle --config config.json --mode initial
python -m deal_finder.worker daily-cycle --config config.json
python -m deal_finder.worker market-status
python -m deal_finder.worker market-candidates
python -m deal_finder.worker market-scan --mode full
```

Il primo comando importa **tutti** i run riusciti dei task iniziali. Non abilitare
una pianificazione ricorrente per quei task. Il ciclo quotidiano esclude i task
initial e importa tutti i run quotidiani/refresh non ancora completati nel nostro
archivio, recuperando eventuali interruzioni. Le pagine già salvate vengono
saltate; cambi di scope/mappatura dello stesso run sono rifiutati. L'assenza di
un ID dai risultati di ricerca non è interpretata come rimozione o vendita.

Terminata la raccolta di tutti i task, lo scanner indicizza tutti gli eventi
prima di valutare il primo annuncio. Se una raccolta è troncata o incompleta il
ciclo fallisce e va ripreso; non dichiara completata una base parziale.

Initial/full valutano tutti gli annunci attuali. Incremental aggiorna le
valutazioni se cambia l'annuncio **o il suo insieme di confronti**. Un annuncio
prima senza confronti può essere selezionato quando arrivano dati sufficienti.
Rimozioni e specifiche incomplete successive fanno decadere i vecchi confronti.
Il controllo della base viene effettuato anche prima di mostrare una candidatura.
Full periodico ricalcola anche gli effetti temporali dei pesi dei confronti.

La selezione registra benchmark, P25/P75, link, pesi, cutoff e firma della base.
Segue una coda idempotente di verifica: una ripartenza dopo il salvataggio del
report recupera l'accodamento mancante. L'agente prezzi della coda riceve gli
stessi confronti della base, salvati negli input per riprodurre l'analisi.

## Precisione e verifica

I prezzi richiesti dal venditore sono evidenze non verificate di transazioni.
Lo screening provvisorio usa una fonte alla volta, senza mescolare mercati quando
non sono verificate le identità dei veicoli. Duplicati nella stessa fonte possono
restare: la candidatura non certifica profitto. Minimo 8 confronti, specifiche
esatte, venditori confrontabili, anno ±1, km ±20.000, freschezza 30 giorni,
campione effettivo e dispersione controllati. Prima provincia, poi Italia senza
correzioni geografiche inventate. Tutti i confronti validi entrano nel calcolo.

Acquisti entro 1.000–50.000 € e almeno 10% sotto P25 producono candidati da
verificare. Anche auto danneggiate possono entrare nella coda: il benchmark è
quello delle auto dichiarate non danneggiate e **non include ancora il costo del
ripristino**. Identità, ispezione e preventivi restano requisiti indipendenti.
L'Actor non può autoattribuirsi attestazioni. Lotti manuali non riconducibili
all'archivio mantengono i controlli di identità precedenti sui comparabili.

GET /market/status mostra dati da normalizzare. POST /market/scan?mode=initial
oppure incremental/full esegue e accoda. GET /market/candidates supporta
city/province e paginazione; il catalogo generale conserva i filtri di raggio e
prezzo. Candidati separati dal feed di opportunità verificate.

Prezzo consigliato di rivendita, profitto e giorni di vendita sono null; il
supervisore e la pubblicazione restano bloccati senza evidenze e calibrazione.
L'anteprima di pubblicazione esistente continua ad applicare i suoi controlli.

## Render

`render.yaml` predispone API, worker, ciclo giornaliero alle 04:00 UTC e revisione
completa domenicale alle 06:00 UTC. I quattro servizi usano la stessa URI del
database PostgreSQL, non file locali. Inserire i segreti nel pannello Render;
applicare prima le migrazioni con account proprietario, poi usare il login backend
limitato per i servizi. `DEAL_FINDER_MODE=production` rifiuta SQLite nei worker.

Il file è un template: non conferma servizi avviati, credenziali collegate,
annunci reali acquisiti o pubblicazione abilitata. Primo bootstrap e verifica
della mappatura sono necessari prima di attivare il ciclo ricorrente.

Documentazione tecnica primaria:
- https://docs.apify.com/api/v2/actor-task-runs-get
- https://docs.apify.com/api/v2/actor-run-get
- https://docs.apify.com/api/v2/dataset-items-get
- https://render.com/docs/blueprint-spec
- https://render.com/docs/cronjobs
