# Deal Finder

Software per individuare opportunità nelle auto usate in Italia. Priorità:
precisione dei prezzi, provenienza delle evidenze e analisi riproducibili.
Budget di acquisto: **1.000–19.999 €**. Nessuna ipotesi su un'officina propria.

## Avvio e verifiche

Python 3.11+. Installare `pip install -r requirements.lock`, poi
`pip install --no-deps -e .`. Eseguire `python -m unittest discover -s tests -v`.
La CI esegue anche le integrazioni PostgreSQL su database usa-e-getta.

SQLite per sviluppo; configurare DEAL_FINDER_DATABASE_URL per PostgreSQL.
Le migrazioni operative sono in deal_finder/migrations:
`python -m deal_finder.worker migrate`, poi `check-db`.
Non applicare i vecchi draft della cartella database.

API locale: `uvicorn deal_finder.api:app --host 127.0.0.1 --port 8000`.
Produzione: `python -m deal_finder.serve`, con token bearer e PostgreSQL.
API e worker devono usare lo stesso database. Docker/compose sono predisposti.
Le dipendenze sono fissate in requirements.lock.

## Flusso operativo

Sette componenti principali: raccolta → selezione prezzi → ripristino →
rivendita → margine/liquidità → supervisione → pubblicazione.
Qualità, identità, evidenze e misurazione degli errori sono controlli interni.
Vedere [docs/agents.md](docs/agents.md) e [docs/architecture.md](docs/architecture.md).

**Disponibile:** archivio nazionale di originali anche incompleti, testo completo
e URL delle fotografie; eventi immutabili, rimozioni esplicite, quarantena;
raccolta iniziale/incrementale con pagine idempotenti e checkpoint; adapter export e Apify;
scraper AutoScout24 nativo sull'HTML pubblico, integrato con API e ciclo giornaliero;
filtri città/provincia/raggio/prezzo; promozione esplicita degli annunci completi
alla coda e selezione automatica sull'intera base; benchmark dei prezzi richiesti; preventivi/ispezioni attestati;
scenari con tutte le categorie di costo; supervisore e anteprima di pubblicazione.

**Da collegare:** ricerche nazionali e configurazione hosting dello scraper nativo;
task Apify e mappature su export reali Facebook/Subito/Automobile.it,
archiviazione dei file fotografici, geocodifica documentata, base ricambi/manodopera,
modelli di prezzo di vendita e liquidità calibrati su esiti reali, scheduler
mattutino e servizi Render continuativi (template e ciclo giornaliero predisposti). Nessuna copertura totale dei marketplace
è dichiarata. La raccolta terminata riguarda solo lo scope/export ricevuto.

## Raccolta iniziale e aggiornamenti

```sh
python -m deal_finder.worker --db demo.db collect-file examples/collection_export.json --max-pages 100
python -m deal_finder.worker agents
python -m deal_finder.worker --db demo.db submit examples/synthetic_batch.json
python -m deal_finder.worker --db demo.db drain --limit 100
python -m deal_finder.worker --db demo.db publication-preview synthetic-demo-v1
```

Gli esempi sono sintetici. Il file export contiene source, run_id, mode
(initial/incremental), scope e pages (array di pagine di eventi). Il checkpoint
permette di riprendere lo stesso export; non cambiare file durante una raccolta.

Per collegamento Apify, base iniziale, aggiornamenti dei soli nuovi annunci,
refresh mirati e ciclo Render vedere [docs/collection-cycle.md](docs/collection-cycle.md).
Per raccolta diretta AutoScout24 senza tariffa Actor:
[docs/autoscout24.md](docs/autoscout24.md), comando `collect-autoscout24` e
POST `/collection/autoscout24`. Il ciclo nativo scarica dettagli dei nuovi ID;
il refresh dei vecchi annunci resta separato. Non dichiara copertura totale.
`collect-file --scan` collega un export completato all'analisi della base.
`daily-cycle --mode initial` esegue il bootstrap; `daily-cycle` esclude i task
iniziali e importa i run quotidiani/refresh, poi seleziona e accoda le candidature.

API protette dal token configurato:
- POST /collection/pages, GET /collection/runs/{source}/{run_id}, GET /collection/sources.
- GET /catalogue: min_price/max_price, city/province, latitude/longitude/radius_km,
  offset/limit. Il catalogo non certifica opportunità.
- GET /catalogue/{source}/{source_id}/history.
- POST /catalogue/{source}/{source_id}/promote: batch_id e attestazioni separate.
- POST /batches; GET /batches/{id}/jobs e /jobs/{id}; POST /batches/{id}/replay.
- POST /publication/preview con batch_id: anteprima e motivi di esclusione.
- POST /evaluations: metriche su previsioni congelate e compravendite documentate.
- GET /market/status, POST /market/scan e GET /market/candidates: base prezzi e candidati da verificare.

Il worker `run --poll-seconds 2` rimane in ascolto dei lotti; non avvia
raccolte online o pianificazioni giornaliere. Un lavoro done può avere analisi
bloccate per evidenze mancanti: consultare components.supervisor.

## Precisione e blocchi

Lotti manuali: comparabili con identità attestata. Archivio: screening separato
su prezzi richiesti della stessa fonte, senza fingere identità verificate.
Stessi marca/modello/generazione/allestimento,
carburante/cambio/venditore, anno ±1 e km ±20.000; minimo 8, campione effettivo
e dispersione controllati. Prima la provincia, poi eventuale confronto nazionale
esplicitamente segnalato. Nessuna correzione geografica inventata.

Prezzi totali distinti da rate/anticipi/prezzi sconosciuti. Solo ultimi annunci
attivi, osservati entro 30 giorni. Screening provvisorio: almeno 10% sotto P25;
questa soglia non dimostra redditività ed è da calibrare. Le fotografie e
le dichiarazioni del venditore non sostituiscono un'ispezione.

P25/P75 e scenari sui prezzi richiesti **non sono previsioni di vendita o profitto**.
Prezzo consigliato, margine previsto e giorni alla vendita restano null senza
modelli calibrati. Pubblicazione automatica disabilitata; il supervisore non
approva finché mancano modelli, termini d'acquisto e verifica disponibilità.
POST /valuations resta un benchmark sperimentale diretto senza tutti i controlli
della pipeline: non usarlo per decisioni d'acquisto.

Database live: [docs/live-status.md](docs/live-status.md).
Configurazione PostgreSQL: [docs/postgres.md](docs/postgres.md).

## Primo test dell’archivio e memoria prezzi

`python -m deal_finder.worker first-archive-test --run-id archive-YYYYMMDD-v1`
proietta tutta la base prima di confrontarla. Il worker aggiorna poi solo i nuovi
eventi nella memoria compatta `price_observations`; la rimozione più recente
annulla il vecchio prezzo. I report in `price_test_reports` sono immutabili e
legati al momento del test. `DEAL_FINDER_FIRST_ARCHIVE_TEST` abilita lo stesso
test nel worker: le candidature vengono accodate per completare le evidenze
prima delle altre richieste. La coda ordinaria rimane conservata.

Il primo test esclude danni dichiarati o segnalati, rate/acconti, dati senza
anno/chilometri e località non risolte. Confronta famiglia, carburante, cambio,
anno ±1 e chilometri ±20.000; richiede almeno otto analogie provvisorie dopo
il collasso delle specifiche duplicate. Usa P25 ridotto del 15%, con almeno
2.000 € di spazio lordo e 25% sul prezzo. Sono priorità di verifica: generazione,
allestimento, prezzo totale, danni, rivendita e tutti i costi restano da attestare.
Il rapporto non indica margini netti o acquisti approvati. Quattro fasce di
acquisto (1–5, 5–10, 10–15, 15–20 mila euro) ricevono turni alternati, massimo
due auto per famiglia, senza abbassare i requisiti per riempire una fascia.

La ricerca ricambi richiede prima identità e lavori necessari: confronta codici,
compatibilità, IVA, consegna, disponibilità e condizioni uniformi. Non si
inventano ricambi o preventivi per sbloccare un’auto senza ispezione.

## Conservazione fotografie delle candidature

Il worker salva i file originali delle candidature del primo test in tabelle
private `photo_assets` e `photo_references`, legando SHA-256, URL originale,
ordine e osservazione dell’annuncio. Le copertine precedono le altre immagini.
Il report autenticato `/price-tests/{run_id}` espone `archived_photos` e
`retained_cover_available`; `/archive/photos/{sha}` serve i byte salvati con
la stessa autenticazione dell’API, senza dipendere dal link esterno.

Sono ammessi solo HTTPS su CDN AutoScout24/Facebook, senza redirect, login o
aggiramento di rifiuti. Limite 2 MiB per file e 32 MiB totali, deduplica per hash;
formati JPEG/PNG/WebP. Download falliti non vengono etichettati come salvati;
retries transitori distanziati, massimo tre per foto. La conservazione è
attivata sulle candidature, non una copia completa delle foto di tutto l’archivio.
Il caricamento prezzi usa estrazione JSON una sola volta prima dei campi
normalizzati e riduce automaticamente i lotti dopo un timeout.

In produzione, memoria prezzi e conservazione foto lavorano su un ciclo
indipendente con connessioni proprie: i tempi della raccolta non ne limitano
il progresso. Errori e retry restano isolati; le connessioni vengono chiuse.
