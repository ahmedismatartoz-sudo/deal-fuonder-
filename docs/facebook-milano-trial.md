# Facebook Marketplace: public Milan trial

This is an experimental, bounded public-HTML probe. It is not a verified
Facebook crawler, a logged-in browser connector, or an official Meta API.
No scheduled Facebook collection is enabled.

Run explicitly in the backend (the existing production database environment
makes Archive use the shared Supabase PostgreSQL database):

```sh
python -m deal_finder.worker collect-facebook-trial --run-id milano-test-20261006 --limit 10
```

The trial reads the public Milan vehicles search and at most 50 linked detail
pages. robots.txt is respected; redirects, login, denials, unsupported markup
and missing location evidence stop the run with exit code 2. No cookies,
private APIs, CAPTCHA solving, paid providers, or proxies are used.

Only public JSON-LD Car/Vehicle objects with explicit city Milano/Milan and
country Italy are imported. The location search alone is insufficient: nearby
cities, non-vehicle objects and foreign listings are excluded. Missing location
stops the trial. The archive stores published title, description, photo URLs,
price if explicitly EUR, available specifications and structured vehicle data.
Seller/contact fields are excluded from the retained top-level object. Images
are URLs, not downloaded copies. Missing specifications stay unknown; a
published amount is not automatically asserted to be a total cash price.

Each successful detail page saves one checkpoint. Restarting the same run ID
and limit resumes the saved URL list rather than a changing search result.
`complete` means the bounded sample was exhausted, never complete market
coverage. No links or blocked search is not reported as a successful empty run.
Partial accepted pages survive a subsequent block. Collection can only enter
market screening once the normal archive quality requirements are met.

## Live trial, 2026-10-06

A public request to `/marketplace/milan/vehicles/` returned HTTP 302 pointing
to `/login/`. The actual collector command then stopped at robots.txt: `Facebook robots rules disallow collection`, exit code 2. No real Facebook listings were obtained or inserted. Parser
fixtures are synthetic and demonstrate archive behavior, not compatibility
with live Marketplace HTML. Real parser compatibility and autonomous access
remain unverified. Do not enable a recurring run without resolving access and
validating a real listing.
