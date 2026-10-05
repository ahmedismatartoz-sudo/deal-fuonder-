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

La prova SQL conferma schema e permessi, non l'esecuzione continuativa degli
agenti Python su Supabase. La CI PostgreSQL usa esclusivamente un database
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

## Avvio ancora da configurare

1. Su Render o sull'hosting scelto configurare API e worker con lo stesso database.
2. Provisionare login backend e segreti database/token; proprietario separato
   per le migrazioni. Il ruolo NOLOGIN esistente contiene solo i permessi.
3. Eseguire check-db; avviare API con HTTPS e worker supervisionato.
4. Configurare ricerche native AutoScout24; per altre fonti/refresh configurare
   task Apify, token e mappature verificate sui veri export. Raccogliere
   la base una volta; poi attivare task giornalieri dei nuovi annunci e refresh
   mirati, con import giornaliero dal servizio. I campi mancanti non sono inventati.
5. Raccogliere transazioni e date documentate, calibrare prezzo/liquidità/costi,
   poi abilitare i gates di pubblicazione mattutina con feed persistente.

L'attivazione dell'account Render non conferma servizi avviati. Non risultano
hosting continuativi, task/credenziali Apify collegati, annunci reali acquisiti
nel database live o modelli calibrati.
Le fotografie sono conservate come URL; i file non sono ancora copiati in storage.
Pubblicazione automatica e raccomandazioni d'acquisto restano disabilitate.
