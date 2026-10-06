# Sette componenti e controlli interni

Contratto `agent-readiness-v0.8`. GET /agents e CLI agents espongono i sette
componenti; GET /agents elenca separatamente i controlli interni.
La revisione, l'ordine dei passaggi, la ricerca ricambi e le connessioni sono in
[agent-readiness.md](agent-readiness.md).
Gli esiti registrati mantengono le chiavi dei controlli precedenti e aggiungono
`components`, così benchmark e scenari restano ispezionabili.

| Componente | Compito eseguito nella base | Evidenze/blocchi |
| --- | --- | --- |
| collection | Salva originali, quarantena, storico e checkpoint; riprende export e dataset Apify paginati | Task/scope/mappatura e segreti da configurare |
| market_selection | Scansione della base e confronto provinciale/nazionale; selezione 1.000–50.000 € | Prezzo totale, minimo 8, sconto provvisorio ≥10% da P25; archivio usa prezzi dichiarati della stessa fonte, manuali identità attestate |
| repairs | Legge ispezione e somma preventivi di ricambi/manodopera | Auto selezionata, identità attestata, ispezione completa, tutti i preventivi validi |
| resale | Contratto per prezzo consigliato/intervallo/modello | Bloccato: nessun modello addestrato e calibrato su vendite reali |
| opportunity | Espone scenari sui prezzi richiesti e contratto profitto/liquidità | Profitto previsto, popolarità e tempi null senza modelli e costi completi |
| supervisor | Riunisce qualità, identità, selezione, ripristino, costi e blocchi modello/disponibilità | approved=false finché mancano evidenze richieste |
| publication | Esito publishable e anteprima di feed con motivi di esclusione | Supervisore, gates, profitto positivo, analisi/fonte entro 24 ore, fonte attiva e snapshot corrente |

I filtri sono query deterministiche sul catalogo, non agenti linguistici.
Non occorre un agente diverso per ogni città. Il filtro radius usa distanza
geografica; city è una corrispondenza esatta normalizzata. Nessuna posizione è
inventata: senza coordinate un annuncio non entra in una ricerca per raggio.

## Raccolta e analisi sono distinte

L'archivio accetta annunci incompleti. L'evento contiene source_id, observed_at,
url, active e payload originale. Il payload può conservare titolo, descrizione,
image_urls, specifiche, dati geografici e field_evidence. I collegamenti foto
sono conservati; i file fotografici non sono ancora scaricati o copiati in storage.

Il catalogo non è una lista di affari verificati. La promozione alla coda richiede
specifiche complete e prezzo classificato; attestazioni di identità, ispezioni,
preventivi e costi vengono forniti separatamente dall'operatore. Il contenuto
originale non può autoattribuirsi una verifica umana.

Nel lavoro di analisi, qualità/identità/mercato precedono la selezione.
Se l'auto non passa lo screening, condizioni/riparazioni/scenario vengono bloccati.
Chiavi legacy: quality, identity, market, condition, repair, opportunity, validation.
La validazione legacy può dire scenario_ready: non equivale ad approved del supervisore.

## Costi e precisione

Ispezione: vehicle_id, inspected_at, verified=true, complete=true, verified_by,
evidence_url HTTPS, required_repairs esplicite. L'assenza di ispezione non implica
ripristino a costo zero. Se non ci sono riparazioni, lo zero proviene dall'ispezione.

Ogni repair_quote deve coprire esattamente un repair_id, stesso vehicle_id e
scope=parts_and_labor. Costi: low_cents/high_cents interi, EUR, tax_included=true,
quoted_at/valid_until, verified/verified_by/evidence_url. Le attestazioni indicano
la dichiarazione di un operatore; la loro autenticità non viene verificata dal codice.

Sette costi operativi obbligatori: transfer, transport, preparation, warranty,
taxes, fees, contingency. Una voce non applicabile richiede uno zero documentato.
Non si presume disponibilità di una propria officina.

Il benchmark usa prezzi richiesti recenti e ultimi snapshot, escludendo annunci
inattivi, danneggiati, rate e anticipi. Specifiche e tipo venditore restano
comparabili anche nel fallback nazionale; la geografia non è calibrata.
P25/P75 descrivono il campione, non intervalli probabilistici.
Scenari: P25 meno acquisto e costi massimi; P75 meno acquisto e costi minimi.

## Calibrazione e responsabilità

Evaluation resta un controllo separato via POST /evaluations. Riceve previsioni
congelate e vendite documentate: identità, versione modello, cutoff training,
predicted_at/sold_at, prezzi effettivi/previsti, intervallo, segmento ed evidenza.
Esclude contaminazioni temporali e con il training; minimo 30 veicoli distinti.
Misura MAE, errore percentuale, P90, bias e copertura. Un report non abilita da solo
le previsioni. Prezzo e tempo di vendita necessitano di calibrazioni distinte.

La scomparsa di un annuncio non dimostra una vendita. Una diagnosi da fotografie
potrà suggerire verifiche; non certificherà danni nascosti o assenza di riparazioni.
Non esiste una garanzia di profitto.

## Coda e pubblicazione

Lotti: massimo 5.000 record/20 MB. Lease con token, tentativi tecnici limitati,
retry e storico immutabile degli input/risultati. Replay aggiunge nuove esecuzioni.
SQLite locale e PostgreSQL centrale implementati. Nessun test di carico nazionale
è stato eseguito. La copia dei comparabili negli input richiede misure dello storage.

Il ciclo [collection-cycle.md](collection-cycle.md) collega archivio e selezione
automatica. Il template Render predispone aggiornamento quotidiano e revisione
completa settimanale; le esecuzioni remote restano da attivare. L'agente prezzi
riceve i confronti dell'intera base negli input della coda, senza trasformare
le dichiarazioni del venditore in attestazioni.

L'anteprima considera l'ultimo lavoro per identità nel lotto indicato, ricontrolla
lo snapshot nell'archivio e motiva le esclusioni. Non scrive una lista pubblica,
non avvia un cron e non rianalizza da sola auto scadute. Quando saranno pronti
modelli e adapter, servono verifica link sul momento,
edizioni persistenti e sostituzione atomica della lista nell'app.
