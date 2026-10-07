# Facebook: analisi automatica dell'archivio

Il worker con `DEAL_FINDER_FACEBOOK_SCREENING_ENABLED=1` legge tutti gli ultimi
annunci Facebook, anche fuori dalle prime 300 candidature AutoScout. Recupera
anno dichiarato nel titolo, chilometri espliciti e carburante dichiarato senza
modificare lo storico. `car_miles` senza unità resta irrisolto; per la sola
priorità di ricerca vengono considerate entrambe le letture possibili
(chilometri/miglia), senza valorizzare `mileage_km` o un margine netto. USED non è sano,
anno del titolo non è prima immatricolazione verificata. 500 L e 500 X sono
famiglie separate dalla 500.

Confronti sull'intera memoria prezzi: ultimi annunci attivi entro 30 giorni,
prezzi totali, nessun danno dichiarato, almeno tre riferimenti vicini entro
un anno/20.000 km. Le incompatibilità note di motore, cambio, generazione,
carrozzeria e versioni prestazionali escludono il confronto. Dati mancanti
non provano equivalenza: sono blocchi espliciti della valutazione finale.

Riferimento più basso, meno 15% nello scenario d'uscita, sconto almeno 20%,
riserva 750 euro. Questa è una soglia necessaria PRIMA di costi ignoti:
`maximum_margin_before_unknown_costs_eur` non è utile netto. La lista finale
`opportunities` accetta soltanto conti con costi/evidenze completi revisionati
dalla pipeline indipendente. `candidates` è la coda di ricerca separata.
Entrambe contengono al massimo 50: nessuna quota obbliga a promuovere esclusi.

Soglie nette v2: acquisto sotto 5.000 -> 2.000 euro; da 5.000 a meno di
10.000 -> 3.000; da 10.000 a meno di 15.000 -> 4.000; da 15.000 a 20.000
-> 5.000. Ordine finale per margine prudente, senza quote per modello/prezzo.
Guadagno realizzato e tempi di vendita non vengono inventati.

`DEAL_FINDER_ARCHIVE_REVIEW_ONLY=1` sospende nuovi avvii di raccolta mentre
viene analizzato il materiale esistente. Nessun nuovo servizio richiesto.
`DEAL_FINDER_FACEBOOK_RESEARCH_LIMIT` (default 0, massimo 50) limita le
osservazioni accodabili per ricerca foto con il provider già configurato.
Batch idempotenti per snapshot: riavvii/report nuovi non ripetono chiamate
sullo stesso annuncio. La ricerca produce indizi, non un'ispezione o un
preventivo di riparazione. Una mancanza materiale mantiene il netto null.

Il report viene conservato in `price_test_reports` e letto con autenticazione
da `GET /facebook/opportunities/latest`. La revisione si aggiorna quando
cambiano base prezzi, archivio o risultati della ricerca. Pubblicazione e
acquisti automatici restano soggetti ai controlli del supervisore.
