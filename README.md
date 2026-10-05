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
