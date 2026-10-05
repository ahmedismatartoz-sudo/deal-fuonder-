# PostgreSQL e Supabase — v0.3

API e worker condividono ora un adapter PostgreSQL operativo. SQLite resta
un'opzione per sviluppo locale. Non copiare le password in GitHub o nei messaggi.

## Configurazione del progetto

Usare un progetto Supabase dedicato al Deal Finder. Le migrazioni creano soltanto
lo schema privato `deal_finder`; non modificano lo schema `public`, utenti o dati
preesistenti. Non utilizzare i vecchi file di design `database/001_initial.sql`
e `database/002_agents.sql`: le migrazioni operative sono incluse nel pacchetto.

Configurare come segreti del servizio:

- DEAL_FINDER_DATABASE_URL: URI PostgreSQL dal pannello Connect. Per IPv4 usare
  Session pooler, porta 5432; una connessione diretta è valida dove raggiungibile.
- DEAL_FINDER_API_TOKEN: token casuale di almeno 32 caratteri, distinto dalla password DB.
- DEAL_FINDER_MODE=production per ogni istanza API esposta in rete.

Il server usa credenziali di database, non la publishable/anon key. Non esporre
queste credenziali nel frontend. La prima migrazione richiede un ruolo che possa
creare lo schema; un ruolo backend dedicato è consigliato per l'esecuzione ordinaria.
I dati sono fuori dallo schema normalmente esposto dalla Data API Supabase.

TLS è obbligatorio per host remoti. `sslmode=require` cifra la connessione; per
verificare anche il server configurare il certificato Supabase e `verify-full`.
Il transaction pooler Supabase sulla porta 6543 viene rifiutato: il backend
richiede una sessione persistente per il search_path privato. Prepared statements
automatici disabilitati. Password con simboli speciali richiedono URI encoding.

Riferimenti ufficiali:
https://supabase.com/docs/guides/database/connecting-to-postgres
https://www.psycopg.org/psycopg3/docs/advanced/prepare.html

## Migrazione e prova

Dopo aver impostato le variabili nell'ambiente:

```sh
pip install -e ".[postgres,test]"
python -m deal_finder.worker migrate
python -m deal_finder.worker check-db
python -m deal_finder.serve
```

In un secondo processo, con lo stesso ambiente:

```sh
python -m deal_finder.worker run --poll-seconds 2
```

Migrate applica SQL in una transazione, memorizza hash e nomi, blocca migrazioni
storiche modificate ed è idempotente. Una migrazione fallita non lascia uno schema
parzialmente installato. Non crea un account Supabase o un progetto automaticamente.

L'entrypoint produzione verifica URL PostgreSQL, schema e token prima dell'avvio.
Tutte le route salvo `/health` richiedono `Authorization: Bearer <token>` quando
il token è configurato. Anche documentazione e report sono protetti. `/health`
è soltanto liveness; `check-db` verifica separatamente connessione e schema.

Dockerfile e compose.yaml avviano una migrazione, API e worker distinti. Compose
pubblica l'API sul loopback: per Internet serve un reverse proxy HTTPS o un servizio
hosting configurato. Questi file non costituiscono un deployment già effettuato.

## Integrità e più worker

I dati JSON sono JSONB nativo e le date timestamptz. Batch idempotenti protetti da
lock transazionale; snapshot e prove di identità non si sovrascrivono. Un conflitto
nel singolo record annulla quel record e lo conserva in quarantena.
I worker prelevano i lavori con FOR UPDATE SKIP LOCKED, token e lease: due processi
non possiedono lo stesso lavoro. Un risultato pubblicato con token scaduto è rifiutato.

La CI avvia un PostgreSQL 16 temporaneo, applica migrazioni ed esegue la suite con
prove di concorrenza, riavvio, quarantena, JSONB e riproducibilità. I test distruttivi
richiedono esplicitamente un database loopback chiamato `deal_finder_test`; non
sono eseguibili sul progetto Supabase di produzione. Nessun dato reale in CI.

## Stato e passi successivi

Il database Supabase dedicato e le migrazioni live sono ora verificati come
documentato in [live-status.md](live-status.md). L'hosting dell'API e del worker
rimane da configurare e non è deducibile dai test CI. Dopo il collegamento dei processi,
provare un lotto sintetico sul progetto di sviluppo
prima di importare auto reali. Misurare storage e tempi prima di caricare 100k+ annunci.
Le previsioni di vendita restano disabilitate finché manca una calibrazione reale.

## Permessi separati per il servizio

La seconda migrazione abilita RLS su tutte le tabelle private e revoca ogni
accesso a `PUBLIC` e, quando presenti, ai ruoli Supabase `anon` e `authenticated`.
Il ruolo `deal_finder_backend` è `NOLOGIN` e non può bypassare RLS. Può leggere
i dati, inserire nuove osservazioni e risultati e aggiornare i lavori; non può
riscrivere o eliminare lo storico né modificare lo schema o le migrazioni.

Prima del deployment, creare un login backend dedicato nel database usando
credenziali generate e salvate nel secret manager dell'hosting. Assegnargli il
ruolo `deal_finder_backend` con ereditarietà dei privilegi. Non dare questo ruolo
ad `anon` o `authenticated`, non esporre lo schema `deal_finder` alla Data API.
API e worker devono usare quel login; `migrate` deve usare una credenziale
separata del proprietario dello schema. Le connessioni al session pooler usano
il nome del login seguito dal riferimento del progetto, come indicato da Supabase.
Il login backend e i segreti non sono ancora stati creati né salvati in GitHub.
