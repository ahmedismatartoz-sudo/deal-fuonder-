# Deal Finder

Nucleo iniziale per opportunità nel mercato delle auto usate. Priorità: precisione,
provenienza dei dati e calcoli verificabili. Nessuna ipotesi su officine o costi agevolati.

## Avvio locale (Python 3.11+)

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
uvicorn deal_finder.api:app --host 127.0.0.1 --port 8000
```

API: http://127.0.0.1:8000/docs. Solo sviluppo locale: aggiungere autenticazione
 e limiti di upload prima di esporre il servizio in rete.

## Funzioni disponibili

- Import CSV/JSON atomico, validazione, normalizzazione, storico immutabile.
- Identità fonte/annuncio, import idempotenti e rifiuto dei conflitti di timestamp.
- Comparabili della stessa marca/modello/generazione/allestimento-motore,
  carburante/cambio/provincia/tipo venditore, anno ±1 e km ±20.000.
- Solo annunci attivi senza danni dichiarati, osservati negli ultimi 30 giorni.
- Mediana ponderata per anno/km/freschezza con URL e pesi dei comparabili.
- Minimo 8 comparabili, controllo numerosità effettiva e dispersione.
- SQLite locale; schema PostgreSQL preparato in database/001_initial.sql.
  Il collegamento operativo a PostgreSQL resta da implementare.

POST /imports: oggetto con format (json/csv) e content (stringa contenente i dati).
POST /valuations: annuncio target nel formato di deal_finder/models.py.
price_eur è un intero in euro; observed_at ISO 8601 con fuso; vehicle_id opzionale
richiede un'identità verificata. Gli esempi nei test sono sintetici.

## Limiti

La versione 0.1 produce un benchmark dei **prezzi richiesti**, non una previsione
di vendita, guadagno netto o tempo di uscita. P25/P75 descrivono i comparabili,
non un intervallo predittivo. Dati insufficienti o prezzi troppo dispersi bloccano
la stima. Le soglie sono provvisorie e non calibrate sul mercato reale.
La deduplicazione tra marketplace necessita di vehicle_id verificati; altrimenti
il risultato segnala il rischio di duplicati. Non ci sono ancora scraper o dati reali.

Prossimo passo: collegare PostgreSQL, importare annunci verificati e raccogliere
vendite effettive per misurare e calibrare gli errori. Vedere docs/architecture.md.

## Agenti e coda persistente (v0.2)

Nove agenti eseguibili: intake, qualità, identità, mercato, condizioni,
ripristino, opportunità, validazione e misurazione delle previsioni.
Contratti dettagliati, limiti e criteri di precisione in [docs/agents.md](docs/agents.md).

```sh
python -m deal_finder.worker agents
python -m deal_finder.worker --db demo.db submit examples/synthetic_batch.json
python -m deal_finder.worker --db demo.db drain --limit 100
python -m deal_finder.worker --db demo.db batch synthetic-demo-v1
python -m deal_finder.worker --db demo.db result 9
```

L'esempio è interamente sintetico e non identifica opportunità reali. Le date
sono fisse per riproducibilità; scadranno e il sistema bloccherà le analisi stale.
Per i tuoi dati inviare un lotto con batch_id univoco a POST /batches. Un worker
locale avviato con drain esegue i lavori. GET /batches/{id}/jobs mostra gli esiti;
POST /batches/{id}/replay li rianalizza; POST /evaluations misura gli errori su
vendite documentate. Nulla si avvia automaticamente o raccoglie dati dai marketplace.

I record invalidi vengono conservati in quarantena e non impediscono l'analisi
degli altri record del lotto. Il vecchio POST /imports mantiene il suo contratto
atomico. Il Market Agent richiede attestazioni di identità anche per i comparabili.
L'endpoint sperimentale POST /valuations della v0.1 resta un benchmark diretto
senza questi controlli aggiuntivi: utilizzare la pipeline per gli esiti organizzati.

Per testare anche l'API: `pip install -e ".[test]"`. I test di API sono obbligatori
nella CI; senza FastAPI/httpx sono saltati nell'ambiente locale.

Per restare in ascolto e organizzare automaticamente i nuovi lotti:

```sh
python -m deal_finder.worker --db deal-finder.db run --poll-seconds 2
```

Avviare API e worker con lo stesso percorso di database. Il comando run rimane
attivo finché viene interrotto; non è ancora installato come servizio cloud.
