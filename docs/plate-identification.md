# Preparazione identificazione veicolo da targa

Ricerca del 6 ottobre 2026. Vincolo finale dell'utente: accesso GRATUITO,
nessun servizio a pagamento e nessun overage automatico. Nessun account
acquistato, token configurato o lookup reale effettuato. Il collegamento
automatico non è ancora attivo: condizioni e quota gratuite vanno verificate.

## Servizi verificati

| Servizio | Evidenza | Da verificare prima dell'attivazione |
| --- | --- | --- |
| Openapi Automotive IT-car | REST documentata, bearer token, sandbox con dati fittizi | Campi restituiti sulle auto del campione, contratto di uso/caching, budget e token |
| TargApp / Visura targa ITA | Sito del fornitore documenta API REST su RapidAPI | Schema completo, prezzi attuali del piano, VIN/motore/cambio effettivi |
| Targa Scan | Scheda RapidAPI trovata; contenuto tecnico non leggibile nel fetch | Endpoint, copertura, prezzi e modalità d'accesso: non confermati |
| Motornet | Ricerca targa e banche dati professionali dichiarate | Specifica API, costo, campi e condizioni commerciali da richiedere |

Openapi è escluso dalla scelta attuale perché a pagamento. Listino pubblico IT-car:
0,40 euro + IVA a chiamata singola. I piani annuali mostrano prezzi unitari
diversi in base al volume; il prezzo pubblicizzato da 0,18 euro riguarda il
piano da 200.000 chiamate/anno, non il consumo singolo. Prezzi da ricontrollare.
Venti mila richieste singole costerebbero 8.000 euro + IVA: per il prototipo
identificare un campione o le candidate, non l'intero archivio automaticamente.

L'esempio ufficiale contiene VIN, KType e cilindrata vuoti. Marca/modello/versione
non dimostrano tutti i codici motore, cambio, impianto frenante o optional.
La data di immatricolazione non è la data di produzione. Non promettere che
una targa identifichi in ogni caso il ricambio esatto.

Fonti ufficiali consultate:
- https://console.openapi.com/it/apis/automotive/documentation
- https://openapi.com/products/italian-car-check
- https://www.visuratarga.it/api/
- https://rapidapi.com/duepuntotre/api/targa-scan
- https://www.motornet.it/prodotti/targa
- https://www.motornet.it/prodotti/banchedati

## Flusso da collegare

1. Prendere targa fornita o visibile; se OCR incerto, conservare alternative e
   chiedere verifica prima di inviare una targa ambigua al fornitore.
2. Consultare prima le identificazioni già salvate, secondo durata del caching
   consentita dal contratto. Non fare una nuova richiesta per ogni agente.
3. Invocare il provider scelto con limite persistente di richieste/spesa e lock
   per targa; non fare retry automatici indiscriminati a pagamento.
4. Salvare un'osservazione separata, immutabile: ID annuncio, targa, provider,
   risposta originale, dati normalizzati, timestamp del provider e ora di lettura.
   Non sovrascrivere la dichiarazione originale del venditore. Evidenziare conflitti.
5. Distinguere sandbox, dichiarazione del provider e attestazione verificata.
   Conservare VIN, KType, versione, potenza, cilindrata e carburante quando presenti;
   codice motore/cambio/produzione restano sconosciuti se non restituiti.
6. L'agente ricambi riceve questa scheda e gli attributi specifici del pezzo.
   In assenza di dettagli sufficienti elabora varianti e stima più larga;
   non presenta il pezzo più economico come sicuramente montabile.

## Scelta gratuita e prossimo passo

Priorità a Targa Scan. La sua app è gratuita, ma il fetch della scheda API e
del pricing RapidAPI non restituisce il contenuto del piano. Non è verificata
una quota gratuita né un uso automatico gratuito su 20.000 targhe.
TargApp dichiara piani gratuiti e a pagamento e rinvia a RapidAPI: la quota
specifica non è leggibile nei risultati consultati. Non promettere uso illimitato.

Verificare nel pricing del portale quota, eventuali overage, rinnovo, endpoint
e campi. Ammissibile solo un piano a costo zero, con blocco prima del limite
e senza fallback a pagamento. Il software deve attendere il rinnovo della
quota invece di sostenere costi. Richieste ripetute vanno evitate con cache
consentita dal provider e identificazione mirata sulle candidate.

Se nessun accesso automatico gratuito è disponibile, mantenere l'identificazione
da dati precisi dell'annuncio, foto del libretto o risultato di consultazione
gratuita caricato dall'utente; non dichiarare equivalenza a un lookup autonomo.

Prima della messa in servizio: provare un campione di circa 30–50 targhe con
documenti forniti dal titolare, misurare campi mancanti/conflitti, implementare
archivio/cache e quota persistenti, poi collegare il worker. I test sintetici
verificano l'integrazione, non la correttezza dei dati reali.
