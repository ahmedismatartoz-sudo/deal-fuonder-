# Priorità: agenti di mercato, selezione e ricerca opportunità

Requisito dell'utente, 6 ottobre 2026: il nucleo del prodotto è l'esperto dei
prezzi di mercato. La raccolta alimenta l'intera base comparativa. Prima delle
verifiche approfondite deve esserci uno screening che invia solo candidature
promettenti agli agenti di danni, ricambi e controlli.

## Sottoagenti del coordinatore mercato

| Responsabilità | Risultato richiesto |
| --- | --- |
| Identità e varianti | Modello, generazione, motore, cambio, allestimento e dati mancanti; targa solo con accesso gratuito verificato |
| Confronti | Auto realmente confrontabili, anno, km, venditore, zona, fonte e data; deduplicazione documentata |
| Chilometraggio | Valore per pochi, medi e tanti km, stimato dai confronti e con copertura dichiarata |
| Condizioni | Valore distinto per ottima condizione, usura, danni estetici, incidente, difetto meccanico e condizione sconosciuta |
| Valore dopo preparazione | Prezzo ipotetico da rivendita per il veicolo nelle condizioni finali dichiarate |
| Valore nello stato attuale | Prezzo delle auto danneggiate analoghe, evitando equiparazioni con vetture sane |
| Zona e andamento | Differenze territoriali e cambiamenti osservati; tempi di vendita solo se esistono esiti verificati |
| Supervisione confronti | Numerosità, dispersione, fonti, conflitti e ampiezza intervallo; astensione da falsa precisione |

Tutti sono ruoli pianificati, non modelli già addestrati. I prezzi degli annunci
sono prezzi richiesti e non vendite concluse. Gli sconti per km/condizione non
devono essere percentuali arbitrarie applicate uguali a tutte le auto.

## Passaggio della candidatura

Il selettore prende prezzo richiesto, intervallo di mercato nelle condizioni
attuali e intervallo di rivendita ipotetica. Produce decisione, motivi, potenziale
lordo, dati mancanti e priorità di verifica. Una candidatura promettente non è
ancora una grande opportunità accertata. Dati insufficienti creano una coda
di arricchimento, non un prezzo inventato né uno scarto definitivo dell'auto.

Le candidature passano a identificazione approfondita, controllo condizioni,
diagnosi/scenari, ricerca ricambi web, ore di lavoro separate senza prezzo della
manodopera, costi accessori, revisione finale e controllo dell'annuncio attuale.
Solo gli esiti che superano la policy entrano nella lista corrispondente al loro
stato: candidatura, stima provvisoria, opportunità con verifiche completate.

Non sottrarre due volte il danno: il valore nello stato incidentato è un confronto
separato; il calcolo acquisto→riparazione→rivendita parte dal prezzo d'acquisto
effettivo e dal valore nella condizione finale, poi sottrae le voci applicabili.
Se la manodopera non è valorizzata, non chiamare il risultato utile netto.
Costi mancanti restano evidenziati, non diventano zero. Il rango deve considerare
anche la sottostima dei costi e la debolezza dei confronti, non solo il massimo
profitto ipotetico. Nessuna percentuale di precisione viene dichiarata senza test.

## Filtri della futura interfaccia

Filtri e ordinamenti sul catalogo/lista: marca, modello, generazione, prezzo
d'acquisto, chilometraggio, anno, carburante, cambio, zona/raggio, privato o
concessionario, condizione, ricambi stimati, ore, potenziale lordo in euro e
percentuale, qualità dei confronti, livello di incertezza, data e stato verifica.
Margine minimo deve specificare quale estremo dell'intervallo usa. Valori ignoti
non vanno ordinati come zero. Gallerie e URL originali restano raggiungibili.

I filtri vanno definiti nello schema già ora; non richiedono di attendere migliaia
di opportunità per essere progettati. La quantità mostrata deve riflettere lo
stato realmente analizzato e non trasformare tutte le auto raccolte in opportunità.

## Stato del codice e prossimo sviluppo

Esistono `Market.sync/scan/screen`, confronti per coorti, storico delle valutazioni
e coda delle candidature. Lo screening attuale confronta il prezzo con P25 dei
prezzi richiesti e applica un criterio iniziale di sconto; non implementa ancora
tutti i ruoli sopra né una valutazione esperta calibrata per ogni condizione.
La pipeline attuale evita condizioni/ricambi/opportunità per auto che non passano
la selezione prezzi. Il nuovo modulo ricambi è provvisorio e richiede offerte
ricercate dall'esterno; la ricerca web automatica deve ancora essere collegata.

Ordine di lavoro: identità/normalizzazione e copertura confronti; test benchmark
per segmenti di km e condizioni; selettore e coda; connessione alle verifiche;
lista con intervalli/stati; filtri dell'interfaccia. Validare su auto tenute fuori
dallo sviluppo e registrare errori per segmento, non soltanto una media generale.
