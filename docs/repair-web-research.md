# Ricambi: ricerca web, stime provvisorie e verifica

Preparazione del 6 ottobre 2026. È un workflow deterministico con casi sintetici
e osservazioni web, non un fine-tuning né un modello di diagnosi già validato.
Non richiede dati o tariffe della singola officina. Il costo della manodopera
rimane `null`; le ore sono separate e non vengono sommate alla cieca, perché
smontaggi comuni possono sovrapporsi.

## Procedura dell'agente

Decisione successiva dell'utente: prima della ricerca ricambi, un agente separato
identificherà il veicolo attraverso targa e un portale autorizzato (integrazione
da valutare domani), oppure dati modello precisi già documentati. La scheda dovrà
conservare dati ottenuti, fonte, data, campi verificati e campi ancora mancanti:
modello/generazione, motore, potenza, cambio, produzione, VIN se restituito e
allestimenti rilevanti. Una risposta da targa non è automaticamente prova di ogni
opzione montata o compatibilità di un ricambio; VIN/codici PR e OEM possono essere
necessari. Non è stata collegata alcuna API targa e nessuna targa viene inventata
nei casi sintetici. L'accesso del portale alla consultazione web non implica API
o permesso di automazione disponibili: vanno verificati prima del collegamento.

1. Ricevere dagli altri agenti identità dell'auto, descrizione, foto, problemi
   dichiarati e dati mancanti. Distinguere componente mancante, danno visibile,
   sintomo e diagnosi confermata. Foto mancanti non provano assenza di danni.
2. Formare una distinta con pezzi sicuramente richiesti e scenari alternativi.
   Il preventivo del singolo componente non è il costo della riparazione completa.
3. Cercare prima codice OEM/EAN/codice produttore; poi modello, generazione,
   motore, cambio, mese di produzione, lato, misure, lampade, sensori e connettori.
   Il nome del modello o la sola cilindrata non dimostrano compatibilità.
4. Consultare AUTODOC, Mister-Auto, Norauto e Motointegrator; cercare il codice
   anche su altri venditori. Aprire la pagina del prodotto, non fermarsi allo snippet.
   Non aggirare 403, CAPTCHA o login. Una fonte illeggibile viene registrata.
5. Salvare prezzo acquistabile, venditore effettivo, valuta, IVA, disponibilità,
   quantità relativa al prezzo, contenuto del kit, condizione, data, URL e
   prova di compatibilità. Non usare PVC barrato, rate o sconti condizionati.
6. Confrontare separatamente originale, aftermarket e usato. Normalizzare la
   quantità acquistabile: due dischi da un prezzo unitario non costano un disco.
   Contare una sola volta lo stesso prodotto dello stesso venditore; distinguere
   il marketplace dal venditore effettivo. Ogni venditore pesa una volta nella mediana.
7. Fornire basso / centrale / alto. Se identità, diagnosi, disponibilità o prezzo
   aggiornato mancano, usare un'analogia documentata e un intervallo più largo.
   Il fallback richiede fonti e ipotesi: non è una media di mercato misurata.
   Gli intervalli provvisori non sono percentili calibrati né massimi garantiti.
8. Riportare a parte spedizione, ingombranti, resi del vecchio pezzo, materiali,
   vernice e lavori accessori. Un valore assente non deve diventare zero.
9. Per ore di lavoro richiedere una fonte tecnica o un'ipotesi esplicita; mai
   presentare le ore inventate nei test come tempario OEM.
10. Conservare stima iniziale e successiva verifica, senza sovrascrivere la prima.
    Aggiornare precisione sul codice, completezza distinta, errore in euro,
    sottostima e copertura dell'intervallo su casi indipendenti.

## Ricerca effettuata e trappole osservate

### Panda III 312/319 2015 1.2: faro sinistro mancante (auto inventata)

La pagina AUTODOC per la versione benzina espone PRASCO FT1244804 sinistro H4
a 104,99 euro e TYC 20-14126-05-2 sinistro H4 a 121,99 euro, con regolazione.
Il MAGNETI MARELLI 712470601129 a 125,99 euro è destro: scartarlo per questo caso.
Disponibilità non confermata nelle pagine lette: i prezzi alimentano l'analogia,
non un'offerta acquistabile attestata. Intervallo provvisorio del solo faro:
90–180 euro, margine ingegneristico esplicito, non intervallo appreso.

Fonte: https://www.auto-doc.it/pezzi-di-ricambio/faro-principale-gruppo-ottico-10533/fiat/panda/panda-312/14002-1-2-312pxa1a

Ricerca di confronto: Oscaro ha negato il fetch (403); Trodo non ha restituito
contenuto utile; la pagina SMC aperta non esponeva il prezzo visto nello snippet.
Non si è inventato un secondo prezzo verificato o un numero di fonti indipendenti.

### Golf VII 2015 1.6 TDI: due dischi anteriori (auto inventata)

Supponendo montaggio da 288 mm, BREMBO 09.9145.14: AUTODOC mostra 49,49 euro
per unità, quindi 98,98 euro per due; Norauto mostra 109,99 euro per la coppia.
La scheda del produttore conferma diametro 288 mm, spessore 25 mm ed EAN.
Il confezionamento del produttore NON sostituisce la quantità riferita al prezzo
del venditore. Variante 312 mm scartata. Serve codice PR/VIN per il montaggio
esatto; la famiglia Golf VII contiene impianti diversi. Pastiglie escluse.
Intervallo operativo ipotetico 75–160 euro, solo dischi e senza spedizione;
non è un totale freni o una garanzia di compatibilità.

Fonti:
- https://www.auto-doc.it/brembo/1657442
- https://www.norauto.it/p/2-dischi-brembo-referenza-09.9145.14-216244.html
- https://www.bremboparts.com/europe/it/view-document/catalogue/disc/09-9145-14

### Clio IV BH 2015 1.5 dCi 84 CV: frizione che slitta (auto inventata)

Nella pagina motore AUTODOC si leggono RIDEX 479C0109 a 64,99 euro, senza CSC,
e SACHS 3000 990 574 a 209,99 euro, con CSC. Non sono lo stesso paniere.
Il LuK 622 3096 33 a 138,99 euro compare in ricerche generiche ma la pagina
prodotto elenca Clio II/III, non conferma Clio IV, e riporta raccordo diverso:
non usarlo come compatibilità dimostrata. Sintomo di slittamento non prova
quali pezzi vadano sostituiti. Mese produzione e cambio sono ancora mancanti.
Ipotesi kit con CSC 150–300 euro; volano, olio e danni aggiuntivi esclusi.

Fonti:
- https://www.auto-doc.it/pezzi-di-ricambio/kit-frizione-10151/renault/clio/clio-iv/135335-1-5-dci-bhm6
- https://www.auto-doc.it/luk/623052

Le copie indicizzate restituite per la stessa categoria Clio contengono prezzi
e riferimenti differenti in letture successive: prova concreta che data di
consultazione non equivale ad aggiornamento del listino. I prezzi dei casi
sono osservazioni web per test, devono essere ricontrollati prima dell'acquisto.

## Codice e limite operativo

`deal_finder.repair_research` costruisce query e calcola stime da offerte ricevute.
API autenticate: POST `/repairs/search-plan` con vehicle/part;
POST `/repairs/estimate` con vehicle/parts. Tutte le stime sono `provisional`,
`calibrated=false`, `verified_quote=false`, senza abilitare previsioni di profitto.
`examples/repair_web_cases.json` contiene tre auto inventate e prezzi osservati.

Il modulo NON naviga autonomamente sul web e NON interpreta le fotografie.
Serve collegare un adapter di ricerca server al contratto delle offerte e un
modello multimodale per proporre gli interventi. La ricerca fatta in chat non
costituisce tale integrazione. Un fallback mancante è un errore di input: l'agente
ricercatore deve prima costruire scenari documentati, anche molto ampi.
Non esiste qui una cifra universale affidabile per qualsiasi guasto sconosciuto.

Prima dell'uso pubblico: verificare manualmente codice/quantità di ogni paniere,
misurare su casi tenuti fuori dallo sviluppo e calibrare gli intervalli sui prezzi
acquistabili. Poi valutare separatamente diagnosi e danni nascosti con ispezioni.
