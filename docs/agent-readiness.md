# Base operativa degli agenti — revisione del 6 ottobre 2026

Contratto `agent-readiness-v0.8`. La preparazione comprende codice, procedure e
test di regressione: non equivale a fine-tuning, calibrazione o collaudo pratico
di un modello sulle auto reali. Le prove pratiche vengono dopo questa revisione.

## Ordine di lavoro

1. Raccolta: originali, titolo, descrizione, link, tutti i collegamenti foto,
   dati disponibili, fonte, date e checkpoint nell'archivio privato.
2. Qualità e primo contesto prezzi: gli annunci incompleti ricevono un piano
   persistente di arricchimento. Una famiglia di modello può avere un intervallo
   degli importi pubblicati e una priorità per prezzo basso; questo non è una
   valutazione della variante né un prezzo di vendita previsto.
3. Arricchimento: elenco dei campi mancanti, piano foto/annuncio/web, risoluzione
   di prezzo totale, variante, km e condizioni. Nessuna ipotesi sovrascrive gli
   originali. Un documento del veicolo può essere necessario per motore e opzioni.
4. Selezione precisa: confronti attuali della stessa fonte, specifiche coerenti,
   anno/km, condizione e prezzo totale. Sconto provvisorio da P25 → verifiche.
5. Identità e danni: controllo delle contraddizioni foto/annuncio prima di
   ispezione, preventivi e conti. Distinguere sintomo, danno visibile, diagnosi,
   riparazione richiesta e distinta pezzi. Danni nascosti restano irrisolti.
6. Ricambi: OEM/codice produttore prima; confronto fornitori, compatibilità,
   contenuto kit, IVA, disponibilità, confezioni e quantità. Stima provvisoria
   bassa/centrale/alta da fonti; meno evidenza → intervallo più ampio.
7. Ore: intervalli per operazione con fonte/ipotesi; prezzo manodopera `null`.
   Tempi senza fonte restano `null`, mai zero. Smontaggi comuni possono sovrapporsi.
8. Costi e supervisore: scenari prima di manodopera/altri costi distinti da
   profitto netto. Rivendita e liquidità richiedono modelli calibrati su esiti.
9. Pubblicazione: soltanto opportunità approvate con evidenze e disponibilità
   attuale. Il catalogo, le candidature e i piani incompleti sono aree distinte.

## Collegamenti corretti

| Area | Prima della revisione | Risultato |
| --- | --- | --- |
| Archivio → prezzi | Bootstrap aspettava la fine della raccolta intera | Scheduler incrementale indipendente ogni 300 secondi, attivabile sul worker esistente |
| Annunci incompleti | Archiviati senza lavori dedicati | Fino a 100 piani per ciclo, idempotenti, ultimi eventi prima; input/risultati nella coda privata |
| Primo segnale prezzo | Richiedeva già tutte le specifiche | Triage della stessa famiglia/fonte con fonti e importi non verificati; serve solo a organizzare l'arricchimento |
| Foto contraddittorie | Controllo dopo i preventivi legacy | Contraddizioni bloccano ispezione/riparazioni/conti prima dell'esecuzione |
| Distinta danni | Confusione possibile con preventivo completo | `damage_scope` distingue ispezione documentata e ipotesi di pezzi; nessuno zero da dati assenti |
| Ricambi web | Soltanto query e calcolo di offerte fornite | Adapter server con ricerca web, schema, fonti consultate, massimo 8 pezzi, 12 chiamate strumento e 8.000 token per richiesta |
| Identificativo auto | Solo vehicle_id, spesso assente | Possibile riferimento esatto source/source_id/observed_at per ricerca provvisoria, senza attestare l'identità fisica |
| Offerta economica | Prezzo pezzo soltanto | Minimo osservato e minimo con costi di consegna/cauzione noti, distinti; costi ignoti mai zero |
| Promozione API | Non accettava parts_research | Lo accetta e il workflow mantiene la selezione prima della ricerca |
| Stato connessioni | Difficile da capire | GET /agents espone presenza/configurazione come booleani, senza credenziali |
| Filtri per interfaccia | Helper senza endpoint | GET /opportunities/candidates: marca/modello/provincia, prezzo/km e margine lordo provvisorio; esclude analisi/fonte superate |

## Attivazione

`DEAL_FINDER_AGENT_SCHEDULER_ENABLED=1` abilita soltanto proiezione, screening e
piani di arricchimento; nessun browser o provider a pagamento è avviato.
Il worker continua a elaborare la coda esistente. Riavvii non duplicano lotti.
I piani superati da nuovi eventi/rimozioni non avviano ricerca.
Le letture per arricchimento e contesto famiglia usano pagine da 100 righe:
gli originali vengono caricati solo per il singolo lavoro. Il catalogo delle
schede usa pagine da 50 e proiezioni JSON, evitando di caricare tutti gli input
con i loro confronti. I limiti sono per pagina; non troncano la base.

Foto/web: `OPENAI_API_KEY`, `DEAL_FINDER_VISION_MODEL` e
`DEAL_FINDER_PHOTO_IDENTITY_ENABLED=1`.

Ricambi/web: `OPENAI_API_KEY`, `DEAL_FINDER_PARTS_MODEL` e
`DEAL_FINDER_PARTS_WEB_ENABLED=1`. Il modello deve supportare Responses,
web_search e lo schema strutturato. Non è scelto né attivato automaticamente.
Il provider è a consumo; il limite per richiesta non è un tetto mensile in euro.
Non è stato abilitato per questo rilascio. Nessun nuovo servizio Render richiesto.

Per una candidatura selezionata, inserire `parts_research` nel lotto o nella
promozione dall'archivio. Specificare `web_search: true`, `vehicle` e `parts`.
Il veicolo deve corrispondere a modello/generazione/anno/cambio e all'identità
o allo snapshot della candidatura. Ogni pezzo richiede id, nome, quantità,
condizione, tier e requisiti noti. Il ricercatore costruisce offerte, analogia e
ore documentate. Non inventa una cifra universale senza fonti.

POST `/repairs/research` è un endpoint autenticato di ricerca diretta; il chiamante
conserva il risultato. Nel workflow della coda input, ricerca, fonti, offerte
accettate/scartate e stima sono automaticamente conservati in `agent_runs`.
I collegamenti foto sono conservati; i file immagine non vengono ancora copiati.

## Procedura ricambi e regressioni

La procedura applicata dal prompt e dai controlli comprende:

- codice OEM/produttore/EAN, generazione, motore, cambio, data produzione;
- lato, diametro, lampada/LED, connettori, sensori, contenuto kit/CSC;
- originale, aftermarket e usato in panieri separati;
- prezzo acquistabile IVA inclusa, disponibilità, quantità per confezione;
- venditore effettivo, alias AUTODOC e una sola evidenza per venditore/prodotto;
- osservazione più recente o minimo a pari data per duplicati dello stesso SKU;
- costo pezzi e costo anticipato con consegna/cauzione distinti;
- nessuna offerta da URL inventato/non consultato, nessun prezzo vecchio/futuro;
- analogie documentate quando dati incompleti, intervalli ampi e ipotesi visibili;
- nessun tentativo di aggirare rifiuti del sito, nessun acquisto o messaggio;
- nessun costo di manodopera e nessuna somma automatica delle ore;
- contraddizioni, fonti mancanti e risposte malformate restituiscono stati espliciti;
- una sola POST al provider, senza ripetizioni automatiche che possano addebitare.

I test sono casi sintetici e HTTP simulato, con controlli aggiuntivi PostgreSQL
su database usa-e-getta in CI. Non sono risultati di accuratezza su veicoli reali.
Per il collaudo successivo: registrare identità/codici verificati, prezzi
acquistabili, distinta completa e tempi documentati; congelare la previsione
prima della verifica, misurare errore e sottostima per ricambio/variante.

## Limiti da risolvere con il collaudo successivo

L'archivio live contiene soprattutto annunci senza trim e con prezzo totale non
ancora classificato: l'arricchimento è necessario e il sistema lo segnala.
Il modello foto e il modello ricambi richiedono configurazione e prove pratiche.
Un piano di arricchimento non produce da solo dati tecnici verificati: gli
esiti vanno risolti con nuove evidenze della fonte/documenti e nuovo screening.
La diagnosi da sole foto non certifica una distinta completa. Identificazione
esatta, compatibilità e fonte acquistabile vanno controllate prima dell'uso.
Modelli rivendita/tempi, interfaccia grafica finale e feed pubblico restano da completare;
nessuna previsione di guadagno o precisione perfetta viene abilitata.

Fonti tecniche controllate il 6 ottobre 2026:

- https://developers.openai.com/api/docs/guides/tools-web-search
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://www.auto-doc.it/services/consegna
- https://www.auto-doc.it/services/cgv
- https://www.bremboparts.com/europe/it/supporto/car/come-scegliere-il-prodotto-giusto/come-identifico-il-prodotto-corretto-da-montare-sulla-mia-auto-usando-le-funzioni-di-ricerca-di-brembo-parts-93377
