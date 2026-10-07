# Identity evidence and non-severe opportunity research

The worker's `DEAL_FINDER_FIRST_TEST_PROFILE=opportunities` profile selects research candidates across EUR 1,000–20,000 acquisition, with target proportions 30% clean, 20% minimal damage, 50% non-severe damage (6/4/10 for 20 results). It excludes critical restraint/structural/mechanical damage and unspecified damage. These classifications describe seller/source evidence; every result requires inspection. Prior accident history alone does not prove a clean reference.

`GET /price-tests/latest` returns the current report, identity coverage/conflicts/recovered fields, requested versus achieved damage counts, price-model validation, necessary economic budget, source evidence and queue execution. Optional filters: `price_band=0..3`, `damage_category=clean|minimal|non_severe`. Existing bearer authentication applies.

Identity extraction recovers named source scalars from retained originals without fetching new pages or transferring entire original blobs. It normalizes units and aliases, preserves unknown fields, detects conflicting horsepower/kW/text declarations, and records each field's claim source and recovery status. Explicit generation/trim labels in version text remain seller claims. Document field verification requires a reviewed, source-observation-bound identity attestation with `evidence_origin` and `verified_fields`; a vehicle-ID assertion alone cannot verify a motor. Open conflicts are never automatically cleared by an attestation.

Comparables used for opportunity research must be classified clean and have compatible declared variants with no identity conflicts. Ranking uses the remaining documented-cost budget ceiling weighted by statistical confidence, with turns across price bands and a preference for varied model families. Repeated target asking specifications are deduplicated. Insufficient eligible damage categories remain shortages; the system never substitutes severe/unknown vehicles to fill quotas.

The necessary budget is 85% of the lowest observed/adjusted comparable asking price, minus acquisition, EUR 750 minimum reserve, and the required net margin tier. This is a screening ceiling before unverified costs, **not a profit forecast**. The final independent economic review still requires all actual costs, repair evidence, inspected condition and calibrated sale outcomes. Damaged candidates additionally need repair-history resale evidence. Unknown costs are never zero.

`POST /identity/evaluations` accepts `training_vehicle_ids` and `records`. Each record requires `vehicle_id`, `listing`, and `reviewed_labels` containing `verified`, `verified_by`, exact `source/source_id/observed_at`, document `origin`, `evidence_url`, `checked_at`, and labelled identity `fields`. Duplicate/development vehicles, future labels and seller/photo hypotheses are rejected. It reports errors, abstentions and coverage separately; it does not authenticate documents or automatically release physical-identity/price predictions.

Development tests use explicitly synthetic independent challenge identities, not claimed verified production vehicles. The production report leaves physical identity accuracy unknown until an independently reviewed holdout is supplied. Missing paid-provider credentials cannot be replaced by a statistical confidence score.

## Opportunity-first AutoScout24 collection

`DEAL_FINDER_AUTOSCOUT24_PRICE_SCREENING_ENABLED=1` enables the deterministic
collection price agent on incremental public search pages. The existing broad
search configuration remains unchanged. At least three deduplicated, healthy,
compatible archived analogies are required; fuel, gearbox, version, seller type,
year and mileage define the comparison. The lowest observed or adjusted asking
price is the reference. Gross headroom must cover the purchase-price tier's
minimum net target and a 10% advertised discount before a full detail fetch is
prioritized. This is an apparent opportunity, never verified profit: the internal
15% adverse resale, reserve, complete costs, identity and inspection gates remain
mandatory. No provider/model spend is added by this collector.

Other search observations retain their basic prices and seller specifications for
market learning, with an explicit `not_apparent_opportunity` or
`needs_market_evidence` decision. Missing comparisons do not prove overpricing.
Known IDs are reconsidered after price/specification changes, on a seven-day
refresh, or when a deferred search-only observation can now qualify. Existing
original details remain immutable; unrefreshed detail facts are not presented as
fresh verified evidence. A cold archive retains search observations until comparisons exist. Compatible v2/v3
initial checkpoints resume without deleting or downloading the existing archive again;
when screening is enabled the remaining pages also prioritize opportunity details.

The worker resumes one incremental search page per iteration (at least ten
seconds between iterations and the existing request delay). Incomplete compatible
cycles resume across restarts and day boundaries. HTTP denials pause collection
with its checkpoint preserved. The daily cron can share the same guarded cycle;
completed cycles make no requests until the next day. Search pagination exhaustion
still does not establish complete national market coverage.

Source normalization now distinguishes fuel components from fuel categories,
accepts octane/blend labels, preserves semiautomatic gearboxes when seller text
uses the generic word automatic, and ignores fiscal horsepower. Seller claims of
good overall vehicle condition remain provisional and require inspection.
