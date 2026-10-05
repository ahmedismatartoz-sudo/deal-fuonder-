# Architettura del mercato nazionale

Il catalogo degli annunci è indipendente dalle valutazioni e dalla lista pubblicata.
Il mercato dei confronti può essere nazionale; il raggio scelto dall'utente filtra
le auto disponibili da vedere, senza cambiare automaticamente la metodologia prezzi.

## Dati persistenti

| Area | Tabelle operative | Funzione |
| --- | --- | --- |
| Raccolte | collection_pages, collection_quarantine | Scope, checkpoint, pagine idempotenti e originali invalidi |
| Catalogo | listing_events | Versioni immutabili, originali, rimozioni, metadati geografici e prezzi |
| Analisi | snapshots, identity_attestations, batches, raw_records, jobs, agent_runs | Dati normalizzati, evidenze, coda e input/risultati riproducibili |
| Precisione | evaluation_reports | Errori delle previsioni contro esiti documentati |
| Migrazioni | schema_migrations | Versione e checksum dello schema |

La migrazione 003 aggiunge tre tabelle allo schema privato esistente. RLS su tutte;
backend con SELECT/INSERT sul nuovo archivio, senza UPDATE/DELETE dello storico.
Nessuna modifica alle migrazioni già applicate.

Ogni pagina massimo 5.000 record/20 MB. Source/run/page identificano un contenuto
immutabile; la transazione salva eventi, quarantena e checkpoint insieme.
Un input_cursor deve continuare il checkpoint precedente. Il blocco PostgreSQL
serializza pagine concorrenti della stessa raccolta. Una pagina completata non
certifica che tutti gli annunci di un marketplace siano stati acquisiti.

Source/source_id identificano l'annuncio; vehicle_id con evidenza identifica
l'auto tra marketplace. L'ultimo evento precede i filtri, quindi una nuova
rimozione, variante o prezzo fuori budget elimina il vecchio risultato.
Una fonte deve inviare rimozioni esplicite: la sola assenza in un export non
viene trattata come vendita o cancellazione.

## Sequenza e precisione

1. Connector/CollectionAgent: raccolta iniziale paginata o aggiornamento incrementale.
2. Archive: originali conservati e consultabili; geografia e prezzo totale.
3. Promozione: specifiche complete verso Queue, attestazioni separate.
4. Screening prezzi: provincia, fallback nazionale segnalato, sconto da P25.
5. Ispezione e preventivi completi per le auto selezionate.
6. Modelli calibrati di rivendita/liquidità, conti, supervisione.
7. Anteprima di pubblicazione e successiva edizione mattutina quando i gates saranno pronti.

I componenti 6–7 hanno contratti e blocchi eseguibili; non hanno un modello
statistico di vendita o un pubblicatore cloud configurati. I controlli esistenti
di costi/scenari restano operativi senza trasformare prezzi richiesti in previsioni.

## Implementazioni successive

- Adapter reali per ogni fonte con accesso configurato, retry/rate limit e audit
  della copertura; nessun aggiramento di login o meccanismi di accesso.
- Storage dei file delle immagini con checksum e associazione allo snapshot.
- Geocodifica con provenienza/precisione e trattamento delle posizioni approssimate.
- Storico ricambi/preventivi: veicolo, riparazione, parti, ore, tariffa, imposte,
  zona, validità e fonte. Non copiare tariffe generiche a tutte le auto.
- Transazioni e date effettive documentate. Addestramento/verifica separati per
  tempo e veicolo. Errori e copertura per modello, fascia, zona e venditore.
- Versioni/approvazioni dei modelli, verifica disponibilità e termini d'acquisto.
- Scheduler Render, heartbeat, monitoraggio, backup e prove di carico;
  feed con edizioni atomiche e aggiornamento delle auto rimosse.

Nessuna precisione percentuale, copertura totale o guadagno garantito è dichiarato.
