# Market agents and data handoff

Implemented in `market_experts.py`, `handoff.py`, workflow and archive screening.
Worker analysis persists the full versioned outputs in the existing shared agent-run archive; no new database or parallel collector is needed.

| Stage | Input | Output and route |
| --- | --- | --- |
| Collection | Original listing, title, description, photo URLs, source and observation date | Immutable originals; missing specifications remain missing |
| Market coordinator | Normalized vehicle and available comparable pool | Six specialist results: specs, mileage, condition, geography, evidence, selector |
| Selector | Current total asking price €1,000–€50,000, known condition, sufficient comparable benchmark | At least 10% below observed lower quartile → verification; missing benchmark or unknown condition → enrichment; others → screened out |
| Parts handoff | Selected candidate; matching vehicle ID/make/model/generation/year/gearbox; `parts_research` request | Existing parts estimator runs; provisional interval, provenance and hours per operation; no labor price |
| Supervisor | Identity, inspection, costs, availability and model validation | Candidate cards remain provisional; verified opportunity publication retains its gates |

The six specialists are deterministic product modules, not fine-tuned AI models. Mileage bands are descriptive: <60,000 km, 60,000–149,999 km and ≥150,000 km. No fixed km depreciation or location adjustment is invented. Each band still matches vehicle specs/year and nearby km around a cohort median. Input pool coverage limits which bands can be estimated.

Healthy and damaged comparable prices are separate. The healthy reference for a damaged car is not a repaired resale forecast: damage severity, inspection, accident history and repair quality remain unresolved. Excellent vs worn, cosmetic vs mechanical damage cannot be inferred from the existing three-state condition field; those details are requested for enrichment. Sparse comparable data gets a provisional min/max observed envelope where observations exist; this is not a guaranteed future sale-price interval. With no matching observations, no price is fabricated; collection/enrichment must supply sourced analogies.

## Request/response contract

Existing queued analysis/API requests accept the same listing envelope. Optional `parts_research` uses the format documented in `repair-web-research.md`, plus `vehicle.vehicle_id`. The identity fields must match the candidate. Supplying parts research never bypasses screening. Inputs are external observations, not automatically fetched prices.

New persisted output keys:
- `market_experts`: specialist evidence, route and next tasks.
- `parts_research`: blocked/waiting/needs_review/provisional, sourced parts intervals and individual hours.
- `candidate_card`: original title, description and photo links, price/km/specs, status, nullable parts/gross-margin values.
- `handoff`: task list, filter fields, archive requirement, free-only plate policy.

`filter_cards` supports equality filters by make/model/generation/fuel/gearbox/province/seller/condition/route and min/max purchase price/km plus minimum conservative gross amount. Cards also expose gross percentage. Unknown numeric values never pass numeric filters. Hours are not additive because operations can overlap. Potential gross equals healthy asking-price scenario minus acquisition and parts only; it excludes labor, other costs and unlisted damage and is never net profit.

The old human-attested complete repair quote control remains distinct: it still requires parts + labor to produce its legacy all-cost scenario. The new parts-only output never substitutes for it or falsely satisfies publication controls.

## Deployment limits

Revisione successiva: [agent-readiness.md](agent-readiness.md). Lo scheduler
ora collega archivio, screening e piani persistenti di arricchimento; il
ricercatore ricambi server è implementato e richiede opt-in/configurazione.

No paid plate lookup is enabled. A fully free unlimited lookup is not connected.
Enrichment jobs preserve missing-field and photo/web plans without starting paid
calls or granting identity attestations. The parts web adapter is opt-in and
configuration remains explicit. Current archive scanning queues selected
candidates; incomplete observations receive separate enrichment plans. Existing
opportunity publication and GUI are not implemented by the candidate-card filter helper.

Validation: full unit suite plus disposable Postgres CI; tests cover missing specs, sparse evidence, unknown conditions, damaged/healthy separation, km bands, selection-before-parts, conflicting identity, separate hours and filter null/budget boundaries.
