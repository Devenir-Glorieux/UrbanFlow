# Verification

## Local checks

```console
uv sync
uv run ruff check .
uv run pyright
uv run pytest
```

Without `TEST_DATABASE_URL`, PostGIS integration tests are skipped. To run them
in Docker, create the dedicated test database once:

```console
docker compose exec db createdb -U urbanflow urbanflow_test
```

Then build and run the test container:

```console
docker compose build api
docker compose -f compose.yaml -f compose.test.yaml build checks
docker compose -f compose.yaml -f compose.test.yaml run --rm checks
```

For a host run against the Compose database:

```console
TEST_DATABASE_URL=postgresql+asyncpg://urbanflow@localhost:5432/urbanflow_test uv run pytest
```

Use the configured host port if it differs from 5432. Integration tests require the
name `urbanflow_test`, apply migrations and create their own test snapshots.

## Coverage

Tests cover OSM transformation, population assignment, routing, aggregation,
provider schemas/retries/timeouts/concurrency, cache reuse, replay, validation
and Streamlit rendering. Integration tests use real PostGIS with mocked LLM HTTP
responses for both adapters. UI tests do not verify browser/WebGL rendering.

## Last recorded checks — 2026-09-20

- Ruff and Pyright passed on CPython 3.14.2.
- Full host suite with the dedicated PostGIS database: 70 passed.
- Rebuilt Compose stack: API, UI and database health checks passed.
- Live OpenRouter check with `openai/gpt-4.1-mini` and prompt `daily-plan-v4`:
  one agent-day, 413 candidates, valid plan on the first attempt; routing and
  subsequent cache reuse passed.

The live check covers one daily plan. It does not establish full-week reliability
or pedestrian prediction accuracy. Native Ollama has transport-test coverage;
a live Ollama check is not recorded here.
