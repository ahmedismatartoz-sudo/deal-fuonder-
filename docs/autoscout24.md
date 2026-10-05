# Scraper AutoScout24 integrato

Lo scraper usa direttamente l'HTML pubblico italiano, senza Apify e senza tariffa
per annuncio. Non è un'API ufficiale. Server, traffico e manutenzione possono
avere un costo. Il collector non cerca di aggirare login, CAPTCHA, divieti
robots, HTTP 403/429 o redirect: ferma la pagina e mantiene il checkpoint.

## Raccolta iniziale

Configurare ricerche reali `/lst/marca[/modello]` su www.autoscout24.it.
L'esempio `examples/autoscout24_config.json` è uno scope piccolo per verifica
tecnica, **non una base nazionale rappresentativa**. Per 100.000 annunci utili
servono molte ricerche distribuite per marca/modello, anno, prezzo e zona;
gli ID unici e i campi utilizzabili contano più dei record ricevuti.
Includere confronti oltre 50.000 euro dove pertinenti.

Le ricerche sono forzate su Italia, auto usate, ordinamento per data. URL
arbitrari, credenziali, altri domini, pagine private e indici iniziali diversi
da 1 sono rifiutati. Ogni risposta deve confermare tutti i filtri richiesti:
un parametro ignorato ferma la raccolta. Eseguire un solo collector alla volta
per rispettare l'intervallo tra richieste, anche tra processi diversi.

```sh
python -m deal_finder.worker collect-autoscout24 \
  --config examples/autoscout24_config.json --mode initial \
  --run-id autoscout-base-01 --max-pages 100
```

Ogni pagina è salvata nell'archivio condiviso. `complete=false` significa che
serve ripetere lo stesso comando/run-id per continuare. Una risposta bloccata
o uno schema sconosciuto non avanza il checkpoint. Configurazione e modalità
non possono cambiare durante un run. Un run completato non fa nuove richieste.
Per indicizzare/selezionare al termine aggiungere `--scan`, oppure eseguire
`market-scan --mode initial` dopo che tutti gli scope della base sono completi.

Il sito limita le pagine accessibili. Se il conteggio supera tale capacità
oppure `max_pages_per_search`, la ricerca è rifiutata con un messaggio che chiede
di suddividerla per modello, anno o prezzo: nessuna troncatura silenziosa.
`max_pages` limita il lavoro del singolo comando, non la dimensione dell'archivio.
L'esaurimento delle ricerche configurate non dimostra copertura di tutto il mercato.
Le ricerche live non sono snapshot: promozioni, inserimenti e spostamenti possono
far saltare o ripetere ID durante la paginazione; ripassi sovrapposti sono necessari.

## Aggiornamenti quotidiani

```sh
python -m deal_finder.worker collect-autoscout24 \
  --config examples/autoscout24_config.json --mode incremental \
  --run-id autoscout-2026-10-06 --max-pages 100
```

Incremental legge gli indici delle ricerche configurate e scarica i dettagli solo
degli ID assenti dall'archivio. Non riscarica tutte le schede delle auto già note.
Duplicati tra pagine/ricerche della stessa base sono saltati dopo il salvataggio.
Non termina al primo ID noto: annunci sponsorizzati possono alterare l'ordine.
Non dichiara venduta/rimossa un'auto che non compare più nei risultati.

Il solo ciclo new-only **non aggiorna prezzi o disponibilità dei vecchi ID**:
per il refresh mirato usare i task Apify `refresh` esistenti, oppure una nuova
raccolta initial con un run-id nuovo e uno scope ridotto. Non cambiare lo scope
di una raccolta che deve riprendere. Le osservazioni precedenti restano immutabili.

## Dentro l'API e nel ciclo Render

Impostare `DEAL_FINDER_AUTOSCOUT24_CONFIG` sul server con il JSON della
configurazione. POST `/collection/autoscout24` richiede il bearer token già
usato dall'API, quando configurato, e riceve:

```json
{"run_id":"autoscout-base-01","mode":"initial","max_pages":1}
```

Il numero massimo per richiesta HTTP è 5 pagine. La risposta restituisce
complete/checkpoint/accepted/quarantined. Ripetere con lo stesso run-id per
continuare; verificare anche GET `/collection/runs/autoscout24/native-RUN_ID`.
Per raccolte ampie usare il worker: anche una pagina con 20 schede può richiedere
molto tempo, con timeout HTTP/proxy. Le operazioni non partono importando l'API.

`daily-cycle` accetta la configurazione nativa nell'env sopra oppure nella chiave
`autoscout24` di `DEAL_FINDER_COLLECTION_CONFIG`, accanto a `tasks`. Può funzionare
senza task/token Apify. Usa un run-id stabile per configurazione e giornata UTC;
`--cycle-id 2026-10-06` permette di riprendere una giornata precedente. Per il
bootstrap: `daily-cycle --mode initial`. La selezione parte solo a raccolta completa.
Il template Render espone la configurazione nativa; l'attivazione dei servizi
e la configurazione delle ricerche nell'account di hosting restano operazioni distinte.

## Dati e precisione

Il formato è verificato sull'HTML pubblico di ricerca e dettaglio del 05/10/2026.
Ogni evento conserva dati originali, fonte/ID/link, ora di osservazione,
descrizione, URL delle foto e provenienza dei campi normalizzati. La data di
creazione, quando presente, è separata dall'osservazione. Non vengono copiati
automaticamente i file delle foto.

Gli importi vengono classificati come totale solo con valuta euro e indicatori
espliciti compatibili; prezzi condizionati, su richiesta, rate o anticipi restano
esclusi dai benchmark. Il chilometraggio deriva esclusivamente da un campo km;
allestimento/generazione non vengono dedotti dal titolo o dall'anno. Il testo
della versione è conservato a parte. La provincia non viene indovinata dalla città.
Lo storico incidenti non dimostra l'assenza di problemi meccanici attuali.

Specifiche mancanti rimangono mancanti: l'archivio può essere utile anche quando
il benchmark preciso è ancora bloccato e serve arricchimento documentato.
I valori pubblicati dal venditore non sono attestazioni di identità, ispezioni
o prezzi di vendita conclusa. Lo scraper non abilita previsioni o pubblicazione.

Verifiche: `python -m unittest discover -s tests -p 'test_autoscout24.py' -v`.
I test non fanno richieste online. Per il controllo live usare uno scope piccolo
e un database di verifica distinto dalla produzione; poi controllare qualità
prima di estendere la raccolta.
