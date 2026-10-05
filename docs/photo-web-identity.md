# Photo + advertisement + web identity subagent

The market coordinator now registers `photo_web_identity`. It combines up to eight original photo URLs (front, rear, body, interior, badges/document clues) with title, description and declared technical fields. Input truncation and photograph count are explicit. The adapter sends image inputs to a vision model and requests web search for manufacturer brochures, specifications and generation/facelift references.

Implemented:
- authenticated `POST /vehicles/identity-plan` for a research plan, including incomplete listings;
- authenticated `POST /vehicles/identify` for vision+web research when configured;
- automatic research during existing queued workflow for price-selected candidates only;
- results persisted with existing `agent_runs` outputs;
- per-field claims/source URLs/photo indexes, hypotheses, missing evidence and contradictions;
- conflicts redirect the candidate to enrichment and block parts handoff;
- no inferred specifications overwrite immutable source originals or verified identity attestations.

Visual evidence can support make/model/generation. Appearance cannot uniquely identify engine code, exact trim, fuel, transmission or year; such visual-only claims are rejected. Seller declarations remain unverified. Web brochures describe available versions, not which version this individual car is. Multiple conflicting values are never silently resolved. Output remains provisional and does not enable exact part fitment or verified publication. No calibration or 100% identification accuracy is claimed. Up to three web tool calls and 4,000 output tokens are requested per analysis; this is a request-level bound, not a daily budget.

Activation requires backend `OPENAI_API_KEY`, explicitly chosen `DEAL_FINDER_VISION_MODEL` supporting image+web search+structured outputs, and `DEAL_FINDER_PHOTO_IDENTITY_ENABLED=1`. No credentials/model/billing were configured in this change; the production adapter reports configuration_required. The API vision/search provider is usage-billed and is not a completely free plate service. Actual model compatibility, responses and identification accuracy require live testing after account configuration. Static unit fixtures and mocked HTTP tests do not substitute for that test.

The current archive scanner queues price-selected normalized candidates; incomplete observations stay in the archive. The new direct endpoints can research incomplete listings, but scheduling automatic enrichment for those archived records is a separate task. Selected job outputs retain evidence automatically; direct `/vehicles/identify` results are returned to the authenticated caller and are not automatically written as standalone archive records.

Official implementation references checked 2026-10-06:
- https://developers.openai.com/api/docs/guides/images-vision
- https://developers.openai.com/api/docs/guides/tools-web-search
- https://developers.openai.com/api/docs/guides/structured-outputs
