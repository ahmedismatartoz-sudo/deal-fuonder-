# Free plate connector: TuttoTarghe candidate

Sources checked 2026-10-06:
- https://panel.tuttotarghe.it/login advertises 10 free API calls/day.
- https://panel.tuttotarghe.it/openapi.yaml documents direct bearer-token POST https://api.tuttotarghe.it/job/jobsync, one plate and type `details`.
- https://rapidapi.com/dynamic-solutions-dynamic-solutions-default/api/informazioni-targhe/pricing lists Basic $0/month, 10 Targa/day, with paid overages. This is NOT unlimited free.
- Registration/pricing links currently redirect to RapidAPI. Availability and free entitlement of the DIRECT bearer-token service must be verified in the user's account. A RapidAPI X-RapidAPI-Key is not a direct bearer token and must not be substituted.

Implemented authenticated POST `/vehicles/plate-lookup` with `{"plate":"AB123CD"}`. It reserves a call in the shared private archive before sending it, limits direct requests to nine in any rolling 24 hours, spaces calls by seven seconds and reuses immutable results for 30 days. Both failed and unresolved attempts count; automatic retries, polling, multiple job types, redirects and paid fallback are disabled. Restarting cannot erase the limit. Credentials must be exclusive to this software; external use of the same account is outside this budget and must be disallowed. The rolling cap is conservative but does not by itself prove zero billing: account entitlement is required.

Responses retain raw data, source, date, missing/conflicting fields. Only explicit aliases are normalized. The provider documentation leaves `details` as an unspecified object; the normalizer and response fixtures must be validated against one real response before automation is claimed. Missing generation/engine/gearbox stays missing. Plate data is not an independently verified identity attestation or exact parts fitment. No owner data is requested.

## Activation — not completed

No provider account was created, no subscription accepted, no token configured, no real plate submitted. The integration returns `configuration_required` until BOTH backend-only environment values exist:

- `DEAL_FINDER_TUTTOTARGHE_TOKEN`: direct provider bearer token (not public, not GitHub, not a chat message).
- `DEAL_FINDER_PLATE_FREE_PLAN_CONFIRMED=tuttotarghe-direct-10-per-day`: set only after confirming the direct `details` entitlement is free, credit top-ups/paid fallback disabled, and the credential is exclusive.

The user must log in/register at https://panel.tuttotarghe.it/login and obtain free-plan access. If it only offers RapidAPI access, pause: a different documented transport is needed and this direct connector must stay disabled. Do not guess the RapidAPI base URL or enable paid access.

After credentials: run one authorized plate lookup; verify real response schema, source provenance and account meter; attach normalized evidence to the vehicle with conflict checks. This endpoint archives lookup evidence, but automatically enriching all selected worker jobs is not yet wired. Ten/day is unsuitable for identifying 20,000 cars at once; use model data for market screening and reserve plate checks for finalists.

Tests use synthetic responses, with no live provider charges. CI additionally verifies persistent reservations/cache and private backend role permissions in disposable Postgres.
