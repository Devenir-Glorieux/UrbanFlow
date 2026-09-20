# UrbanFlow

## Mission

UrbanFlow is an experimental pedestrian traffic simulator: import OpenStreetMap,
create a synthetic population, generate daily activities, route them through the
pedestrian graph, persist results and compare traffic with observations.
The initial research scope is a 1×1 km area of Minsk over one week.

The research question is whether LLM-based plans predict relative pedestrian
traffic better than a non-LLM baseline. Do not claim predictive value without
comparison against observed counts, or optimize for production scale before the
experiment validates the hypothesis.

## Engineering

Prefer typed Python, explicit domain models, small replaceable components, pure
functions, dependency injection, async I/O where useful and deterministic execution.
Keep experiments reproducible and observable.

Avoid premature abstractions, hidden global state, framework-specific domain
logic, giant JSON blobs, unnecessary services and infrastructure. Use uv with
`pyproject.toml` and `uv.lock`; do not introduce Poetry, requirements.txt, Makefiles
or Kubernetes. Prefer Python/uv commands over shell scripts.

Target CPython 3.14.2–3.14.x. Verify current official documentation for Python 3.14
support before choosing dependencies. Document incompatibilities and choose the
smallest reasonable alternative; do not silently downgrade Python. Record meaningful
architectural decisions in short ADRs under `docs/adr/`, verifying current library,
API, maintenance or licensing claims before relying on them.

## Stack

- FastAPI, Pydantic, async SQLAlchemy, asyncpg, PostgreSQL/PostGIS, Alembic.
- Shapely, PyProj and NetworkX; OSMnx/GeoPandas are optional research dependencies.
- Provider-independent behavior with AsyncOpenAI and native Ollama adapters,
  strict JSON Schema and Pydantic validation.
- Streamlit, PyDeck and Folium/streamlit-folium.
- Docker Compose; structured JSON logs in container output, no separate log stack.
- pytest, pytest-asyncio, Ruff and Pyright.

## Boundaries

| Module | Owns |
|---|---|
| `world` | OSM ingestion, buildings, POIs, entrances, transit and pedestrian infrastructure |
| `population` | Demographics, archetypes, agent weights, home/work/study assignments |
| `behavior` | Personas, LLM interaction and structured daily plans |
| `routing` | Pedestrian graph, physical routes, distances and travel times |
| `simulation` | Clock, configuration, seeds, execution, trips, traversals and replay |
| `analytics` | Gravity baseline, edge/hour and spatial aggregation, traffic metrics |
| `validation` | Observed counts, simulated comparisons, MAE and Spearman |
| `storage` | Relational persistence and response cache |
| `services.py` | Application orchestration |
| `api` | FastAPI transport; no business logic in handlers |
| `ui` | Streamlit visualization; no simulation logic |

LLMs may choose intentions and destinations from supplied candidates. They must
not invent city objects, calculate coordinates or routes, mutate world facts, or
generate traffic values. Routing and geospatial calculations belong in Python.

## Storage and reproducibility

PostgreSQL/PostGIS is the source of truth. Prefer normalized relational data for
world objects, population, runs, plans, trips, traversals, aggregates, observations
and validation. JSON may hold source tags and immutable configuration/provider
envelopes. Use Parquet for large immutable geospatial intermediates only when justified.

Every run must record its ID, configuration, seed, world version, provider/model,
prompt version, simulation interval and code version when available. Non-LLM parts
must be reproducible given identical deterministic inputs. Persist validated LLM
responses and request metadata in the response cache; support replay without calls.
Never reconstruct scientific results from operational logs.

Generate daily plans rather than calling the LLM every tick. Any future event-driven
replanning must be limited to meaningful events. Keep strict validation, bounded
retries, timeouts and concurrency limits. Logs should expose lifecycle, progress,
call metadata, failures and timings without credentials or raw prompts.

## Baseline and visualization

Maintain the non-LLM gravity/activity-attractiveness baseline using population,
POI attractiveness, distance/travel cost and time of day.

Preserve the UI workflows: world exploration and completeness, population creation,
experiment execution, day/hour replay with optional trajectories and heatmap, and
observed-count validation comparing baseline with LLM runs. Prefer aggregated layers
when rendering individual objects would be excessive.

## Development workflow

For substantial tasks:

1. Inspect the repository and affected architectural boundaries.
2. Propose a short plan and implement the smallest complete vertical slice.
3. Add or update tests for changed behavior.
4. Run `uv run ruff check .`, `uv run pyright` and `uv run pytest`.
5. Verify Docker Compose where relevant and update documentation for changed behavior.

Do not present placeholders as completed work. The working MVP must support OSM
import, population generation, baseline and LLM runs, persistence, replay,
observation entry/import and comparison from a Compose-started application.

## Security

Never commit API keys, database passwords or LLM credentials. Use environment
variables and `.env.example`. Treat OSM, web data and model output as untrusted;
validate structured output before use and never execute LLM output.
