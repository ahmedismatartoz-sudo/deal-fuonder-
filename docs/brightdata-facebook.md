# Bright Data Facebook → shared archive

Backend integration, disabled by default. No local browser, Facebook login or
cookie export is used. An API key and verified provider configuration are
still required. On 2026-10-06 a live 10-record generic trial and a 20-record
targeted Fiat 500 search completed and were saved in the private archive.
The first targeted search accepted 6/20 records under the validator deployed
at the time. This is a small connectivity/yield check, not market accuracy.

## Free tier

Official documentation checked 2026-10-06 describes 5,000 monthly credits,
shared with other Bright Data products. Without deposited funds the provider
hard-stops when the credits are exhausted. Do not add funds or auto-recharge.
The connector cannot attest the provider account balance or other clients'
usage. Backend confirmation is required before starting any collection.

Sources:
- https://docs.brightdata.com/general/account/billing-and-pricing/free-tier
- https://docs.brightdata.com/products/scrapers/facebook/send-first-request
- https://docs.brightdata.com/api-reference/rest-api/scraper/asynchronous-requests

## Configuration after account setup

Set `BRIGHTDATA_API_KEY` in the Render worker's secret environment, never in
GitHub/frontend/chat. Set `DEAL_FINDER_BRIGHTDATA_FREE_ACCOUNT_CONFIRMED` to
`unfunded-no-auto-recharge` only after checking the account is unfunded and
has no automatic recharge. The detailed Marketplace dataset ID is verified:
`gd_lvt9iwuh6fbcwmx1a`, with inputs `[{"url": ".../marketplace/item/ID/"}]`.

The user's provider UI verified Marketplace URL discovery with the same
dataset ID, discover_by=url and input fields url/country. A live URL discovery
request succeeded. Keyword discovery has not been verified and is not used
by this campaign. Future schema changes require verification before enabling.

Configuration fields:
- cycle_id: stable identifier, reused on every restart for this collection
- dataset_id: confirmed gd_* dataset identifier
- discover_by: null for item URL details; url/keyword for verified discovery
- input: 1–10 input objects copied from that scraper's provider example
- limit: 1–100 results total (start with 10)
- schema_verified: true only after checking the above

CLI: `python -m deal_finder.worker collect-brightdata --config CONFIG.json`.
It starts or checks a single async step, not a blocking poll loop. To complete
collection autonomously, set the same JSON in `DEAL_FINDER_BRIGHTDATA_CONFIG`
on the existing worker. The worker checks at most once per minute, imports
when ready, then stops this fixed cycle. Reusing the cycle ID never starts a
second job. This is an initial bounded trial, not an enabled daily schedule.

## Archive and failure behavior

Uses existing private Supabase collection_pages/listing_events/quarantine
through Archive; no new schema or public permissions. A trigger reservation
is saved before the provider POST. Returned snapshot IDs survive restarts.
If the POST's outcome is uncertain, it is not repeated: inspect Bright Data
and reconcile the snapshot before re-enabling a new cycle. Provider metadata
is stored under source=brightdata_control with zero listing records.

Only country_code=IT, a published municipality in an approved nearby province,
car breadcrumb or conservative
known model-family title with corroborating provider vehicle fields,
EUR numeric asking amount strictly below 20,000, matching item ID/URL and
is_sold=false are accepted. A provider search cap must also be set in the
verified discovery input: post-filtering cannot prevent credits consumed by
out-of-scope results. The current guard prevents those results entering the
usable catalogue, retaining raw evidence in private quarantine.
Other rows and provider errors go to private quarantine for later enrichment.
No inference from search location. Descriptions,
all supplied photo URLs and original output are retained. Images are links,
not downloaded copies. Model family and model year copied from the published
title remain coarse evidence, never an exact variant or registration year.
car_miles stays unit-unknown unless explicit published km evidence agrees.
Trim, condition verification and total cash price are not invented.
Asking amount stays price_kind=unknown, so these
records are archived but not asserted ready for market valuation.

Synthetic tests cover provider contract and persistence, not real Marketplace
accuracy or discovery compatibility. Full catalogue coverage is never claimed.

### Expanded nearby geography, 6 October 2026

The user enabled MI, MB, BG, BS, CO, LC, VA, PV, LO, CR and NO: Milano,
Monza e della Brianza, Bergamo, Brescia, Como, Lecco, Varese, Pavia, Lodi,
Cremona and Novara. Every one of their 1,447 current municipalities is matched
against the bundled ISTAT register (reference 21 February 2026), including
names with accents. Published conflicting provinces/regions and nationally
ambiguous names without a province remain excluded. The actual city/province
is retained, never rewritten to Milano/MI; comparisons still use the target's
local province. All price, availability, identity and professional margin
checks remain in effect.

Source: https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/
Dataset: https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.xlsx
The bundled JSON includes provenance, download date and source SHA256.

The worker revalidates one archived Facebook snapshot per iteration under
`brightdata-revalidate-v3-SNAPSHOT`. This uses only existing original rows,
preserves observation timestamps and the old quarantine, and sends no provider
requests. Completed replay markers survive restarts. Old in-flight v2 control
scopes resume their identical request without repeating a trigger or altering
its budget. The existing campaign search URLs and credit ceiling are retained;
the broader acceptance applies to their returned data and future snapshots.

## Market sample coverage

Use separate reviewed search batches across price ranges [500,5000),
[5000,10000), [10000,15000), [15000,20000), with explicit EUR and Milano.
Within these distribute across mileage ranges 0–50,000, 50,001–100,000,
100,001–150,000 and above 150,000 km, and different brands, models and years.
Only apply provider kilometre filters after confirming their actual units;
unknown mileage must remain unknown and its coverage must be reported.
Do not claim representativeness or mark a batch complete because it reached
a record quota: report unique vehicles per price/make/model/mileage group and
identify missing groups. A 10-record connectivity trial checks the pipeline;
it is not a sufficiently distributed market-price training set. Targeted URL
searches work in the live pilot, but geographic radius is not an enforced
guarantee: published listing location remains the acceptance gate.

## Autonomous initial campaign

Opt in with `DEAL_FINDER_BRIGHTDATA_CAMPAIGN` on the existing worker:

```json
{"campaign_id":"milano-20261006-base-5k-v1","credit_ceiling":5000,"batch_limit":40,"plan_version":"milano-diverse-v1"}
```

This takes precedence over the fixed trial config. Reuse the campaign ID on
every restart. The durable cursor, allocations, provider IDs, counts and stop
reasons live in the existing private archive; no local/browser session is
needed and no daily repeating schedule is enabled.

The plan rotates 21 brands, known model families and four asking-price bands
below EUR 20,000. About 80% of searches collect comparables; 20% add urgency,
private-sale or repair keywords. These keywords are leads, not verified facts.
Provider price/radius filters are hints checked against actual returned data.
Do not buy an advertised opportunity solely because a search term matched.

The monthly connector ledger counts all returned rows, including quarantined
or repeated ads, and holds the full request cap for unresolved triggers. Known
completed short batches release unused cap. Trials and earlier connector
campaigns count toward the same 5,000 ceiling. Unknown prior trigger outcomes
remain conservatively reserved. This cannot see other products' credit usage;
the confirmed unfunded account's external hard stop is still required.

Only one async collection is in flight. Each search URL is scheduled once.
After five nonempty batches produce no new valid car IDs, pause rather than
spend the remaining credits on duplicates/out-of-scope results. Also stop at
the ceiling, plan exhaustion, account/API failure or an uncertain trigger.
Temporary failures of progress/download GETs retry at most three times, with
the retry counter saved across restarts; trigger POSTs never retry. A download
HTTP 202 returns to polling rather than treating an unfinished snapshot as a
terminal failure. Completed snapshots are persisted before advancing; restarts do not repost
reserved triggers. Creating another campaign ID does not reset the ledger.

Report provider rows and accepted unique cars separately. 5,000 credits is
not a promise of 5,000 unique Milano cars or any financial return. Preserve
all original records, descriptions and photo URLs, including excluded records
in private quarantine. Market/repair agents must resolve variant, mileage,
asking-price ambiguity, repair parts, resale evidence and selling costs before
publishing a margin. Photos remain links, not durable downloaded image files.
