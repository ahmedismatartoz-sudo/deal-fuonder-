# Stato verificato — 5 ottobre 2026

Progetto Supabase `deal-finder`, riferimento `jfqjtgasmnbiliijdjwo`,
regione eu-central-1, organizzazione scelta dall'utente. Costo comunicato dal
connettore alla creazione: 0/mese; non è una previsione dei costi futuri.

## Database live

- Migrazioni 001_postgres.sql, 002_private_access.sql, 003_national_archive.sql
  e 004_market_screening.sql
  applicate; checksum registrati nello schema privato deal_finder.
- Tredici tabelle, tutte con RLS. Anon e authenticated non possono leggere l'archivio.
- Ruolo deal_finder_backend senza login, superuser o bypass RLS; nessun segreto nel codice.
- L'archivio aggiunto contiene collection_pages, listing_events e collection_quarantine.
  Il backend può inserire/leggere, non aggiornare/eliminare lo storico.
- La base prezzi aggiunge market_observations e market_reviews: proiezioni
  indicizzate degli eventi, dati mancanti, benchmark e candidature immutabili.
- Verifica SQL transazionale sul progetto reale: inserimento/lettura di pagina,
  evento e quarantena sintetici con il ruolo backend; controllo dei divieti
  di UPDATE/DELETE; ROLLBACK di tutti i dati e del permesso temporaneo SET ROLE.
  Zero pagine/eventi/quarantene persistenti dopo la prova.
- Advisor sicurezza dopo la migrazione 003: nessun rilievo.
- Hash 003: `4f5e79d4de674ae8a6f1f34358dd37ce5aa56552d1dc23293eebb4431f9121d7`.
  I due hash precedenti restano invariati.
- Migrazione 004 applicata dopo i test PostgreSQL della PR #6. Prova SQL live
  transazionale: backend inserisce/legge proiezione e report, controllo dei
  divieti UPDATE/DELETE, ROLLBACK di tutti i dati e del permesso SET ROLE.
  Zero eventi/proiezioni/report persistenti dopo la prova; advisor sicurezza
  dopo la 004 senza rilievi.
- Hash 004: `06c58dc22acf611b80b09153f86326429980ccbb3167edcd7da443a28fddfb6b`.

La prova SQL iniziale confermava schema e permessi. L'avvio continuativo su
Render è stato verificato successivamente, come riportato sotto. La CI PostgreSQL usa esclusivamente un database
usa-e-getta locale; nessun test distruttivo viene eseguito sul progetto live.
La suite della PR #6 comprendeva 121 test, tutti superati su GitHub Actions:
API/SQLite/PostgreSQL, ruoli privati, provenienza, checkpoint, rimozioni,
rianalisi e packaging delle quattro migrazioni. Nessuna prova di carico nazionale.

## Componenti effettivamente disponibili

Archivio, raccolta export/Apify iniziale e incrementale con checkpoint, catalogo
geografico, scansione di tutta la base iniziale, aggiornamento delle valutazioni
per annunci/confronti modificati e accodamento automatico idempotente.
L'agente prezzi della coda usa i confronti dell'archivio nei suoi input
riproducibili. I prezzi dichiarati restano distinti da identità verificate.
Disponibili anche promozione con evidenze, screening provinciale/nazionale, ispezioni/preventivi
attestati, scenari con costi, supervisione e anteprima di pubblicazione.
Il flusso espone sette componenti principali e controlli interni separati.
Template Render: API, worker, import/analisi giornalieri, revisione settimanale.
Configurazione e comandi in [collection-cycle.md](collection-cycle.md).

### Scraper nativo AutoScout24

Collector HTML pubblico integrato con API, worker e daily-cycle, senza Actor
Apify a pagamento. Verifica live locale del 05/10/2026: una ricerca circoscritta,
due annunci reali importati con dettagli, fotografie come URL, descrizioni e
prezzi originali; zero quarantene. Database di verifica separato, nessun dato
di questa prova importato nel progetto Supabase live. Non è una raccolta nazionale.
Venti test dedicati su parsing, rate/prezzi condizionati, confini URL/robots,
filtri applicati, checkpoint, new-only, API e ciclo nativo. Aggiunta integrazione
PostgreSQL nella CI usa-e-getta. Configurazione in [autoscout24.md](autoscout24.md).
I dati mancanti, inclusi provincia/generazione/allestimento, bloccano i benchmark
precisi finché non sono arricchiti: nessuna identità o condizione viene inventata.

## Avvio Render verificato — 5 ottobre 2026, 21:45 UTC

- Blueprint collegato a main; API e worker avviati dopo aver configurato
  DEAL_FINDER_DATABASE_URL. La build iniziale era riuscita, ma il processo
  si fermava perché mancava la connessione.
- API: https://deal-finder-api-3lr5.onrender.com; GET /health verificato con
  HTTP 200. GET /market/status autenticato verificato con HTTP 200.
- Ruolo di login deal_finder_runtime, membro di deal_finder_backend:
  nessun superuser, creazione database/ruoli o bypass RLS.
  Password generata e token API inseriti direttamente nelle variabili Render;
  nessun segreto aggiunto al repository. Session pooler su porta 5432 con TLS.
- Worker confermato live da deploy/log; connessione del ruolo runtime
  rilevata nel database. Nessun candidato accodato nel campione.
- Job giornaliero configurato con le due ricerche dell'esempio nativo:
  Panda 2015 a 5.000–6.000 euro e Fiat 500 2015 a 6.000–8.000 euro, Italia.
  Deploy del cron confermato live; esecuzione programmata alle 04:00 UTC.
  Questa verifica non conferma ancora l'esecuzione del primo ciclo giornaliero.
- Revisione settimanale creata, connessione configurata; pianificazione
  domenica alle 06:00 UTC.

### Prima raccolta persistente su Supabase

Run native-render-bootstrap-2026-10-05-01, modalità initial:
12 pagine, 209 annunci unici accettati, zero quarantene, complete=true.
Checkpoint ripreso correttamente tra richieste successive; dettagli acquisiti
dal collector su Render. Campione ristretto a due modelli/anno/fasce di prezzo:
non è una base nazionale rappresentativa né copertura del mercato verificata.

Verifica SQL: 209 eventi e 209 ID unici; 81 prezzi classificati total,
128 unknown, esclusi dai confronti utilizzabili. Scansione iniziale via API:
209 eventi proiettati, zero eventi da indicizzare residui, zero selezioni/jobs.
Tutti i 209 record hanno un problema di normalizzazione sul campo generation:
il benchmark preciso resta bloccato finché la generazione non viene
arricchita con provenienza documentata. Zero quarantene non significa
completezza dei dati o disponibilità di stime attendibili.

## Lavoro ancora necessario

1. Arricchire generazione e altre specifiche mancanti con fonti documentate,
   senza inferirle automaticamente dall'anno; verificare prezzi totali.
2. Ampliare le ricerche per marca/modello, anno, prezzo e zona e misurare
   copertura/qualità della base. Il campione attuale non è la base ampia richiesta.
3. Verificare il primo ciclo giornaliero, il recupero dei nuovi ID e il refresh
   mirato dei vecchi annunci; eventuali task Apify non sono collegati.
4. Raccogliere transazioni e date documentate, calibrare prezzo/liquidità/costi,
   poi abilitare i gates di pubblicazione con un feed persistente.

Le fotografie sono conservate come URL; i file non sono ancora copiati in storage.
Previsioni di rivendita/margine/liquidità, pubblicazione automatica e
raccomandazioni d'acquisto restano disabilitate.
