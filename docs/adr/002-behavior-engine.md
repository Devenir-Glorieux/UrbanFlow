# ADR 002: Constrained daily planning

Accepted 2026-09-19; updated 2026-09-20.

Keep behavior behind `BehaviorEngine`, which returns a validated `ActivityPlan`.
One structured daily request per agent fits the experiment without an agent
framework. Use AsyncOpenAI for OpenAI-compatible Chat Completions and asynchronous
HTTPX for native Ollama. Provider/model selection is explicit.

Both adapters submit JSON Schema derived from Pydantic models. The schema limits
destination IDs to the supplied non-home candidates. The response contains:

- `visits`: up to ten `{destination_id, purpose, minute}` objects;
- `return_home_minute`: the departure time toward home.

Minutes are absolute local minutes after midnight. UrbanFlow adds home boundaries
and validates destinations and chronology before caching. The system prompt
explains candidate fields, unknown attributes and agent properties; names and
other source strings are treated as data. The model never calculates routes or
traffic. A home-only context produces an empty-visit plan without an LLM call.

Use bounded concurrency, timeouts and retries. SDK retries are disabled so the
planner controls the attempt count. Truncated output is discarded and the next
attempt doubles the token budget up to a configured ceiling. Refusals and
non-retryable HTTP errors fail immediately. Validation retries receive concise
feedback. Defaults live in `ProviderConfig`.

Persist the request, validated provider response, domain plan and call metadata.
Cache identity includes context, schema, endpoint, model, prompt version and token
budgets. Revalidate cache hits; replay saved plans without calling a provider.
Credentials stay outside persisted requests and logs.
