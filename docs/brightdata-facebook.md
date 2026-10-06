# Bright Data Facebook → shared archive

Backend integration, disabled by default. No local browser, Facebook login or
cookie export is used. An API key and verified provider configuration are
still required; no live Facebook collection has been started.

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

For discovery, copy the dataset ID, discover_by and input fields from the
provider's Facebook Marketplace discovery API example. The public JSON input
catalogue could not be retrieved in this workspace, so no discovery ID or
input schema has been guessed. Confirm the actual schema with an account
before setting `schema_verified=true`. The importer uses the documented
Marketplace output fields; alternative discovery output needs verification.

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

Only country_code=IT, explicit Milano/Milan city, verified car breadcrumb,
EUR numeric price, matching item ID/URL and explicit is_sold are accepted.
Other rows and provider errors go to private quarantine for later enrichment.
No inference from search location. Nearby cities are excluded. Descriptions,
all supplied photo URLs and original output are retained. Images are links,
not downloaded copies. car_miles units, model, year, trim, condition and total
cash price are not invented. Asking amount stays price_kind=unknown, so these
records are archived but not asserted ready for market valuation.

Synthetic tests cover provider contract and persistence, not real Marketplace
accuracy or discovery compatibility. Full catalogue coverage is never claimed.
