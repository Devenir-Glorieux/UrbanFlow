"""Real PostGIS tests. Provider HTTP responses are explicitly mocked, never scientific data."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from urbanflow.analytics.baseline import GravityBaseline
from urbanflow.api.app import create_app
from urbanflow.behavior import PlanningContext
from urbanflow.behavior.providers import ProviderConfig, StructuredProvider
from urbanflow.common import DomainError
from urbanflow.services import UrbanFlow
from urbanflow.settings import Settings
from urbanflow.simulation import SimulationConfig
from urbanflow.storage import schema as s
from urbanflow.storage.repository import DatabasePlanCache, Repository
from urbanflow.validation import Observation

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostGIS test database")
    if not url.endswith("/urbanflow_test"):
        pytest.fail("Integration tests require a dedicated database named urbanflow_test")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": url},
        check=True,
    )
    return url


@pytest.fixture
async def service(database_url):
    engine = create_async_engine(database_url)
    settings = Settings(
        database_url=database_url,
        overpass_url="http://unused",
        openai_base_url="http://unused/v1",
        llm_api_key="",
        ollama_base_url="http://unused",
    )
    yield UrbanFlow(Repository(engine), settings)
    await engine.dispose()


@pytest.mark.parametrize("protocol", ["openai", "ollama"])
async def test_full_persisted_experiment_cache_replay_and_validation(
    service, raw, bbox, population_config, protocol
):
    raw["test_snapshot_id"] = str(uuid4())
    world = await service.ingest(bbox, "SYNTHETIC integration", raw, "SYNTHETIC test")
    loaded = await service.repo.load_world(world.id)
    assert loaded.id == world.id and len(loaded.edges) == 40
    assert loaded.objects == sorted(world.objects, key=lambda o: o.id)
    # Reimport is idempotent, even with concurrent attempts.
    await asyncio.gather(*(service.repo.save_world(world, raw) for _ in range(2)))
    population = await service.create_population(
        world.id, population_config.model_copy(update={"size": 5})
    )
    assert population["eligible_homes"] == 5
    assert population["occupied_homes"] == 5
    assert population["max_agents_in_home"] == 1
    config = SimulationConfig(population_id=population["id"], days=2)
    baseline = await service.run(config)
    calls = 0

    async def responder(request):
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        context = PlanningContext.model_validate_json(payload["messages"][1]["content"])
        plan = await GravityBaseline().plan(context)
        provider_plan = {
            "visits": [activity.model_dump() for activity in plan.activities[1:-1]],
            "return_home_minute": plan.activities[-1].minute,
        }
        message = {"role": "assistant", "content": json.dumps(provider_plan)}
        return httpx.Response(
            200,
            json=(
                {"id": "mock", "choices": [{"finish_reason": "stop", "message": message}]}
                if protocol == "openai"
                else {"message": message}
            ),
        )

    provider_config = ProviderConfig(provider=protocol, model="MOCK-CONTRACT-TEST")
    llm_config = config.model_copy(update={"mode": "llm", "provider": provider_config})
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        behavior = StructuredProvider(
            provider_config, "http://mock", "", client, DatabasePlanCache(service.repo.engine)
        )
        llm = await service.run(llm_config, behavior=behavior)
        assert calls == 10
        rerun = await service.run(llm_config, behavior=behavior)
        assert calls == 10  # Persisted cache avoids every HTTP call.
    # Older runs predate the token settings; schema defaults must not prevent replay.
    old_config = llm_config.model_dump(mode="json")
    del old_config["provider"]["max_completion_tokens"]
    del old_config["provider"]["max_completion_tokens_limit"]
    async with service.repo.engine.begin() as conn:
        await conn.execute(update(s.run).where(s.run.c.id == llm["id"]).values(config=old_config))
    replay = await service.replay(llm["id"])  # The closed client cannot possibly be contacted.
    async with service.repo.engine.connect() as conn:

        async def traffic(run_id):
            rows = await conn.execute(
                select(s.traffic.c.edge_id, s.traffic.c.hour, s.traffic.c.weighted_count)
                .where(s.traffic.c.run_id == run_id)
                .order_by(s.traffic.c.edge_id, s.traffic.c.hour)
            )
            return list(rows)

        expected = await traffic(baseline["id"])
        assert expected
        assert expected == await traffic(llm["id"]) == await traffic(rerun["id"])
        assert expected == await traffic(replay["id"])
        samples = (
            (
                await conn.execute(
                    select(s.traversal)
                    .where(s.traversal.c.run_id == baseline["id"])
                    .order_by(s.traversal.c.started_at)
                    .limit(3)
                )
            )
            .mappings()
            .all()
        )
        assert (await conn.execute(select(func.count()).select_from(s.cache))).scalar_one() >= 10
    observations = [
        Observation(
            world_id=world.id,
            edge_id=row["edge_id"],
            started_at=row["started_at"],
            ended_at=row["started_at"] + timedelta(seconds=1),
            count=round(row["weight"]),
            source="SYNTHETIC test assertion, not a field observation",
        )
        for row in samples
    ]
    await service.import_observations(observations)
    result = await service.compare(baseline["id"], llm["id"])
    assert result["baseline"] == result["llm"]
    assert result["baseline"]["mae"] < 0.001  # Integer observations round fractional weights.
    assert result["baseline"]["spearman"] is None  # constant observations are undefined
    app = create_app(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get(f"/worlds/{world.id}/geojson")).json()["features"]
        housing = (await client.get(f"/worlds/{world.id}/housing")).json()
        assert housing["residential_buildings"] == housing["minimum_agents"] == 5
        invalid = await client.get(f"/runs/{llm['id']}/traffic?day=2026-09-14&hour=24")
        assert invalid.status_code == 422
        response = await client.get(f"/runs/{llm['id']}/traffic?day=2026-09-14&hour=8")
        assert response.status_code == 200 and response.json()["features"]
        trajectories = await client.get(f"/runs/{llm['id']}/trajectories?day=2026-09-14&hour=8")
        assert trajectories.status_code == 200
        assert (await client.get("/validation")).json()


async def test_failure_status_and_atomic_observation_import(service, raw, bbox, population_config):
    raw["test_snapshot_id"] = str(uuid4())
    world = await service.ingest(bbox, "test", raw, "SYNTHETIC test")
    population = await service.create_population(
        world.id, population_config.model_copy(update={"size": 5})
    )

    class FailingBehavior:
        async def plan(self, context):
            raise DomainError("Deliberate provider failure")

    with pytest.raises(DomainError, match="Deliberate"):
        await service.run(
            SimulationConfig(population_id=population["id"], days=1), behavior=FailingBehavior()
        )
    async with service.repo.engine.connect() as conn:
        failed = (
            (await conn.execute(select(s.run).where(s.run.c.world_id == world.id))).mappings().one()
        )
        assert failed["status"] == "failed"
        assert (
            await conn.execute(
                select(func.count()).select_from(s.plan).where(s.plan.c.run_id == failed["id"])
            )
        ).scalar_one() == 0
    valid = Observation(
        world_id=world.id,
        edge_id=world.edges[0].id,
        started_at=failed["started_at"],
        ended_at=failed["ended_at"],
        count=2,
        source="SYNTHETIC test",
    )
    with pytest.raises(DomainError, match="unknown edge"):
        await service.import_observations([valid, valid.model_copy(update={"edge_id": "missing"})])
    assert await service.observations(world.id) == []
