# Stato verificato — 5 ottobre 2026

Progetto Supabase `deal-finder`, riferimento `jfqjtgasmnbiliijdjwo`,
regione eu-central-1, organizzazione scelta dall'utente. Costo comunicato dal
connettore alla creazione: 0/mese; non è una previsione dei costi futuri.

## Database live

- Migrazioni 001_postgres.sql, 002_private_access.sql e 003_national_archive.sql
  applicate; checksum registrati nello schema privato deal_finder.
- Undici tabelle, tutte con RLS. Anon e authenticated non possono leggere l'archivio.
- Ruolo deal_finder_backend senza login, superuser o bypass RLS; nessun segreto nel codice.
- L'archivio aggiunto contiene collection_pages, listing_events e collection_quarantine.
  Il backend può inserire/leggere, non aggiornare/eliminare lo storico.
- Verifica SQL transazionale sul progetto reale: inserimento/lettura di pagina,
  evento e quarantena sintetici con il ruolo backend; controllo dei divieti
  di UPDATE/DELETE; ROLLBACK di tutti i dati e del permesso temporaneo SET ROLE.
  Zero pagine/eventi/quarantene persistenti dopo la prova.
- Advisor sicurezza dopo la migrazione 003: nessun rilievo.
- Hash 003: `4f5e79d4de674ae8a6f1f34358dd37ce5aa56552d1dc23293eebb4431f9121d7`.
  I due hash precedenti restano invariati.

La prova SQL conferma schema e permessi, non l'esecuzione continuativa degli
agenti Python su Supabase. La CI PostgreSQL usa esclusivamente un database
usa-e-getta locale; nessun test distruttivo viene eseguito sul progetto live.
La suite verificata comprende 84 test, API/SQLite/PostgreSQL e packaging migrazioni.

## Componenti effettivamente disponibili

Archivio, raccolta export iniziale/incrementale con checkpoint, catalogo geografico,
promozione alla coda, screening prezzi provinciale/nazionale, ispezioni/preventivi
attestati, scenari con costi, supervisione e anteprima di pubblicazione.
Il flusso espone sette componenti principali e controlli interni separati.

## Avvio ancora da configurare

1. Su Render o sull'hosting scelto configurare API e worker con lo stesso database.
2. Provisionare login backend e segreti database/token; proprietario separato
   per le migrazioni. Il ruolo NOLOGIN esistente contiene solo i permessi.
3. Eseguire check-db; avviare API con HTTPS e worker supervisionato.
4. Collegare adapter reali per le fonti e verificare la prima raccolta di annunci.
5. Raccogliere transazioni e date documentate, calibrare prezzo/liquidità/costi,
   poi abilitare i gates e lo scheduler mattutino con feed persistente.

L'attivazione dell'account Render non conferma servizi avviati. Non risultano
hosting continuativi, scraper collegati, annunci reali acquisiti o modelli calibrati.
Le fotografie sono conservate come URL; i file non sono ancora copiati in storage.
Pubblicazione automatica e raccomandazioni d'acquisto restano disabilitate.
