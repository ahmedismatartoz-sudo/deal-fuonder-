# Agenti Deal Finder — contratto operativo v0.2

Questi sono moduli eseguibili, non descrizioni di prompt. I nove agenti sono
registrati in `GET /agents` e `python -m deal_finder.worker agents`.
Il codice è pronto per l'alimentazione e per prove locali. Non ci sono ancora
scraper, servizi cloud accesi o modelli di vendita addestrati su dati reali.

## Compiti e blocchi

| Agente | Riceve | Produce | Quando si ferma |
| --- | --- | --- | --- |
| Intake | Lotto con batch_id e record | Originali, ricevuta e lavori in coda | Lotto troppo grande o ID lotto riusato con contenuto diverso |
| Quality | Annuncio originale | Annuncio normalizzato o quarantena | Campi mancanti, tipi errati, specifiche sconosciute, timestamp invalido |
| Identity | Annuncio e attestazione di identità | Collegamenti tra fonti e conflitti | Identità senza evidenza o specifiche incompatibili |
| Market | Target, comparabili e attestazioni | Mediana ponderata, quartili, pesi e URL | Target scaduto/superato, meno di 8 comparabili, campione debole o disperso |
| Condition | Ispezione documentata | Elenco esplicito delle riparazioni necessarie | Ispezione assente/incompleta, scaduta, di altra auto o incoerente |
| Repair | Ispezione e preventivi | Costi min/max del ripristino | Preventivi incompleti, scaduti, senza manodopera o per altra auto |
| Opportunity | Benchmark, ripristino e costi operativi | Scenari economici sui prezzi richiesti | Una categoria di costo non documentata o una dipendenza bloccata |
| Validation | Risultati di tutti i passaggi | Stato analisi e lista delle evidenze mancanti | Le previsioni di vendita restano disabilitate finché manca un modello calibrato |
| Evaluation | Previsioni congelate e vendite documentate | MAE, errore percentuale, P90, bias e copertura | Pochi veicoli distinti, contaminazione con training, esiti non documentati o dati futuri |

## Sequenza di ricezione

1. Intake salva tutti i record originali, compresi quelli invalidi.
2. Normalizza l'intero lotto e inserisce gli snapshot prima di renderlo visibile ai worker.
   Il primo lavoro può quindi usare anche i comparabili arrivati alla fine dello stesso lotto.
3. Ogni record genera un lavoro persistente. Quality ricontrolla il contratto.
4. Identity e Market elaborano le loro evidenze; Condition, Repair e Opportunity
   eseguono soltanto i passaggi con dipendenze disponibili.
5. Validation riunisce tutti i blocchi. Ogni esecuzione conserva input completi,
   cutoff temporale, hash, versione e risultati per essere riprodotta.
6. Nuove informazioni possono essere inviate come un nuovo lotto. Il comando
   replay rianalizza un lotto con il mercato aggiornato e crea nuove esecuzioni,
   senza sovrascrivere quelle precedenti.

Gli stati degli agenti sono completed, blocked, needs_review, quarantined, failed.
`completed` significa che quel compito è stato eseguito: non certifica che una
macchina sia un affare. Un lavoro `done` può avere agenti bloccati perché mancano
informazioni. Questo caso non è un errore tecnico da ritentare automaticamente.

## Formato dei record

Un lotto JSON contiene `batch_id` e `records`. Ogni record contiene:

- `listing`: tutti i campi in `models.Listing`. Prezzo richiesto intero in euro,
  observed_at ISO 8601 con fuso. Source ID e vehicle ID mantengono maiuscole e minuscole.
  Specifiche mancanti o placeholder sconosciuti vanno in quarantena.
- `identity_evidence`: vehicle_id, verified=true, verified_by, evidence_url HTTPS,
  verified_at con fuso. La prova si riferisce allo snapshot ricevuto.
- `inspection` facoltativo: vehicle_id, inspected_at, verified=true, complete=true,
  verified_by, evidence_url e required_repairs, anche vuoto se nessuna riparazione.
- `repair_quotes` facoltativo: un preventivo completo per ogni repair_id
  dell'ispezione, con vehicle_id e scope=parts_and_labor, oltre ai campi costo sotto.
- `operating_costs` facoltativo: esattamente transfer, transport, preparation,
  warranty, taxes, fees, contingency. Una voce non applicabile richiede comunque
  un valore zero esplicito e la motivazione/evidenza del verificatore.

Ogni costo contiene low_cents/high_cents interi, currency=EUR, tax_included=true,
quoted_at, valid_until, verified=true, verified_by ed evidence_url HTTPS.
I due estremi possono coincidere. Non si sommano automaticamente ricambi separati
senza preventivo di manodopera. Le stime di costo non vengono create da un LLM.

Gli allegati e l'autenticità delle attestazioni non sono verificati autonomamente:
`verified` rappresenta una dichiarazione di un operatore autorizzato. Prima di
una ricezione pubblica servono autenticazione, ruoli e verifica di chi attesta.
URL e metadati sono conservati come evidenze; il servizio non scarica gli URL.
Non assegnare identità solo perché due annunci hanno lo stesso anno/km/prezzo.

## Benchmark e scenari

Il Market Agent ammette solo comparabili con identità attestata e criteri di
somiglianza esatti, più tolleranza ±1 anno e ±20.000 km. Usa l'ultima osservazione
per fonte/annuncio disponibile al cutoff, anche se la nuova osservazione è
inattiva o corregge l'allestimento: la vecchia non viene riutilizzata.
Le soglie sono iniziali e provvisorie; nessuna precisione percentuale è promessa.

Lo scenario basso è P25 dei prezzi richiesti meno prezzo d'acquisto richiesto
meno l'estremo alto di tutti i costi. Lo scenario alto usa P75 ed estremo basso.
Sono scenari di confronto, non probabilità, intervalli predittivi o margini attesi.
`forecast_profit_cents` è null, `buy_recommendation` e `forecast_ready` sono false.
Sono ammessi anche scenari negativi: il sistema non forza risultati positivi.

## Coda e recupero

SQLite con WAL, transazioni e indici funziona per sviluppo locale. Ogni claim
assegna un token e una lease di 300 secondi. Un worker che perde la lease non può
pubblicare risultati. Le lease scadute tornano in coda; dopo 3 tentativi tecnici
falliti/scaduti il lavoro passa a `dead`. I tentativi hanno attesa esponenziale.
`drain` termina quando non ci sono lavori immediatamente disponibili; eseguirlo
nuovamente permette di raccogliere i retry. Il comando `run --poll-seconds 2` resta in ascolto; non è ancora installato come servizio cloud.

Limite lotto: 5.000 record / 20 MB. I risultati sono paginati a massimo 100 lavori.
Non è un collaudo della piattaforma su 100.000–200.000 auto. Per quel volume:
implementare l'adapter PostgreSQL, worker gestiti con heartbeat, partizionamento,
monitoraggio, backup e prove di carico. Gli schemi PostgreSQL sono predisposti;
il runtime operativo è ancora SQLite. Le esecuzioni salvano una copia del pool
candidato per riproducibilità: la crescita dello storage va misurata prima del cloud.

## Valutazione della precisione

Evaluation è separato dalla pipeline: non si esegue se non sono arrivati veri esiti.
Ogni record richiede vehicle_id, model_version, training_cutoff, predicted_at,
sold_at, predicted_cents, sold_cents, interval_low_cents, interval_high_cents,
segment, data_kind=observed_transaction, verified/verified_by/evidence_url.
Il richiedente fornisce anche gli ID dei veicoli di training per escluderli.

Controlli: almeno 30 veicoli distinti, cutoff training non successivo alla previsione,
previsione anteriore alla vendita, vendita non futura e modello coerente.
Qualsiasi esito invalido blocca il report di rilascio, pur mostrando le metriche
esplorative sui dati ammessi. Segmenti con meno di 30 auto restano insufficienti.
Le identità e le date dei record devono essere verificati da un operatore.

Le metriche non abilitano automaticamente le previsioni. Il passo successivo è
costruire una base di transazioni reali, verificare le evidenze e fissare soglie
accettabili per segmento. Non esiste ancora un modello di vendita da certificare.

## Aggiornamento v0.3

Adapter PostgreSQL e migrazioni ora implementati. Il runtime seleziona PostgreSQL
quando DEAL_FINDER_DATABASE_URL è configurata. Le note SQLite sopra descrivono
il percorso locale; per il database centrale seguire docs/postgres.md.
