# Stato verificato del database — 5 ottobre 2026

Progetto: `deal-finder`, riferimento `jfqjtgasmnbiliijdjwo`, regione `eu-central-1`.
Organizzazione scelta dall'utente. Costo comunicato dal connettore per la creazione:
0 al mese; non è una stima dei costi futuri di hosting, traffico o crescita dei dati.

## Eseguito

- Progetto attivo e query PostgreSQL eseguita con successo.
- Migrazioni del pacchetto `001_postgres.sql` e `002_private_access.sql` applicate.
- Hash memorizzati in `deal_finder.schema_migrations`, uguali ai file del repository.
- Otto tabelle, tutte con RLS abilitata; schema non accessibile ad `anon` e
  `authenticated`, che non hanno permessi SELECT sulle tabelle.
- Ruolo backend senza login, superuser o bypass RLS; nessuna password nel repository.
- Prova transazionale nel database reale: inserimento di lotto/record/lavoro,
  passaggio running/done e registrazione di un risultato **sintetico di storage**.
  Il ruolo backend non può eliminare lo storico o scrivere nelle migrazioni.
  Tutti i dati della prova sono stati annullati con ROLLBACK; zero lotti persistenti.
  La capacità di SET ROLE per l'amministratore è stata concessa solo all'interno
  della transazione di prova e annullata insieme ad essa.
- Advisor di sicurezza: nessun rilievo. Advisor prestazioni: cinque indici non
  ancora utilizzati, atteso su database senza annunci; conservarli e misurare
  l'utilizzo dopo il caricamento.

## Separazione delle verifiche

La prova SQL conferma schema, permessi e scritture, non l'esecuzione dei nove
agenti Python contro Supabase. La pipeline Python è verificata con test locali
e con la CI PostgreSQL su database usa-e-getta. Nessun test distruttivo va eseguito
sul progetto live. Nessuna previsione è calibrata su compravendite reali.

## Passaggio necessario per l'avvio continuativo

1. Scegliere un hosting per due processi/container: API e worker Python.
2. Provisionare il login backend e salvare le stringhe database e il token API
   nei segreti dell'hosting; usare un login proprietario separato per le migrazioni.
3. Sul servizio configurato eseguire `check-db`, avviare l'API con HTTPS e
   `python -m deal_finder.worker run --poll-seconds 2` come worker supervisionato.
4. Verificare importazione via API, analisi del worker e lettura degli esiti
   sullo stesso database con un lotto sintetico dedicato allo sviluppo.
5. Importare annunci verificati e raccogliere esiti reali per calibrare le stime.

GitHub conserva il codice; Supabase conserva il database. Al momento non è
attivo un hosting continuativo per l'API o il worker. Non sono stati acquisiti
annunci reali, pubblicate previsioni di vendita o collegati scraper.
