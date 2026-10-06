# Opportunità prudenti e specialisti professionali

Contratto `professional-opportunity-v1`, 6 ottobre 2026. Il minimo richiesto
dall'utente è **2.000 EUR nello scenario prudente dopo i costi**. La ricerca
attuale resta Milano e acquisto strettamente sotto 20.000 EUR. Annuncio valido,+candidatura sottoprezzo e opportunità approvata sono stati diversi.

## Tre sottoagenti eseguiti nel workflow

- `announcement_anomalies`: prezzi condizionati a finanziamento, anticipi/rate,
  uso ricambi, documenti/chilometri incerti e identità discordanti. Il testo
  genera una richiesta di verifica, non una diagnosi di frode. Un segnale
  bloccante ferma la ricerca foto/ricambi/rischi prima di chiamare provider.
- `technical_risk`: gravità del danno e problemi documentati della variante.
  Un problema ricorrente non dimostra che sia presente sulla singola auto.
  Le dichiarazioni del venditore non possono abbassare una gravità rilevata.
- `independent_review`: ricalcolo deterministico separato dei costi/margini,
  revisione dei confronti e controllo di una seconda attestazione con revisore
  diverso. Non è un secondo modello AI addestrato e non certifica documenti.

Il coordinatore produce dipendenze, stati e prossimo lavoro disponibile;
non esegue chiamate a pagamento, acquisti o contatti. Le evidenze relative
a fasi successive possono essere presenti, ma non autorizzano a saltare
una fase precedente ancora bloccata. La coda esistente conserva input/output.

## Compiti specialistici concreti

| Area | Esecuzione aggiunta |
| --- | --- |
| Raccolta | Audit fino a 100 osservazioni della stessa identità; ribassi, rimozioni, segnali di foto condivise e identità duplicate nei confronti correnti. Lo storico completo rimane nell'archivio. Una rimozione non è una vendita. |
| Identità | Mantiene variante, contraddizioni, documenti e stati di evidenza; nessuna supposizione diventa attestazione. |
| Mercato | Confronti con specifiche, km, venditore, motivazione; audit recente delle identità fisiche e del prezzo; ricalcola il quartile sui confronti revisionati. |
| Danni | Organizza osservazioni con origine e riferimenti foto validi, distingue lavori necessari/estetici e prepara domande, senza inviarle. |
| Ricambi | Verifica codici, quantità, compatibilità, disponibilità, condizioni/tier e copertura consegna/cauzione; offerte incomplete o incompatibili sono scartate. |
| Ore | Operazioni documentate, copertura di tutti i lavori, materiali, gruppi di smontaggio condivisi e decisioni di sovrapposizione. Nessuna tariffa interna inventata. |
| Rivendita | Confronti di auto riparate con storia grave dichiarata; prezzi richiesti usati soltanto come limite provvisorio. Prezzo reale previsto resta disabilitato. |
| Convenienza | Costo alto, stress separati, riserva, margine prudente, capitale totale e prezzo massimo condizionato al perimetro dei costi correnti. |
| Supervisore/pubblicazione | Evidenze e controlli aggiuntivi; ricalcolo prima della pubblicazione e minimo di 2.000 EUR anche nel limite inferiore della futura previsione calibrata. |
| Precisione | Previsioni congelate/esiti documentati; sovrastima della rivendita e sottostima del ripristino, senza modificare automaticamente la policy. |

La memoria sono l'archivio, `agent_runs`, preventivi e report di valutazione:
non sono pesi di un modello addestrato. L'audit dei duplicati non pretende
copertura globale quando mancano identità fisiche verificate.

## Policy prudente esplicita

Sono ipotesi di selezione aziendale, **non probabilità calibrate**:

| Parametro | Ordinaria | Grave o danno non chiarito |
| --- | ---: | ---: |
| Confronti locali recenti e unici revisionati | 12 | 20 |
| Finestra dei confronti | 14 giorni | 14 giorni |
| Auto riparate con storia grave comparabile | — | 8 |
| Stress iniziale sul riferimento di uscita | −10% | −15% |
| Stress iniziale sul costo alto di ripristino | +25% | +40% |
| Ulteriore shock sul riferimento di uscita | −5% | −5% |
| Ulteriore shock sul ripristino alto | +15% | +20% |
| Riserva aggiuntiva | max(750 EUR, 15% ripristino alto) | max(2.000 EUR, 30% ripristino alto) |
| Orizzonte minimo di costi di giacenza | 45 giorni | 90 giorni |
| Margine prudente minimo | 2.000 EUR | 2.000 EUR |
| Rendimento minimo sul capitale totale | 25% | 30% |

La riserva è un buffer di policy aggiuntivo anche quando `contingency` è
documentata nei costi operativi. I sette costi devono essere presenti:
passaggio, trasporto, preparazione, garanzia, imposte, commissioni e imprevisti.
Nessuna voce sconosciuta vale zero. Uno zero deve essere documentato.

Il riferimento provvisorio ordinario è P25 dei prezzi richiesti. Per le gravi
è il minore tra quel riferimento e P25 delle auto riparate comparabili con
storia dichiarata. Senza questi ultimi confronti non si calcola il margine
della grave. Si applica lo stress al riferimento, si sottraggono acquisto,
preventivi al costo alto, costi operativi al costo alto, stress ripristino e
riserva. Non si usa il prezzo sano per fingere che la storia sparisca.

Si calcolano quattro scenari: base, ulteriore ribasso, ulteriore aumento dei
lavori e shock simultaneo. Il margine mostrato e il massimo d'offerta usano
lo scenario simultaneo peggiore: devono rispettare entrambi il minimo di
2.000 EUR e il rendimento minimo. Il solo margine base non basta.

Finanziamento/capitale, deposito e assicurazione hanno importi giornalieri
documentati, moltiplicati per almeno 45 giorni (90 per le gravi). Uno zero
richiede un motivo esplicito; una voce o un periodo ignoto blocca il conto.
L'orizzonte è uno stress di pianificazione, non una previsione del tempo di
vendita. Questi costi sono aggiuntivi ai costi operativi: se sono già inclusi
altrove, il dossier deve documentare il perimetro e gli eventuali zeri, per
evitare duplicazioni.

Il massimo d'offerta rispetta sia il margine assoluto sia il rendimento sul
capitale totale. È condizionato a costi e riferimenti correnti: una variazione
di tasse/preventivi/condizioni richiede una nuova analisi. Non è un'offerta
automatica né una raccomandazione di acquisto calibrata.

## Incidenti gravi

Una descrizione grave, indicatori struttura/airbag/incendio/alluvione/batteria
o `damage_severity=severe` attivano il percorso grave. `condition=damaged`
con gravità ignota mantiene gli stessi controlli finché una valutazione
documentata non la chiarisce. Foto e testo non possono certificare danni
nascosti né riparabilità.

Obbligatori: misurazione struttura, procedure OEM/riparabilità, sistemi di
ritenuta, ispezione danni nascosti, piano diagnostico/ADAS, storia e rivendita,
capacità e calendario della carrozzeria.

Serve un preventivo di **carrozzeria esterna identificata**, con perimetro
completo e righe distinte per smontaggio/misurazione, struttura, verniciatura,
materiali, ritenuta, diagnosi, ADAS, assetto e trasporto al fornitore.
Le righe non applicabili richiedono zeri documentati. Il totale deve
riconciliarsi con le righe e con i preventivi complessivi di ripristino,
senza contare due volte la stessa quota. Un'officina meccanica interna
non abilita uno sconto generico sui lavori di carrozzeria. Un preventivo
esterno mancante, aperto o incoerente blocca il conto del margine.

## Contratto delle evidenze

`professional_evidence` è accettato nei lotti autenticati e nella promozione
dall'archivio. Non viene letto come attestazione dal payload del venditore.
Ogni documento professionale deve contenere:

```json
{
  "source": "facebook_marketplace",
  "source_id": "ID_ESATTO",
  "observed_at": "TIMESTAMP_ESATTO_DELLO_SNAPSHOT",
  "vehicle_id": "ID_FISICO_SE_PRESENTE",
  "verified": true,
  "verified_by": "OPERATORE",
  "evidence_url": "https://fonte.example/documento",
  "checked_at": "2026-10-06T19:00:00+00:00"
}
```

Il codice registra attestazioni, non ne certifica l'autenticità. Le evidenze
scadute, future o di un'altra osservazione sono escluse. La nuova osservazione
richiede nuove evidenze: non si trasferisce silenziosamente un'approvazione.

Campi del dossier:

- `acquisition`: prezzo totale in centesimi, disponibilità, documenti e km
  controllati; conferma entro 24 ore; eventuali segnali risolti sono espliciti.
- `damage_assessment`: gravità none/minor/moderate/severe; nessun downgrade
  automatico di segnali gravi.
- `comparable_identities`: riferimenti esatti dei confronti, identità fisiche,
  specifiche revisionate uguali a quelle del confronto e prezzo revisionato.
- `model_risk_review`: variante esatta, copertura e almeno due fonti consultate;
  `model_risks`: problemi documentati e controlli specifici risolti sulla macchina.
- `collision_checks`: i sette controlli nominati in `professional.COLLISION_CHECKS`,
  con `passed=true`, findings e documento della stessa macchina.
- `bodywork_quote`: contratto `bounded_cost`, fornitore, righe nominate in
  `BODYWORK_LINES`, scope completo, repair_id coperti e totali riconciliati.
- `repaired_history_comparables`: variante, anno/km, venditore/geografia,
  identità unica, prezzo totale, disponibilità e storia grave dichiarata.
- `parts_plan`, `labor_plan`, `labor_overlap`, `repair_plan_review`: pezzi,
  offerte, operazioni, sovrapposizioni motivate e completezza. Prezzi dei pezzi
  non sostituiscono i preventivi completi parti+manodopera.
- `holding_plan`: documento dello snapshot, `planned_days` e `daily_costs`
  con esattamente funding/storage/insurance. Ogni costo giornaliero usa il
  contratto `bounded_cost` e `unit=day`; quando è zero richiede `zero_basis`.
- `damage_observations`, `work_classification`: origine, finding, riferimenti
  foto e distinzione lavori necessari/estetici; non certificano una diagnosi.
- `independent_review`: revisore diverso da quello primario, margine e capitale
  ricalcolati, motivazione e controllo entro 24 ore.

## Attivazione e limiti reali

Controlli deterministici attivi nel normale workflow. Il catalogo restituisce
`opportunity_status=research_only`, `high_opportunity_approved=false`, margine
prudente, massimo d'offerta e blocchi. `professional_policy_ready` indica che
la revisione prudente è completa, **non** che la pubblicazione sia approvata.

GET `/agents` espone compiti, policy e presenza dei collegamenti senza chiavi.
GET `/opportunities/candidates` permette filtri per margine prudente,
`severe_controls_required` e `professional_policy_ready`.

La ricerca tecnica web richiede `OPENAI_API_KEY`, `DEAL_FINDER_RISK_MODEL`
e `DEAL_FINDER_TECHNICAL_RISK_WEB_ENABLED=1`. Massimo 6 chiamate strumento,
4.000 token output, timeout 60 secondi; nessun retry a pagamento automatico.
Fonti non consultate sono scartate; risultati restano ipotesi da verificare.
Nessuna attivazione o spesa viene introdotta da questa modifica.

Foto e ricambi mantengono i propri opt-in. La precisione non è dimostrata dai
test sintetici. Servono configurazione dei provider e prove pratiche; prezzo
di rivendita, tempi di vendita e pubblicazione verificata rimangono bloccati
finché mancano modelli calibrati. Nessun numero di opportunità o guadagno
viene garantito. Soglie/stress vanno validati su esiti, non abbassati per
produrre una lista più lunga.

Riferimenti tecnici consultati (principi di riparazione, non norme italiane
né fonti dei buffer percentuali):

- https://rts.i-car.com/images/pdf/oem-info/oem_technical_information_matrix_x.pdf
- https://www.thatcham.org/wp-content/uploads/2020/07/006-IIR-Guidance-July-2020.pdf

I-CAR distingue procedure strutturali, ritenuta e calibrazione OEM; Thatcham
richiede procedure specifiche e documentazione delle verifiche e considera
l'esternalizzazione quando il riparatore non ha capacità adeguate.
